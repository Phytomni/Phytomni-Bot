# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Branch edges for graphs/analyst_dispatch_adapters.py."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.analyst import task_ops
from mcp_server_phytomni.graphs import analyst_dispatch_adapters as ada
from mcp_server_phytomni.graphs.analyst_dispatch_adapters import (
    build_analyst_dispatch_request,
    map_analyst_output_to_dispatch_state,
    map_send_payload_to_analyst_input,
)
from mcp_server_phytomni.runtime.task_manager import Submission, TaskManager

from ._analyst_fakes import fake_submitting_agent

pytestmark = pytest.mark.agent


def _payload(**overrides: Any) -> dict[str, Any]:
    """Return one dispatch payload with optional field overrides."""
    body: dict[str, Any] = {
        "analysis_type": "design",
        "target_id": "AT1G01010",
        "prompt_parts": (
            "Design a CRISPR knockout.",
            "preset-meta",
            {"sample.tsv": "matrix"},
        ),
        "compute_resource": "small",
        "output_dir": "/obs/run/out",
    }
    body.update(overrides)
    return body


def test_build_analyst_dispatch_request_projects_prompt_parts() -> None:
    """The shared builder keeps goal, meta, and data_list as prompt_parts."""
    request = build_analyst_dispatch_request(
        "design",
        "AT1G01010",
        {
            "goal_description": "goal",
            "meta": "meta-plan",
            "data_list": {"a.tsv": "counts"},
            "output_dir": "/obs/out",
        },
        "medium",
    )

    assert request["analysis_type"] == "design"
    assert request["target_id"] == "AT1G01010"
    assert request["compute_resource"] == "medium"
    assert request["prompt_parts"] == (
        "goal",
        "meta-plan",
        {"a.tsv": "counts"},
    )


@pytest.mark.parametrize(
    "obs_file_list",
    (
        "obs://private/a.pdf",
        ["obs://private/a.pdf", ""],
        ["obs://private/a.pdf", 3],
        [""],
    ),
)
def test_map_send_payload_rejects_invalid_obs_file_list(
    obs_file_list: Any,
) -> None:
    """obs_file_list must be a sequence of non-empty strings."""
    with pytest.raises(ValueError, match="obs_file_list"):
        map_send_payload_to_analyst_input(
            _payload(obs_file_list=obs_file_list)
        )


def test_map_send_payload_accepts_tuple_obs_file_list() -> None:
    """A tuple of document paths is copied onto AnalystInput."""
    result = map_send_payload_to_analyst_input(
        _payload(obs_file_list=("obs://private/a.pdf",))
    )
    mapped = cast(dict[str, Any], result)

    assert mapped["obs_file_list"] == ["obs://private/a.pdf"]


def test_map_send_payload_omits_optional_document_and_grant_fields() -> None:
    """A bare payload leaves obs_file_list and sidecar unset."""
    result = map_send_payload_to_analyst_input(_payload())

    assert "obs_file_list" not in result
    assert "dispatch_fingerprint" not in result
    assert "research_grant_sidecar" not in result


def test_map_send_payload_threads_fingerprint_and_sidecar() -> None:
    """Explicit identity and grant sidecar land on AnalystInput."""
    sidecar = {"schema_version": 1, "objects": []}
    result = map_send_payload_to_analyst_input(
        _payload(
            dispatch_fingerprint="sha256-map",
            research_grant_sidecar=sidecar,
        )
    )
    mapped = cast(dict[str, Any], result)

    assert mapped["dispatch_fingerprint"] == "sha256-map"
    assert mapped["input_fingerprint"] == "sha256-map"
    assert mapped["research_grant_sidecar"] is sidecar


def test_map_analyst_output_projects_missing_keys_as_none() -> None:
    """The dispatch projection surfaces absent analyst fields as None."""
    result = map_analyst_output_to_dispatch_state(
        {"task_id": "T-out", "output_dir": "/obs/out"}
    )

    assert result == {
        "task_id": "T-out",
        "output_dir": "/obs/out",
        "plan": None,
        "tool_usages": None,
        "task_status": None,
    }


