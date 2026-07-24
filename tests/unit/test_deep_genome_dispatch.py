# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for DeepGenome analyst result dispatch helpers.

Covers obsfs result reuse, SDK download fallback, dispatch context handling,
and the small harness used to exercise private download helpers.
"""

from __future__ import annotations

from collections.abc import Awaitable, Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from tests.support.logging_helpers import capture_non_propagating_logger

from mcp_server_phytomni.agents.deep_genome import (
    dispatch as deep_genome_dispatch,
)
from mcp_server_phytomni.agents.deep_genome.coordinator import (
    RemoteSubmission,
    WorkItemOutcome,
    WorkItemPollRequest,
)
from mcp_server_phytomni.agents.deep_genome.dispatch import (
    GENERIC_ANALYSIS_NODE_TYPES,
    AnalysisDispatchContext,
    DeepGenomeDispatchMixin,
    _analyst_node_name,
)
from mcp_server_phytomni.agents.deep_genome.remote_io import (
    DeepGenomeRemoteIO,
    RemoteIOHooks,
)
from mcp_server_phytomni.storage.path_policy import IdFactory, RunIdentity

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _attach_remote_io_log_handler(
    caplog: pytest.LogCaptureFixture,
) -> Iterator[None]:
    """Attach pytest capture to the remote-I/O logger."""
    with capture_non_propagating_logger(
        "mcp_server_phytomni.agents.deep_genome.remote_io",
        caplog.handler,
    ):
        yield


def _fixed_run_identity() -> RunIdentity:
    """Return a deterministic RunIdentity for local fallback paths."""
    factory = IdFactory(
        now=lambda: datetime(2026, 5, 7, 1, 2, 3, tzinfo=UTC),
        token_factory=lambda _: "abcdef01",
    )
    return RunIdentity.create("alice", "deep-genome-test", factory)


_FIXED_RUN_ID = "20260507T010203Z-deep-genome-test-alice-abcdef01"


class FakeSensitiveConfig:
    """Minimal sensitive config for OBS credential access."""

    def obs_credentials(self) -> tuple[str, str]:
        """Return fake OBS credentials.

        Returns:
            Access key id and secret access key pair.
        """
        return "access-key", "secret-key"

    def is_test_config(self) -> bool:
        """Return whether this is a fake test config.

        Returns:
            True because this config is only used in tests.
        """
        return True


class DispatchHarness(DeepGenomeDispatchMixin):
    """Small concrete harness for private dispatch helper tests.

    Attributes:
        deep_genome_config: Minimal config namespace used by dispatch helpers.
        sensitive_config: Fake sensitive config with OBS credentials.
    """

    def __init__(self, deepgenome_out: str):
        """Initialize fake DeepGenome config."""
        self.deep_genome_config = SimpleNamespace(
            BUCKET_NAME="phytomni",
            DEEPGENOME_OUT=deepgenome_out,
            OBS_SERVER="https://example.invalid",
        )
        self.sensitive_config = FakeSensitiveConfig()


async def test_dispatch_polls_normalized_submit_ack_before_download(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """The coordinator polls the effective id before resolving results."""
    harness = DispatchHarness(str(tmp_path / "deep-out"))
    setattr(harness.deep_genome_config, "USER_ID", "alice")
    setattr(harness.deep_genome_config, "TIMEOUT", 4.0)
    setattr(harness.deep_genome_config, "POLL_INTERVAL", 2.0)
    setattr(harness.deep_genome_config, "MAX_POLL", 10.0)
    submission = RemoteSubmission("caller-1", "remote-1", "/obs/out")
    submit = AsyncMock(return_value=submission)
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "result.summary").write_text(
        "# usable result\n",
        encoding="utf-8",
    )
    download = AsyncMock(return_value=str(results_dir))
    monkeypatch.setattr(harness, "_submit_analysis_task", submit)
    monkeypatch.setattr(harness, "_download_analysis_result", download)

    status = AsyncMock(return_value={"status": "SUCCEEDED"})
    monkeypatch.setattr(deep_genome_dispatch, "task_status", status)

    async def poll(
        request: WorkItemPollRequest,
    ) -> WorkItemOutcome:
        """Drive the injected seams once and return their settled result."""
        received = request.submission
        assert received.poll_task_id == "remote-1"
        assert request.options.request_timeout == 4.0
        assert request.options.poll_interval == 2.0
        assert request.options.deadline_seconds == 10.0
        assert await request.callbacks.status_reader(
            received.poll_task_id, 4.0
        ) == {"status": "SUCCEEDED"}
        summary = await cast(
            Awaitable[str | None],
            request.callbacks.result_resolver(received),
        )
        return await cast(
            Awaitable[WorkItemOutcome],
            request.callbacks.transition_sink("succeeded", summary, None),
        )

    monkeypatch.setattr(deep_genome_dispatch, "poll_work_item", poll)

    def fail_guard(_result: Any) -> None:
        """Fail if a submit-only acknowledgement is treated as terminal."""
        raise AssertionError("submit acknowledgement reached failure guard")

    monkeypatch.setattr(harness, "_raise_if_agent_failed", fail_guard)

    dispatch_and_wait = getattr(harness, "_dispatch_and_wait_analysis")
    result = await dispatch_and_wait(
        "haplotypes_analysis",
        "ath",
        "GeneA",
    )

    assert result == {
        "task_id": "caller-1",
        "output_path": "/obs/out",
        "results_dir": str(results_dir),
        "status": "completed",
    }
    submit.assert_awaited_once()
    download.assert_awaited_once()
    status.assert_awaited_once()


async def test_download_analysis_result_uses_readable_obsfs_dir(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify readable obsfs result directories are used in place.

    Args:
        tmp_path: Temporary directory used as fake obsfs and output root.
        monkeypatch: Pytest monkeypatch fixture used to replace I/O helpers.
    """
    harness = DispatchHarness(str(tmp_path / "local-out"))
    result_dir = tmp_path / "obsfs-result"
    result_dir.mkdir()

    def fail_download(*args: Any, **kwargs: Any):
        """Fail if SDK download fallback is called.

        Args:
            *args: Ignored fallback positional arguments.
            **kwargs: Ignored fallback keyword arguments.
        """
        del args, kwargs
        raise AssertionError("download_obs_out should not be called")

    monkeypatch.setattr(
        deep_genome_dispatch,
        "obsfs_path_for",
        lambda *args, **kwargs: result_dir,
    )
    monkeypatch.setattr(
        deep_genome_dispatch,
        "download_obs_out",
        fail_download,
    )

    context = AnalysisDispatchContext(
        analysis_type="gene_expression_tissues",
        species_code="ath",
        gene_id="GeneA",
        output_dir="/obs/phytomni/results/GeneA",
    )

    download_analysis_result = getattr(harness, "_download_analysis_result")

    assert await download_analysis_result(
        context,
        "/obs/phytomni/results/GeneA",
        _fixed_run_identity(),
    ) == str(result_dir)


