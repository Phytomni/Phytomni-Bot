# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP and resolver regressions for remote lifecycle identity."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from mcp_server_phytomni import server
from mcp_server_phytomni.api import run_lifecycle
from mcp_server_phytomni.runtime import (
    submit_recorder as submit_recorder_module,
)
from mcp_server_phytomni.runtime.request_context import (
    current_run_id,
    request_context,
)
from mcp_server_phytomni.runtime.submit_recorder import (
    record_submitted_task,
    records_submission,
)

pytestmark = pytest.mark.server


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

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.ANALYST_AGENT.value,
        records_submission("analyst")(fake),
    )

    response = await api_client.post(
        "/v1/agents/analyst/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
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

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.ANALYST_AGENT.value,
        records_submission("analyst")(fake),
    )

    response = await api_client.post(
        "/v1/agents/analyst/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
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
    assert body["id"] is None
    assert "run_id" not in body
    assert body["task_ids"] == ["accepted-1"]
    assert body["degraded_tracking"] is True
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

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.ANALYST_AGENT.value,
        fake,
    )

    response = await api_client.post(
        "/v1/agents/analyst/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
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