def test_dispatch_identity_rejects_blank_fingerprint() -> None:
    """Whitespace-only dispatch fingerprints fail before reuse lookup."""
    dispatch_identity = getattr(ada, "_dispatch_identity")
    with pytest.raises(ValueError, match="dispatch_fingerprint"):
        dispatch_identity(_payload(dispatch_fingerprint="   "))


def test_dispatch_identity_rejects_non_string_fingerprint() -> None:
    """A non-string fingerprint is the same programming error as blank."""
    dispatch_identity = getattr(ada, "_dispatch_identity")
    with pytest.raises(ValueError, match="dispatch_fingerprint"):
        dispatch_identity(_payload(dispatch_fingerprint=123))


def test_dispatch_identity_returns_explicit_fingerprint() -> None:
    """A non-blank explicit fingerprint is returned unchanged."""
    dispatch_identity = getattr(ada, "_dispatch_identity")
    child, fingerprint = dispatch_identity(
        _payload(dispatch_fingerprint="sha256-explicit")
    )

    assert child is False
    assert fingerprint == "sha256-explicit"


def test_dispatch_identity_computes_fingerprint_for_standalone() -> None:
    """A non-child request without an explicit digest uses the formula."""
    dispatch_identity = getattr(ada, "_dispatch_identity")
    child, fingerprint = dispatch_identity(_payload())

    assert child is False
    assert fingerprint == getattr(ada, "_dispatch_fingerprint")(_payload())


def test_dispatch_identity_child_without_fingerprint_is_none() -> None:
    """A flagged result child does not invent a digest of its own."""
    dispatch_identity = getattr(ada, "_dispatch_identity")
    child, fingerprint = dispatch_identity(
        {
            **_payload(),
            "output_dir": "/obs/run/children/part-001",
            "output_dir_is_result_child": True,
        }
    )

    assert child is True
    assert fingerprint is None


async def test_reuse_prior_dispatch_skips_unrecognized_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live SQL hit with an unknown status must not be reused."""
    db = str(tmp_path / "tasks.sqlite")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", db)
    TaskManager(db).record(
        Submission(
            task_id="prior-queued",
            status="queued",
            output_dir="/obs/prior",
            input_fingerprint="fp-queued",
        )
    )

    reuse_prior = getattr(ada, "_reuse_prior_dispatch")
    reused = await reuse_prior("fp-queued", require_terminal_success=False)

    assert reused is None


async def test_reuse_prior_dispatch_skips_dead_live_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A submitted prior whose live probe is dead is not reused."""
    db = str(tmp_path / "tasks.sqlite")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", db)
    TaskManager(db).record(
        Submission(
            task_id="prior-dead",
            status="submitted",
            output_dir="/obs/prior",
            input_fingerprint="fp-dead",
        )
    )

    async def fake_probe(task_id: str) -> str:
        del task_id
        return "FAILED"

    monkeypatch.setattr(task_ops, "probe_live_status", fake_probe)

    reuse_prior = getattr(ada, "_reuse_prior_dispatch")
    reused = await reuse_prior("fp-dead", require_terminal_success=False)

    assert reused is None