async def test_download_analysis_result_falls_back_to_sdk_download(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify unavailable obsfs directories trigger SDK result download.

    Args:
        tmp_path: Temporary directory used as local output root.
        monkeypatch: Pytest monkeypatch fixture used to replace download.
    """
    captured: dict[str, Any] = {}
    harness = DispatchHarness(str(tmp_path / "deep-out"))

    def fake_download_obs_out(*args: Any, **kwargs: Any):
        """Capture SDK download fallback arguments.

        Args:
            *args: Positional download arguments.
            **kwargs: Keyword download arguments.
        """
        captured["args"] = args
        captured["kwargs"] = kwargs
        yield "keep.txt download succeed."

    monkeypatch.setattr(
        deep_genome_dispatch,
        "download_obs_out",
        fake_download_obs_out,
    )

    context = AnalysisDispatchContext(
        analysis_type="gene_expression_tissues",
        species_code="ath",
        gene_id="GeneA",
        output_dir="/obs/phytomni/results/GeneA",
    )

    download_analysis_result = getattr(harness, "_download_analysis_result")

    result = await download_analysis_result(
        context,
        "/obs/phytomni/results/GeneA",
        _fixed_run_identity(),
    )

    scratch_root = (
        tmp_path / "deep-out" / _FIXED_RUN_ID / "gene_expression_tissues"
    )
    assert result == str(scratch_root / "GeneA")
    assert captured["kwargs"]["obs_output_path"] == "results/GeneA"
    assert captured["kwargs"]["download_path"] == str(scratch_root)
    assert captured["kwargs"]["bucket_name"] == "phytomni"
    assert captured["kwargs"]["target_file_feature"] == [
        ".png",
        ".summary",
        ".legend",
    ]


async def test_download_analysis_result_relay_mode_streams_via_relay(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Relay mode downloads results through the relay, not the OBS SDK."""
    harness = DispatchHarness(str(tmp_path / "deep-out"))
    monkeypatch.setattr(
        deep_genome_dispatch, "relay_mode_enabled", lambda: True
    )
    relay_dl = AsyncMock(return_value=["GeneA.png download succeed."])
    monkeypatch.setattr(
        deep_genome_dispatch, "download_obs_out_via_relay", relay_dl
    )

    def fail_sdk(*args: Any, **kwargs: Any):
        del args, kwargs
        raise AssertionError("SDK download_obs_out must not run in relay mode")

    monkeypatch.setattr(deep_genome_dispatch, "download_obs_out", fail_sdk)

    context = AnalysisDispatchContext(
        analysis_type="gene_expression_tissues",
        species_code="ath",
        gene_id="GeneA",
        output_dir="/obs/phytomni/results/GeneA",
    )
    download_analysis_result = getattr(harness, "_download_analysis_result")

    result = await download_analysis_result(
        context,
        "/obs/phytomni/results/GeneA",
        _fixed_run_identity(),
    )

    scratch_root = (
        tmp_path / "deep-out" / _FIXED_RUN_ID / "gene_expression_tissues"
    )
    assert result == str(scratch_root / "GeneA")
    assert relay_dl.await_count == 1
    call = relay_dl.await_args
    assert call is not None
    assert call.kwargs["obs_output_path"] == "results/GeneA"
    assert call.kwargs["download_path"] == str(scratch_root)


async def test_download_analysis_result_relay_empty_raises(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Relay mode fails loud when the result set is empty (no silent dir)."""
    harness = DispatchHarness(str(tmp_path / "deep-out"))
    monkeypatch.setattr(
        deep_genome_dispatch, "relay_mode_enabled", lambda: True
    )
    monkeypatch.setattr(
        deep_genome_dispatch,
        "download_obs_out_via_relay",
        AsyncMock(return_value=[]),
    )

    context = AnalysisDispatchContext(
        analysis_type="gene_expression_tissues",
        species_code="ath",
        gene_id="GeneA",
        output_dir="/obs/phytomni/results/GeneA",
    )
    download_analysis_result = getattr(harness, "_download_analysis_result")

    with pytest.raises(RuntimeError, match="no analysis results"):
        await download_analysis_result(
            context,
            "/obs/phytomni/results/GeneA",
            _fixed_run_identity(),
        )


async def test_bi_json_runs_gauss_query(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify _bi_json runs the SQL directly via gauss_query.

    The direct (non-relay) branch of ``_bi_json`` now runs the SQL
    straight against GaussDB through the shared ``gauss_query`` seam
    instead of POSTing to a BI HTTP endpoint. This pins that the SQL
    reaches ``gauss_query`` and its decoded payload flows back to the
    caller.

    Args:
        tmp_path: Unused; reserved for harness symmetry.
        monkeypatch: Pytest monkeypatch fixture used to swap the
            shared ``gauss_query`` seam with a recording stub.
    """
    del tmp_path
    harness = DispatchHarness("/tmp/deep-out")
    recorded: dict[str, Any] = {}

    async def fake_gauss_query(sql: str) -> dict[str, Any]:
        """Record the SQL and return the canned BI payload."""
        recorded["sql"] = sql
        return {"message": "ok", "data": [{"x": 1}]}

    monkeypatch.setattr(
        deep_genome_dispatch, "relay_mode_enabled", lambda: False
    )
    monkeypatch.setattr(deep_genome_dispatch, "gauss_query", fake_gauss_query)
    bi_json = getattr(harness, "_bi_json")

    payload = await bi_json("SELECT 1")

    assert payload == {"message": "ok", "data": [{"x": 1}]}
    assert recorded["sql"] == "SELECT 1"


async def test_prepare_analysis_tasks_escapes_gene_id_and_builds_tasks() -> (
    None
):
    """The osa gene-id lookup escapes the id and threads the v2 id.

    Pins the SQL-injection fix: a gene id carrying a single quote must
    reach the BI query quote-doubled (via ``sql_literal``), never breaking
    out of its literal, and the osa branch must resolve the v2 id into the
    expression tasks while emitting all eleven analysis tasks.
    """
    harness = DispatchHarness("/tmp/deep-out")
    recorded: dict[str, Any] = {}

    async def _fake_bi_json(sql: str) -> dict[str, Any]:
        """Record the SQL and return a canned osa id-table row."""
        recorded["sql"] = sql
        return {"data": [{"msu_gene_id": "LOC_Os01g012345"}]}

    setattr(harness, "_bi_json", _fake_bi_json)
    prepare = getattr(harness, "_prepare_analysis_tasks")
    state = {
        "gene_id": "Os01'; DROP TABLE id_table; --",
        "species_code": "osa",
    }

    result = await prepare(state)

    # The gene id reaches the query quote-doubled, never as a raw breakout.
    assert "'Os01''; DROP TABLE id_table; --'" in recorded["sql"]
    assert "= 'Os01'; DROP" not in recorded["sql"]
    tasks = result["analysis_tasks"]
    assert len(tasks) == 11
    tissue = next(
        task
        for task in tasks
        if task["analysis_type"] == "gene_expression_tissues"
    )
    assert tissue["target_gene"] == "LOC_Os01g012345"


def test_analyst_node_name_maps_nine_generics_to_distinct_nodes() -> None:
    """The deterministic node-name rule covers all nine generics.

    ``analysis_type.removesuffix("_analysis") + "_node"`` must yield a
    distinct node name for every generic type and match the documented
    mapping (e.g. ``smep_analysis`` -> ``smep_node``).
    """
    expected = {
        "gene_expression_tissues": "gene_expression_tissues_node",
        "gene_expression_cultivars": "gene_expression_cultivars_node",
        "gene_expression_treatments": "gene_expression_treatments_node",
        "gene_expression_genotypes": "gene_expression_genotypes_node",
        "single_cell_analysis": "single_cell_node",
        "promoter_analysis": "promoter_node",
        "smep_analysis": "smep_node",
        "smoc_analysis": "smoc_node",
        "protein_structure_analysis": "protein_structure_node",
    }
    assert set(GENERIC_ANALYSIS_NODE_TYPES) == set(expected)
    names = [_analyst_node_name(t) for t in GENERIC_ANALYSIS_NODE_TYPES]
    assert names == [expected[t] for t in GENERIC_ANALYSIS_NODE_TYPES]
    assert len(set(names)) == len(GENERIC_ANALYSIS_NODE_TYPES)


async def test_prepare_analysis_tasks_generic_types_match_node_constant() -> (
    None
):
    """Every non-mount prepared task has a registered generic node.

    Guards against a future task added to ``_prepare_analysis_tasks``
    without a matching ``GENERIC_ANALYSIS_NODE_TYPES`` entry (which would
    route to an unregistered node at runtime). The two mount types are
    excluded.
    """
    harness = DispatchHarness("/tmp/deep-out")

    async def _fake_bi_json(sql: str) -> dict[str, Any]:
        del sql
        return {"data": [{"msu_gene_id": "LOC_Os01g012345"}]}

    setattr(harness, "_bi_json", _fake_bi_json)
    prepare = getattr(harness, "_prepare_analysis_tasks")
    result = await prepare({"gene_id": "Os01g012345", "species_code": "osa"})

    prepared = {task["analysis_type"] for task in result["analysis_tasks"]}
    mounts = {"evolution_analysis", "digital_design"}
    assert prepared - mounts == set(GENERIC_ANALYSIS_NODE_TYPES)


def _remote_config(deepgenome_out: str) -> SimpleNamespace:
    """Return the explicit remote-I/O configuration used by protocol tests."""
    return SimpleNamespace(
        ANALYSIS_URL="https://analysis.example",
        ANALYSIS_REGION="cn-test",
        RETRIABLE_CODES=[429, 503],
        MAX_RETRIES=3,
        TIMEOUT=7.0,
        BUCKET_NAME="phytomni",
        DEEPGENOME_OUT=deepgenome_out,
        OBS_SERVER="https://obs.example",
    )


async def test_remote_io_cancellation_scopes_id_and_redacts_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cancellation targets only the caller id and never logs secrets."""
    delete = AsyncMock(side_effect=RuntimeError("secret-token-should-not-log"))
    remote_io = DeepGenomeRemoteIO(
        config=_remote_config("/tmp/deep-out"),
        sensitive_config=FakeSensitiveConfig(),
        hooks={"task_delete": delete},
    )

    with caplog.at_level("WARNING"):
        await remote_io.cancel_submission(
            RemoteSubmission("caller-1", "shared-source-1", "/obs/out")
        )

    delete.assert_awaited_once_with(
        "caller-1",
        timeout=7.0,
        analysis_url="https://analysis.example",
        region="cn-test",
        retriable_codes=[429, 503],
        max_retries=3,
    )
    assert "secret-token-should-not-log" not in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.parametrize(
    ("analysis_type", "expected_key"),
    [
        ("protein_structure_analysis", "protein"),
        ("promoter_analysis", "promoter"),
    ],
)
async def test_remote_io_submit_selects_design_feature(
    analysis_type: str,
    expected_key: str,
) -> None:
    """Design analysis types use their dedicated producer wrappers."""
    producer = AsyncMock(
        return_value={
            "task_id": f"{expected_key}-task",
            "source_task_id": f"{expected_key}-source",
            "output_dir": "/obs/out",
        }
    )
    hooks: RemoteIOHooks = {}
    hooks[
        "protein_structure" if expected_key == "protein" else "promoter_design"
    ] = producer
    remote_io = DeepGenomeRemoteIO(
        config=_remote_config("/tmp/deep-out"),
        sensitive_config=FakeSensitiveConfig(),
        hooks=hooks,
    )
    context = AnalysisDispatchContext(
        analysis_type=analysis_type,
        species_code="ath",
        gene_id="GeneA",
        output_dir="/obs/out",
    )

    result = await remote_io.submit_analysis_task(context, prompt_parts=None)

    assert result == RemoteSubmission(
        f"{expected_key}-task", f"{expected_key}-source", "/obs/out"
    )
    producer.assert_awaited_once_with(
        species_code="ath",
        gene_id="GeneA",
        output_dir="/obs/out",
        is_polling=False,
    )


async def test_remote_io_submit_rejects_blank_ack_and_preserves_reused_id():
    """The adapter validates acknowledgements at the protocol boundary."""
    submit = AsyncMock(
        return_value={
            "task_id": "caller-2",
            "source_task_id": "source-2",
            "output_dir": "/obs/out",
        }
    )
    remote_io = DeepGenomeRemoteIO(
        config=_remote_config("/tmp/deep-out"),
        sensitive_config=FakeSensitiveConfig(),
        analyst_agent="analyst",
        hooks={"analyst_submit": submit},
    )
    context = AnalysisDispatchContext(
        analysis_type="haplotypes_analysis",
        species_code="ath",
        gene_id="GeneA",
        output_dir="/obs/out",
    )

    result = await remote_io.submit_analysis_task(
        context,
        prompt_parts=lambda _: ("goal", {"data": 1}, "meta", "small"),
    )

    assert result == RemoteSubmission("caller-2", "source-2", "/obs/out")
    assert submit.await_args is not None
    assert submit.await_args.args[0] == "analyst"
    submit.return_value = {"task_id": " ", "output_dir": "/obs/out"}
    with pytest.raises(ValueError, match="invalid analysis submission"):
        await remote_io.submit_analysis_task(
            context,
            prompt_parts=lambda _: ("goal", {"data": 1}, "meta", "small"),
        )


async def test_remote_io_poll_uses_effective_id_and_timeout_kwargs() -> None:
    """Polling delegates timing while binding status calls to the source id."""
    status = AsyncMock(return_value={"status": "SUCCEEDED"})
    download = AsyncMock(return_value="/tmp/results")
    observed: dict[str, Any] = {}

    async def fake_poll(
        request: WorkItemPollRequest,
    ) -> WorkItemOutcome:
        submission = request.submission
        observed.update(
            {
                "request_timeout": request.options.request_timeout,
                "poll_interval": request.options.poll_interval,
                "deadline_seconds": request.options.deadline_seconds,
            }
        )
        assert await request.callbacks.status_reader(
            submission.poll_task_id, 7.0
        ) == {"status": "SUCCEEDED"}
        assert (
            await cast(
                Awaitable[str | None],
                request.callbacks.result_resolver(submission),
            )
            == "/tmp/results"
        )
        return await cast(
            Awaitable[WorkItemOutcome],
            request.callbacks.transition_sink("succeeded", "# summary", None),
        )

    transition = AsyncMock(
        return_value=WorkItemOutcome("succeeded", "# summary", None)
    )
    remote_io = DeepGenomeRemoteIO(
        config=_remote_config("/tmp/deep-out"),
        sensitive_config=FakeSensitiveConfig(),
        hooks={
            "task_status": status,
            "download_result": download,
            "poll_work_item": fake_poll,
        },
    )
    context = AnalysisDispatchContext(
        analysis_type="haplotypes_analysis",
        species_code="ath",
        gene_id="GeneA",
        output_dir="/obs/out",
    )
    submission = RemoteSubmission("caller-1", "source-1", "/obs/out")

    outcome, result_dir = await remote_io.poll_remote_submission(
        submission,
        context,
        _fixed_run_identity(),
        summary_builder=lambda path: path,
        tracking=SimpleNamespace(persist_work_item_transition=transition),
        work_item_key="haplotypes_analysis",
    )

    assert outcome.status == "succeeded"
    assert result_dir == "/tmp/results"
    assert observed == {
        "request_timeout": 7.0,
        "poll_interval": 300.0,
        "deadline_seconds": 86400.0,
    }
    status.assert_awaited_once_with(
        "source-1",
        timeout=7.0,
        analysis_url="https://analysis.example",
        region="cn-test",
        retriable_codes=[429, 503],
        max_retries=3,
    )
    download.assert_awaited_once_with(
        context,
        "/obs/out",
        _fixed_run_identity(),
    )


async def test_remote_io_download_rejects_unsafe_obs_path(tmp_path) -> None:
    """OBS paths leaving the configured bucket fail before any download."""
    remote_io = DeepGenomeRemoteIO(
        config=_remote_config(str(tmp_path / "deep-out")),
        sensitive_config=FakeSensitiveConfig(),
    )
    context = AnalysisDispatchContext(
        analysis_type="haplotypes_analysis",
        species_code="ath",
        gene_id="GeneA",
        output_dir="/obs/out",
    )

    with pytest.raises(ValueError, match="escapes bucket root"):
        await remote_io.download_analysis_result(
            context,
            "/obs/phytomni/../secrets",
            _fixed_run_identity(),
        )
