# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP and resolver regressions for remote lifecycle identity."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from typing import Any, NotRequired, TypedDict

import httpx
import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from tests.support.http_fakes import (
    assert_degraded_tracking_response,
    install_tool_handler,
    open_asgi_client,
)
from tests.support.resolver_fakes import (
    post_native_run,
    post_recorded_analyst_run,
)

from mcp_server_phytomni import server
from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.api import run_lifecycle
from mcp_server_phytomni.api.app import create_app
from mcp_server_phytomni.api.lifecycle_contract import canonicalize_run_record
from mcp_server_phytomni.runtime import (
    submit_recorder as submit_recorder_module,
)
from mcp_server_phytomni.runtime.checkpoint_backend import (
    build_default_checkpointer,
)
from mcp_server_phytomni.runtime.request_context import (
    current_run_id,
    request_context,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry
from mcp_server_phytomni.runtime.submit_recorder import (
    record_submitted_task,
    records_submission,
)

pytestmark = pytest.mark.server


def test_canonical_owner_read_preserves_sanitized_deep_genome_snapshot() -> (
    None
):
    """Canonical owner reads place the snapshot in report/execution fields."""
    canonical = canonicalize_run_record(
        {
            "id": "run-deep-genome-public",
            "run_id": "run-deep-genome-public",
            "agent": "deep_genome",
            "origin": "remote",
            "user_id": "u1",
            "status": "running",
            "task_ids": ["dg-public"],
            "result": {
                "formatted": {
                    "answer": "stored answer",
                    "metadata": {
                        "consumer": "artifact-ui",
                        "report": {
                            "stage": "intermediate",
                            "completeness": "partial",
                            "revision": 3,
                            "updated_at": "2026-07-25T00:00:00Z",
                            "progress": {
                                "planning_complete": True,
                                "brief_gene_status": "succeeded",
                                "total": 12,
                                "completed": 1,
                            },
                            "degraded": True,
                            "failure_count": 1,
                            "provider_payload": "private",
                        },
                    },
                },
                "intermediate_report": "# Intermediate report\n",
                "final_report": None,
                "report_stage": "intermediate",
                "report_completeness": "partial",
                "report_revision": 3,
                "report_updated_at": "2026-07-25T00:00:00Z",
                "progress": {
                    "planning_complete": True,
                    "brief_gene_status": "succeeded",
                    "total": 12,
                    "completed": 1,
                },
                "degraded": True,
                "degraded_reason": "1 of 12 optional analyses unavailable",
                "failures": [
                    {
                        "work_item_key": "protein_design",
                        "status": "failed",
                        "message": "analysis task failed",
                        "provider_trace": "private",
                    }
                ],
                "provider_payload": "private",
                "raw": {"provider_payload": "private"},
            },
        }
    )

    result = canonical["result"]
    assert result["formatted"]["answer"] == "# Intermediate report\n"
    assert result["execution"]["report"] == {
        "state": "intermediate",
        "degraded": True,
        "source_artifact_count": 0,
    }
    assert result["formatted"]["metadata"]["report"] == (
        result["execution"]["report"]
    )
    assert result["formatted"]["metadata"]["deep_genome"]["revision"] == 3
    assert result["execution"]["warnings"] == [
        {
            "code": "deep_genome_report_degraded",
            "retryable": False,
            "stage": "deep_genome",
        },
        {
            "code": "task_failed",
            "retryable": False,
            "stage": "reconcile",
        },
    ]
    for field in (
        "intermediate_report",
        "final_report",
        "report_revision",
        "failures",
        "provider_payload",
    ):
        assert field not in result
    assert "provider_payload" not in result


@pytest.mark.parametrize(
    ("field", "malformed_value"),
    (
        ("report_stage", []),
        ("report_completeness", {"provider": "private"}),
        (
            "failures",
            [
                {
                    "work_item_key": "protein_design",
                    "status": ["failed"],
                    "provider_trace": "private",
                }
            ],
        ),
    ),
)
def test_canonical_owner_read_redacts_unhashable_deep_genome_snapshot_fields(
    field: str, malformed_value: Any
) -> None:
    """Malformed persisted snapshots remain redacted on owner reads."""
    result: dict[str, Any] = {
        "formatted": {
            "answer": "stored answer",
            "metadata": {"provider_payload": "private"},
        },
        "intermediate_report": "# Intermediate report\n",
        "report_stage": "intermediate",
        "report_completeness": "partial",
        "report_revision": 3,
        "failures": [
            {
                "work_item_key": "protein_design",
                "status": "failed",
                "provider_trace": "private",
            }
        ],
        "provider_payload": "private",
    }
    result[field] = malformed_value

    canonical = canonicalize_run_record(
        {
            "id": "run-malformed-deep-genome",
            "run_id": "run-malformed-deep-genome",
            "agent": "deep_genome",
            "origin": "remote",
            "user_id": "u1",
            "status": "running",
            "task_ids": ["dg-malformed"],
            "result": result,
        }
    )

    projected = canonical["result"]
    assert "provider_payload" not in str(projected)
    assert "intermediate_report" not in projected
    assert "final_report" not in projected
    assert "failures" not in projected
    assert projected["formatted"]["answer"] == (
        "# Intermediate report\n" if field == "failures" else "stored answer"
    )


def _auth_headers(api_key: str) -> dict[str, str]:
    """Build an API-key header without embedding a credential sentinel."""
    return {"Authorization": f"{'Bearer'} {api_key}"}


class _RestartReviewState(TypedDict):
    """State for the file-backed pause/resume graph used below."""

    summary: str
    decision: NotRequired[dict[str, Any]]
    final_response: NotRequired[dict[str, Any]]


def _build_restart_review_graph(checkpointer: Any) -> Any:
    """Build a model-free Review graph with one durable interrupt."""

    async def pause(state: _RestartReviewState) -> dict[str, Any]:
        decision = interrupt({"draft": state["summary"]})
        return {"decision": decision}

    async def finish(_state: _RestartReviewState) -> dict[str, Any]:
        return {
            "final_response": {
                "choices": [
                    {
                        "message": {
                            "content": "Restarted review.",
                            "doc_list": [],
                            "follow_up_questions": [],
                        }
                    }
                ]
            }
        }

    graph: Any = StateGraph(_RestartReviewState)
    graph.add_node("pause", pause)
    graph.add_node("finish", finish)
    graph.add_edge(START, "pause")
    graph.add_edge("pause", "finish")
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer)


