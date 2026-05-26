# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``GET /v1/runs/{run_id}`` owner-scoped run status.

Covers terminal cache replay (no reconcile_task call), owner isolation
(unknown id and foreign-owned id both collapse to a 404 envelope), and
non-terminal reconciliation: the route polls each child task once and
settles the run as terminal when all children are success-like.
"""

from __future__ import annotations

from typing import Any, Dict

import httpx
import pytest

from mcp_server_phytomni.runtime import run_registry as run_registry_module
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunSpec,
)
from mcp_server_phytomni.runtime.task_manager import (
    RunContext,
    Submission,
    TaskManager,
)

pytestmark = pytest.mark.server


async def test_get_run_returns_terminal_record(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cached terminal run is replayed without any task poll."""
    registry = RunRegistry(tasks_db_path)
    registry.create_run(
        RunSpec(
            run_id="run-sync-1",
            user_id="u1",
            agent="chat",
            origin="local",
        ),
        outcome=RunOutcome(status="succeeded", result={"answer": "hello"}),
    )
    calls = {"n": 0}

    async def boom(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        """Track that the terminal cache skips reconcile_task."""
        _ = (args, kwargs)
        calls["n"] += 1
        return {"status": "succeeded"}

    monkeypatch.setattr(run_registry_module, "reconcile_task", boom)

    response = await api_client.get(
        "/v1/runs/run-sync-1",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "run-sync-1"
    assert body["status"] == "succeeded"
    assert body["agent"] == "chat"
    assert body["origin"] == "local"
    assert body["result"] == {"answer": "hello"}
    assert body["task_ids"] == []
    assert calls["n"] == 0


async def test_get_run_unknown_id_is_404(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """An unknown run id yields the unified 404 envelope."""
    _ = tasks_db_path

    response = await api_client.get(
        "/v1/runs/does-not-exist",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == 404


async def test_get_run_foreign_owner_is_404(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """A run owned by another user is invisible (same 404 envelope)."""
    RunRegistry(tasks_db_path).create_run(
        RunSpec(
            run_id="run-other-1",
            user_id="someone-else",
            agent="chat",
            origin="local",
        ),
        outcome=RunOutcome(status="succeeded", result={"answer": "secret"}),
    )

    response = await api_client.get(
        "/v1/runs/run-other-1",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 404


async def test_get_run_reconciles_non_terminal_to_terminal(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A running run polls its children once and settles as succeeded."""
    registry = RunRegistry(tasks_db_path)
    manager = TaskManager(tasks_db_path)
    ctx = RunContext(
        run_id="run-r-1",
        user_id="u1",
        agent="analyst",
        origin="remote",
        created_at="2026-05-20T00:00:00+00:00",
        updated_at="2026-05-20T00:00:00+00:00",
    )
    for task_id in ("t-1", "t-2"):
        manager.record(
            Submission(
                task_id=task_id,
                status="submitted",
                output_dir="/obs/run",
                run_context=ctx,
            )
        )
    registry.create_run(
        RunSpec(
            run_id="run-r-1",
            user_id="u1",
            agent="analyst",
            origin="remote",
        )
    )

    output_dirs = {"t-1": "/obs/x", "t-2": "/obs/y"}

    async def fake(task_id: str) -> Dict[str, Any]:
        """Return a terminal success for every child task with output_dir."""
        return {
            "task_id": task_id,
            "status": "succeeded",
            "output_dir": output_dirs[task_id],
        }

    monkeypatch.setattr(run_registry_module, "reconcile_task", fake)

    response = await api_client.get(
        "/v1/runs/run-r-1",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["origin"] == "remote"
    assert sorted(body["task_ids"]) == ["t-1", "t-2"]
    assert body["expires_at"] is not None
    # The terminal envelope keeps task_results / live_status pointed at
    # the reconciled blob and exposes one artifacts descriptor per
    # succeeded child task that carries an output_dir.
    assert body["result"]["task_results"] == body["result"]["live_status"]
    assert body["result"]["artifacts"] == [
        {"task_id": "t-1", "output_dir": "/obs/x", "paths": []},
        {"task_id": "t-2", "output_dir": "/obs/y", "paths": []},
    ]
