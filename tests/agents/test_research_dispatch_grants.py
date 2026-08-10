# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Research dispatch fingerprint and relay-sidecar contracts."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.analyst.graph import _relay_analysis_body
from mcp_server_phytomni.agents.shared.remote_analysis import (
    RemoteAnalysisPrompt,
    RemoteAnalysisRequest,
    ResearchGrantUse,
    submit_remote_analysis,
)
from mcp_server_phytomni.graphs import analyst_dispatch_adapters as ada
from mcp_server_phytomni.graphs.analyst_dispatch_adapters import (
    map_send_payload_to_analyst_input,
)

pytestmark = pytest.mark.agent


def _grant() -> ResearchGrantUse:
    """Return one bounded fixture grant."""
    return ResearchGrantUse(
        dataset_id="dataset_001",
        exact_reference="obs://fixture-bucket/a.tsv",
        grant_id="grant_fixture_01",
        snapshot_digest="sha256-snapshot",
    )


def _research_request() -> RemoteAnalysisRequest:
    """Return one child request with explicit dispatch identity."""
    return RemoteAnalysisRequest(
        analysis_type="bounded-job",
        target_id="child-001",
        output_dir="/obs/run/children/part-001",
        prompt=RemoteAnalysisPrompt(
            goal_description="Use the validated data.",
            meta="bounded plan",
            data_list={"dataset_001": "validated"},
        ),
        compute_resource="medium",
        output_dir_is_result_child=True,
        dispatch_fingerprint="sha256-fixture",
        research_grants=(_grant(),),
        parent_run_id="run_fixture_01",
        obs_file_list=("obs://fixture-bucket/paper.pdf",),
    )


async def test_research_request_threads_fingerprint_and_private_sidecar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit child identity reaches the adapter without public leakage."""
    captured: dict[str, Any] = {}

    async def fake_submit(
        _agent: Any,
        _config: Any,
        _secrets: Any,
        payload: dict[str, Any],
        *,
        is_polling: bool,
    ) -> dict[str, Any]:
        captured.update(payload=payload, is_polling=is_polling)
        return {"task_id": "task-accepted", "output_dir": "/obs/out"}

    monkeypatch.setattr(
        "mcp_server_phytomni.agents.shared.remote_analysis"
        ".submit_analyst_via_subgraph",
        fake_submit,
    )

    result = await submit_remote_analysis(
        object(), object(), object(), _research_request(), is_polling=False
    )

    assert result["task_id"] == "task-accepted"
    assert captured["payload"]["dispatch_fingerprint"] == "sha256-fixture"
    assert captured["payload"]["research_grant_sidecar"] == {
        "schema_version": 1,
        "parent_run_id": "run_fixture_01",
        "execution_fingerprint": "sha256-fixture",
        "objects": [_grant().to_payload()],
    }
    assert captured["payload"]["obs_file_list"] == [
        "obs://fixture-bucket/paper.pdf"
    ]


def test_child_explicit_fingerprint_reaches_analyst_input() -> None:
    """The adapter maps explicit child identity to task persistence input."""
    payload = {
        "analysis_type": "research",
        "target_id": "child-001",
        "prompt_parts": ("goal", "meta", {"dataset_001": "desc"}),
        "compute_resource": "medium",
        "output_dir": "/obs/run/children/part-001",
        "output_dir_is_result_child": True,
        "dispatch_fingerprint": "sha256-fixture",
        "research_grant_sidecar": {
            "schema_version": 1,
            "parent_run_id": "run_fixture_01",
            "execution_fingerprint": "sha256-fixture",
            "objects": [],
        },
    }

    result = map_send_payload_to_analyst_input(payload)

    assert result.get("dispatch_fingerprint") == "sha256-fixture"
    assert result.get("input_fingerprint") == "sha256-fixture"
    assert (
        result.get("research_grant_sidecar")
        == payload["research_grant_sidecar"]
    )


async def test_explicit_child_fingerprint_reaches_context(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Child dispatch uses explicit identity instead of dropping it."""
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "tasks.sqlite"))
    captured: dict[str, Any] = {}

    def fake_context(
        _config: Any,
        _secrets: Any,
        request: dict[str, Any],
        fingerprint: str | None = None,
    ) -> Any:
        captured.update(request=request, fingerprint=fingerprint)
        return SimpleNamespace(
            analysis_type=request["analysis_type"],
            target_id=request["target_id"],
            output_dir=request["output_dir"],
            thread_id="thread-001",
        )

    async def fake_reuse(*_: Any, **__: Any) -> None:
        return None

    async def ainvoke(_state: Any, *, config: Any) -> dict[str, Any]:
        """Return one accepted child task for the adapter seam."""
        del config
        return {"task_id": "task-001", "output_dir": "/obs/out"}

    monkeypatch.setattr(ada, "prepare_analyst_dispatch_context", fake_context)
    monkeypatch.setattr(ada, "_reuse_prior_dispatch", fake_reuse)

    result = await ada.submit_analyst_via_subgraph(
        SimpleNamespace(app=SimpleNamespace(ainvoke=ainvoke)),
        SimpleNamespace(USER_ID="user-001"),
        object(),
        {
            "analysis_type": "research",
            "target_id": "child-001",
            "prompt_parts": ("goal", "meta", {"dataset_001": "desc"}),
            "compute_resource": "medium",
            "output_dir": "/obs/run/children/part-001",
            "output_dir_is_result_child": True,
            "dispatch_fingerprint": "sha256-fixture",
        },
        is_polling=False,
    )

    assert result["task_id"] == "task-001"
    assert captured["fingerprint"] == "sha256-fixture"


def test_relay_wrapper_preserves_plain_analysis_request_shape() -> None:
    """Only a validated sidecar introduces the relay wrapper."""
    job = {"name": "bounded-job", "tasks": []}
    assert _relay_analysis_body(job, None) is job
    assert _relay_analysis_body(
        job,
        {
            "schema_version": 1,
            "parent_run_id": "run_fixture_01",
            "execution_fingerprint": "sha256-fixture",
            "objects": [_grant().to_payload()],
        },
    ) == {
        "analysis_request": job,
        "research_input_grants": {
            "schema_version": 1,
            "parent_run_id": "run_fixture_01",
            "execution_fingerprint": "sha256-fixture",
            "objects": [_grant().to_payload()],
        },
    }


def test_relay_wrapper_rejects_invalid_sidecar_without_forward_shape() -> None:
    """Malformed grant data fails closed before any upstream call."""
    with pytest.raises(McpError, match="invalid research grant"):
        _relay_analysis_body(
            {"name": "bounded-job", "tasks": []},
            {
                "schema_version": 1,
                "parent_run_id": "run_fixture_01",
                "execution_fingerprint": "sha256-fixture",
                "objects": [{"grant_id": "only-one-field"}],
            },
        )