async def _new_restart_review_app(
    checkpoint_path: str,
) -> tuple[Any, Any]:
    """Open a fresh graph/checkpointer pair at one SQLite path."""
    checkpointer = build_default_checkpointer(checkpoint_path)
    await checkpointer.setup()
    return _build_restart_review_graph(checkpointer), checkpointer


async def _pause_restart_review(
    client: httpx.AsyncClient,
    api_key: str,
) -> tuple[str, str]:
    """Create the durable pause and return its run and surface IDs."""
    paused = await post_native_run(
        client,
        api_key,
        "review",
        {"user_query": "Review after restart.", "obs_file_list": []},
    )
    assert paused.status_code == 200
    body = paused.json()
    run_id = body["id"]
    surface_id = body["interrupt"]["draft"]["a2ui"]["surface_id"]
    return run_id, surface_id


def _assert_restart_run_persisted(tasks_db_path: str, run_id: str) -> None:
    """Verify the first process wrote the input-required registry row."""
    record = RunRegistry(tasks_db_path).get_run(run_id, owner="u1")
    assert record is not None
    assert record.status == "input_required"


async def _resume_restart_review(
    client: httpx.AsyncClient,
    api_key: str,
    run_id: str,
    surface_id: str,
) -> None:
    """Resume the reloaded pause and prove the old surface cannot replay."""
    fetched = await client.get(
        f"/v1/runs/{run_id}",
        headers=_auth_headers(api_key),
    )
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "input_required"
    fetched_surface = fetched.json()["result"]["interrupt"]["draft"]["a2ui"]
    assert fetched_surface["surface_id"] == surface_id

    action = {
        "run_id": run_id,
        "surface_id": surface_id,
        "widget": "confirm",
        "action_id": "restart-approve",
        "payload": {"accepted": True},
    }
    resumed = await client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers=_auth_headers(api_key),
        json=action,
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "succeeded"
    assert resumed.json()["result"]["formatted"]["answer"] == (
        "Restarted review."
    )

    replay = await client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers=_auth_headers(api_key),
        json=action,
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "a2ui_action_conflict"
    assert replay.json()["error"]["message"] == (
        "This input request has already been handled."
    )


