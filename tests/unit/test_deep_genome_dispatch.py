# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for DeepGenome analyst result dispatch helpers.

Covers obsfs result reuse, SDK download fallback, dispatch context handling,
and the small harness used to exercise private download helpers.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from mcp_server_phytomni.agents.deep_genome import (
    dispatch as deep_genome_dispatch,
)
from mcp_server_phytomni.agents.deep_genome.dispatch import (
    AnalysisDispatchContext,
    DeepGenomeDispatchMixin,
)
from mcp_server_phytomni.storage.path_policy import IdFactory, RunIdentity

pytestmark = pytest.mark.unit


def _fixed_run_identity() -> RunIdentity:
    """Return a deterministic RunIdentity for local fallback paths."""
    factory = IdFactory(
        now=lambda: datetime(2026, 5, 7, 1, 2, 3, tzinfo=timezone.utc),
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


def test_download_analysis_result_uses_readable_obsfs_dir(
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
        species="ath",
        gene_id="GeneA",
        output_dir="/obs/phytomni/results/GeneA",
    )

    download_analysis_result = getattr(harness, "_download_analysis_result")

    assert download_analysis_result(
        context,
        "/obs/phytomni/results/GeneA",
        _fixed_run_identity(),
    ) == str(result_dir)


def test_download_analysis_result_falls_back_to_sdk_download(
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
        species="ath",
        gene_id="GeneA",
        output_dir="/obs/phytomni/results/GeneA",
    )

    download_analysis_result = getattr(harness, "_download_analysis_result")

    result = download_analysis_result(
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


async def test_bi_json_posts_via_async_factory(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify _bi_json sends the SQL payload via the shared async factory.

    The previous implementation called ``requests.post`` synchronously
    from inside ``async def _run_data_agent``, blocking the event loop
    and bypassing the central TLS resolver. This test pins the new
    async path: the factory is invoked with the configured TIMEOUT,
    the SQL JSON body and headers reach client.post, and the returned
    JSON flows back to the caller.

    Args:
        tmp_path: Unused; reserved for harness symmetry.
        monkeypatch: Pytest monkeypatch fixture used to swap the
            shared HTTP client factory with a recording stub.
    """
    del tmp_path
    harness = DispatchHarness("/tmp/deep-out")
    harness.deep_genome_config = SimpleNamespace(
        BI_URL="https://bi.example.invalid/query",
        TIMEOUT=42.0,
    )
    # _sql_headers lands on the real agent at __init__ time, not on
    # this mixin-only harness; setattr injects it just for this test
    # without forcing a typed subclass.
    setattr(harness, "_sql_headers", {"X-BI-Token": "stub-token"})
    recorded: dict[str, Any] = {}
    bi_response = httpx.Response(
        200, json={"message": "ok", "data": [{"x": 1}]}
    )

    class _PostStub:
        """One-shot recorder for the BI ``post`` call inside the factory."""

        def __init__(self) -> None:
            """Initialise the recorder with the canned BI response."""
            self._response = bi_response

        async def post(self, url: str, **post_kwargs: Any) -> Any:
            """Stash the URL plus kwargs and return the canned response."""
            recorded["url"] = url
            recorded["post_kwargs"] = post_kwargs
            return self._response

    @asynccontextmanager
    async def fake_factory(**factory_kwargs: Any):
        """Yield the recorder so the harness sees a post-capable client."""
        recorded["factory_kwargs"] = factory_kwargs
        yield _PostStub()

    monkeypatch.setattr(deep_genome_dispatch, "get_async_client", fake_factory)
    bi_json = getattr(harness, "_bi_json")

    payload = await bi_json("SELECT 1")

    assert payload == {"message": "ok", "data": [{"x": 1}]}
    assert recorded["factory_kwargs"]["timeout"] == 42.0
    assert recorded["url"] == "https://bi.example.invalid/query"
    assert recorded["post_kwargs"]["json"] == {
        "sql": "SELECT 1",
        "returnType": "json",
    }
    assert recorded["post_kwargs"]["headers"] == {"X-BI-Token": "stub-token"}


async def test_prepare_analysis_tasks_escapes_gene_id_and_builds_tasks() -> (
    None
):
    """The osa gene-id lookup escapes the id and threads the v2 id.

    Pins the SQL-injection fix: a gene id carrying a single quote must
    reach the BI query quote-doubled (via ``sql_literal``), never breaking
    out of its literal, and the osa branch must resolve the v2 id into the
    expression tasks while emitting all nine analysis tasks.
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
    assert len(tasks) == 9
    tissue = next(
        task
        for task in tasks
        if task["analysis_type"] == "gene_expression_tissues"
    )
    assert tissue["target_gene"] == "LOC_Os01g012345"


async def test_gene_summary_node_is_topology_passthrough(tmp_path) -> None:
    """Verify _run_gene_summary_node returns an empty state mutation.

    The node exists only to connect knowledge_node to the experiment /
    introduction branches when ``use_data_agent=False``; the previous
    body wrote a placeholder ``summary_context`` value that no other
    node reads. The contract this test pins is the absence of stale
    state writes, not any positive summary content.

    Args:
        tmp_path: Temporary directory used as fake deep-genome output root.
    """
    harness = DispatchHarness(str(tmp_path / "deep-out"))
    run_node = getattr(harness, "_run_gene_summary_node")
    sentinel_state: Any = {"gene_id": "GeneA", "species_code": "ath"}

    result = await run_node(sentinel_state)

    assert result == {}
    assert "summary_context" not in result