async def test_reuse_prior_dispatch_misses_when_no_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty registry is a miss, not a reuse hit."""
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "tasks.sqlite"))
    reuse_prior = getattr(ada, "_reuse_prior_dispatch")

    assert (
        await reuse_prior("fp-missing", require_terminal_success=False) is None
    )


async def test_submit_via_subgraph_records_a_fresh_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fingerprint miss runs ainvoke and persists the new task row."""
    db = str(tmp_path / "tasks.sqlite")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", db)

    async def fake_context(
        _config: Any,
        request: dict[str, Any],
        fingerprint: str | None = None,
    ) -> SimpleNamespace:
        del fingerprint
        return SimpleNamespace(
            analysis_type=request["analysis_type"],
            target_id=request["target_id"],
            output_dir="/obs/fresh",
            thread_id="thread-miss",
        )

    monkeypatch.setattr(ada, "prepare_analyst_dispatch_context", fake_context)

    result = await ada.submit_analyst_via_subgraph(
        fake_submitting_agent("T-fresh", output_dir="/obs/fresh"),
        SimpleNamespace(USER_ID="user-1"),
        object(),
        _payload(dispatch_fingerprint="fp-miss"),
        is_polling=False,
    )

    assert result["task_id"] == "T-fresh"
    found = TaskManager(db).get_task_by_fingerprint("fp-miss")
    assert found is not None
    assert found["task_id"] == "T-fresh"


async def test_submit_via_subgraph_child_skips_fingerprint_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flagged result child submits fresh and does not record a digest."""

    async def fake_context(
        _config: Any,
        request: dict[str, Any],
        fingerprint: str | None = None,
    ) -> SimpleNamespace:
        assert fingerprint is None
        return SimpleNamespace(
            analysis_type=request["analysis_type"],
            target_id=request["target_id"],
            output_dir=request["output_dir"],
            thread_id="thread-child",
        )

    async def unexpected_reuse(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("child dispatch must not reuse a fingerprint")

    monkeypatch.setattr(ada, "prepare_analyst_dispatch_context", fake_context)
    monkeypatch.setattr(ada, "_reuse_prior_dispatch", unexpected_reuse)

    result = await ada.submit_analyst_via_subgraph(
        fake_submitting_agent("T-child"),
        SimpleNamespace(USER_ID="user-child"),
        object(),
        {
            **_payload(),
            "output_dir": "/obs/run/children/part-001",
            "output_dir_is_result_child": True,
        },
        is_polling=False,
    )

    assert result["task_id"] == "T-child"


async def test_submit_via_subgraph_rejects_blank_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blank identity fails in _dispatch_identity before context prep."""

    async def unexpected_context(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("context must not run on a bad fingerprint")

    monkeypatch.setattr(
        ada, "prepare_analyst_dispatch_context", unexpected_context
    )

    with pytest.raises(ValueError, match="dispatch_fingerprint"):
        await ada.submit_analyst_via_subgraph(
            fake_submitting_agent("T-bad"),
            SimpleNamespace(USER_ID="user-1"),
            object(),
            _payload(dispatch_fingerprint=""),
            is_polling=False,
        )


async def test_submit_via_subgraph_reuses_verified_prior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A verified-live fingerprint hit returns the normalized reuse shape."""
    db = str(tmp_path / "tasks.sqlite")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", db)

    async def fake_context(
        _config: Any,
        request: dict[str, Any],
        fingerprint: str | None = None,
    ) -> SimpleNamespace:
        del fingerprint
        return SimpleNamespace(
            analysis_type=request["analysis_type"],
            target_id=request["target_id"],
            output_dir=request["output_dir"],
            thread_id="thread-reuse",
        )

    async def fake_probe(task_id: str) -> str:
        del task_id
        return "SUCCEEDED"

    monkeypatch.setattr(ada, "prepare_analyst_dispatch_context", fake_context)
    monkeypatch.setattr(task_ops, "probe_live_status", fake_probe)
    TaskManager(db).record(
        Submission(
            task_id="prior-ok",
            status="succeeded",
            output_dir="/obs/prior-ok",
            input_fingerprint="fp-ok",
        )
    )

    result = await ada.submit_analyst_via_subgraph(
        fake_submitting_agent("T-must-not-run"),
        SimpleNamespace(USER_ID="user-1"),
        object(),
        _payload(dispatch_fingerprint="fp-ok"),
        is_polling=True,
    )

    assert result["task_id"] != "prior-ok"
    assert result["source_task_id"] == "prior-ok"
    assert result["output_dir"] == "/obs/prior-ok"
    assert result["plan"] is None