async def test_sync_persistence_failure_is_not_success(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed native run fails closed when its row cannot be written."""

    async def fake(_args: Any) -> dict[str, Any]:
        return {"answer": "ok", "doc_list": []}

    def fail_create_run(*_args: Any, **_kwargs: Any) -> None:
        raise sqlite3.OperationalError("private database failure")

    install_tool_handler(
        monkeypatch, server.PhytomniAgents.CHAT_AGENT.value, fake
    )
    monkeypatch.setattr(RunRegistry, "create_run", fail_create_run)

    response = await post_native_run(
        api_client,
        issued_api_key,
        "chat",
        {"user_query": "hello", "obs_file_list": []},
    )

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "run_persistence_failed",
        "message": "The completed run could not be persisted.",
        "request_id": response.headers["x-request-id"],
        "stage": "run_persist",
        "retryable": False,
    }
    assert "private database failure" not in response.text


def test_resolve_remote_run_without_run_id_preserves_accepted_ids() -> None:
    """No local run returns request-scoped accepted work and recorder state."""
    healthy = run_lifecycle.resolve_remote_run(
        "owner-1",
        run_id=None,
        accepted_task_ids=("accepted-1",),
        recorder_degraded=False,
    )
    degraded = run_lifecycle.resolve_remote_run(
        "owner-1",
        run_id=None,
        accepted_task_ids=("accepted-2",),
        recorder_degraded=True,
    )

    assert healthy == run_lifecycle.ResolvedRemoteRun(
        run_id=None,
        task_ids=("accepted-1",),
        persisted=False,
        degraded_tracking=False,
    )
    assert degraded == run_lifecycle.ResolvedRemoteRun(
        run_id=None,
        task_ids=("accepted-2",),
        persisted=False,
        degraded_tracking=True,
    )


def test_resolve_remote_run_uses_durable_owner_scoped_row(
    tasks_db_path: str,
) -> None:
    """A durable owner row supplies the canonical run and task identities."""
    with request_context("owner-1", "request-1"):
        record_submitted_task(
            {"task_id": "durable-1", "output_dir": "tenant/out"},
            agent="analyst",
        )
        run_id = current_run_id()

    assert run_id is not None
    resolved = run_lifecycle.resolve_remote_run(
        "owner-1",
        run_id=run_id,
        accepted_task_ids=("fallback-1",),
        recorder_degraded=True,
        db_path=tasks_db_path,
    )

    assert resolved == run_lifecycle.ResolvedRemoteRun(
        run_id=run_id,
        task_ids=("durable-1",),
        persisted=True,
        degraded_tracking=False,
    )


def test_resolve_remote_run_missing_row_degrades_without_leaking_owner(
    tasks_db_path: str,
) -> None:
    """A missing owner-scoped row retains only this request's accepted ids."""
    with request_context("owner-1", "request-1"):
        record_submitted_task(
            {"task_id": "owner-1-task", "output_dir": "tenant/out"},
            agent="analyst",
        )
        run_id = current_run_id()

    assert run_id is not None
    resolved = run_lifecycle.resolve_remote_run(
        "owner-2",
        run_id=run_id,
        accepted_task_ids=("owner-2-accepted",),
        recorder_degraded=False,
        db_path=tasks_db_path,
    )

    assert resolved == run_lifecycle.ResolvedRemoteRun(
        run_id=None,
        task_ids=("owner-2-accepted",),
        persisted=False,
        degraded_tracking=True,
    )
    assert "owner-1-task" not in resolved.task_ids


async def test_remote_http_response_keeps_run_identity_byte_identical(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A durable remote response exposes the same public id in both fields."""

    async def fake(_args: Any) -> dict[str, Any]:
        return {"task_id": "accepted-healthy", "output_dir": "tenant/out"}

    install_tool_handler(
        monkeypatch,
        server.PhytomniAgents.ANALYST_AGENT.value,
        records_submission("analyst")(fake),
    )

    response = await api_client.post(
        "/v1/agents/analyst/runs",
        headers=_auth_headers(issued_api_key),
        json={
            "arguments": {
                "goal_description": "analyze this dataset",
                "data_list": {},
                "obs_file_list": [],
            }
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["id"] == body["run_id"]
    assert body["task_ids"] == ["accepted-healthy"]
    assert "degraded_tracking" not in body


async def test_remote_registry_failure_returns_real_tasks(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accepted remote work remains visible when local persistence fails."""

    def _raising_create_run(*_args: Any, **_kwargs: Any) -> None:
        raise sqlite3.OperationalError("closed")

    def _exploding_registry_factory(_db_path: str) -> SimpleNamespace:
        return SimpleNamespace(create_run=_raising_create_run)

    monkeypatch.setattr(
        submit_recorder_module, "RunRegistry", _exploding_registry_factory
    )

    async def fake(_args: Any) -> dict[str, Any]:
        return {"task_id": "accepted-1", "output_dir": "tenant/out"}

    response = await post_recorded_analyst_run(
        monkeypatch,
        api_client,
        issued_api_key,
        fake,
        {
            "goal_description": "analyze this dataset",
            "data_list": {},
            "obs_file_list": [],
        },
    )

    body = assert_degraded_tracking_response(response, "accepted-1")
    execution = body["result"]["execution"]
    assert execution["tracking"] == {"degraded": True}
    assert execution["warnings"] == [
        {
            "code": "run_registry_unavailable",
            "retryable": False,
        }
    ]
    assert execution["tasks"] == [{"id": "accepted-1", "accepted": True}]


async def test_remote_response_without_durable_or_accepted_work_is_safe_error(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A remote handler with no accepted identity cannot return a fake 202."""

    async def fake(_args: Any) -> dict[str, Any]:
        return {"output_dir": "tenant/out"}

    install_tool_handler(
        monkeypatch, server.PhytomniAgents.ANALYST_AGENT.value, fake
    )

    response = await api_client.post(
        "/v1/agents/analyst/runs",
        headers=_auth_headers(issued_api_key),
        json={
            "arguments": {
                "goal_description": "analyze this dataset",
                "data_list": {},
                "obs_file_list": [],
            }
        },
    )

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "running_without_work"
    assert error["message"] == (
        "agent run response violated lifecycle contract"
    )
    assert error["stage"] == "lifecycle"
    assert error["retryable"] is False
    assert error["request_id"]


async def test_review_a2ui_survives_client_and_registry_reload(
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
    tmp_path: Any,
) -> None:
    """A file-backed Review pause survives app/client reconstruction."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    checkpoint_path = str(tmp_path / "checkpoints.db")
    first = await _new_restart_review_app(checkpoint_path)
    try:
        monkeypatch.setattr(
            api_app_module, "_review_stream_app", lambda: first[0]
        )
        monkeypatch.setattr(
            api_app_module,
            "_review_initial_state",
            lambda _args: {"summary": "Restart review summary."},
        )
        async with open_asgi_client(
            monkeypatch, create_app(), base_url="http://api.restart.first"
        ) as first_client:
            run_id, surface_id = await _pause_restart_review(
                first_client, issued_api_key
            )
            _assert_restart_run_persisted(tasks_db_path, run_id)
    finally:
        await first[1].conn.close()

    second = await _new_restart_review_app(checkpoint_path)
    try:
        monkeypatch.setattr(
            api_app_module, "_review_stream_app", lambda: second[0]
        )
        async with open_asgi_client(
            monkeypatch, create_app(), base_url="http://api.restart.second"
        ) as second_client:
            await _resume_restart_review(
                second_client,
                issued_api_key,
                run_id,
                surface_id,
            )
    finally:
        await second[1].conn.close()

    assert (
        RunRegistry(tasks_db_path)
        .list_a2ui_actions(owner="u1", run_id=run_id)[0]
        .outcome
        == "succeeded"
    )
