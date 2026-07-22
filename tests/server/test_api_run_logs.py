# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``GET /v1/runs/{run_id}/logs`` task log reconciliation.

Covers the happy path (run with tasks returns reconciled logs), empty
runs (no tasks yields empty task_logs), debug projection (raw fields
stripped by default, included with debug=true), owner isolation (unknown
run returns 404), and cache reuse (reconcile returns cached data without
re-polling the remote platform).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from tests.support.run_registry_fakes import seed_foreign_run

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


async def test_get_run_logs_returns_reconciled_task_logs(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run with tasks returns reconciled logs for each task."""
    registry = RunRegistry(tasks_db_path)
    # Use a terminal status so ``RunRegistry.reconcile`` short-circuits
    # at the terminal-status guard and never probes live task status —
    # a non-terminal run would call ``reconcile_task`` per child, which
    # escapes to a real ``task_status`` HTTP call and hangs the offline
    # test (``block_external_http`` covers ``request`` but the
    # ``api_client`` fixture restores it for ASGI transport, leaving
    # the low-level ``send`` unguarded).
    registry.create_run(
        RunSpec(
            run_id="run-logs-1",
            user_id="u1",
            agent="analyst",
            origin="remote",
        ),
        outcome=RunOutcome(status="succeeded"),
    )
    manager = TaskManager(tasks_db_path)
    manager.record(
        Submission(
            task_id="task-1",
            status="running",
            output_dir="/tmp/task-1",
            run_context=RunContext(run_id="run-logs-1"),
        )
    )
    manager.record(
        Submission(
            task_id="task-2",
            status="running",
            output_dir="/tmp/task-2",
            run_context=RunContext(run_id="run-logs-1"),
        )
    )

    reconcile_calls = []

    async def fake_reconcile(task_id: str) -> dict[str, Any]:
        """Simulate reconcile returning cached log data."""
        reconcile_calls.append(task_id)
        return {
            "formatted": {"answer": f"log for {task_id}"},
            "raw": {"internal": "debug-data"},
        }

    monkeypatch.setattr(
        "mcp_server_phytomni.api.app.reconcile_task_log", fake_reconcile
    )

    response = await api_client.get(
        "/v1/runs/run-logs-1/logs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "run-logs-1"
    assert set(body["task_ids"]) == {"task-1", "task-2"}
    assert len(body["task_logs"]) == 2
    # Raw fields stripped by default
    for log in body["task_logs"]:
        assert "formatted" in log
        assert "raw" not in log
    assert len(reconcile_calls) == 2


async def test_get_run_logs_empty_tasks(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """A run without tasks returns an empty task_logs list."""
    registry = RunRegistry(tasks_db_path)
    registry.create_run(
        RunSpec(
            run_id="run-empty-1",
            user_id="u1",
            agent="chat",
            origin="local",
        ),
        outcome=RunOutcome(status="succeeded"),
    )

    response = await api_client.get(
        "/v1/runs/run-empty-1/logs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "run-empty-1"
    assert body["task_ids"] == []
    assert body["task_logs"] == []


async def test_get_run_logs_response_keys_locked(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """The /logs response is locked to exactly three top-level keys.

    Earlier plan iterations promised an OpenAI-style ``object``
    discriminator and top-level lifts (``init_info`` / ``steps`` /
    ``tasks``); the shipped contract instead carries reconciled
    per-task logs verbatim inside ``task_logs``. This test catches
    any future drift toward the previously-documented shape or any
    silent new top-level key that would surprise chat-ai / Web Go.
    """
    registry = RunRegistry(tasks_db_path)
    registry.create_run(
        RunSpec(
            run_id="run-shape-1",
            user_id="u1",
            agent="chat",
            origin="local",
        ),
        outcome=RunOutcome(status="succeeded"),
    )

    response = await api_client.get(
        "/v1/runs/run-shape-1/logs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"run_id", "task_ids", "task_logs"}, (
        f"/v1/runs/{{id}}/logs response shape drifted; got keys "
        f"{sorted(body)}"
    )
    assert isinstance(body["task_ids"], list)
    assert isinstance(body["task_logs"], list)


async def test_get_run_logs_ignores_delegated_user_id_query(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Passing ``?user_id=`` does not promote the call to delegated.

    Under the candidate-A architecture every log belongs to the
    single ``web`` user so the route intentionally does not honour
    a delegated query. The route ignores the extra query parameter
    rather than 400-ing on it (FastAPI strict-mode would refuse the
    request); the response is still owner-scoped, so the caller can
    only ever see their own runs.
    """
    registry = RunRegistry(tasks_db_path)
    registry.create_run(
        RunSpec(
            run_id="run-deleg-1",
            user_id="u1",
            agent="chat",
            origin="local",
        ),
        outcome=RunOutcome(status="succeeded"),
    )

    response = await api_client.get(
        "/v1/runs/run-deleg-1/logs?user_id=someone-else",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "run-deleg-1"


async def test_get_run_logs_debug_includes_raw(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing debug=true includes raw fields in task logs."""
    registry = RunRegistry(tasks_db_path)
    # Terminal status — see test_get_run_logs_returns_reconciled_task_logs
    # for why a non-terminal run would hang the offline test.
    registry.create_run(
        RunSpec(
            run_id="run-debug-1",
            user_id="u1",
            agent="analyst",
            origin="remote",
        ),
        outcome=RunOutcome(status="succeeded"),
    )
    manager = TaskManager(tasks_db_path)
    manager.record(
        Submission(
            task_id="task-dbg",
            status="running",
            output_dir="/tmp/task-dbg",
            run_context=RunContext(run_id="run-debug-1"),
        )
    )

    async def fake_reconcile(_task_id: str) -> dict[str, Any]:
        """Return log with raw field."""
        return {
            "formatted": {"answer": "debug log"},
            "raw": {"internal": "debug-data", "trace": "full-trace"},
        }

    monkeypatch.setattr(
        "mcp_server_phytomni.api.app.reconcile_task_log", fake_reconcile
    )

    response = await api_client.get(
        "/v1/runs/run-debug-1/logs?debug=true",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["task_logs"]) == 1
    log = body["task_logs"][0]
    assert log["formatted"] == {"answer": "debug log"}
    assert log["raw"] == {"internal": "debug-data", "trace": "full-trace"}


async def test_get_run_logs_unknown_run_is_404(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
) -> None:
    """An unknown run id returns 404."""
    response = await api_client.get(
        "/v1/runs/does-not-exist/logs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == 404


async def test_get_run_logs_foreign_owner_is_404(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """A run owned by another user is invisible (404)."""
    seed_foreign_run(
        tasks_db_path,
        run_id="run-foreign-1",
        user_id="u2",
        agent="analyst",
        origin="remote",
    )

    response = await api_client.get(
        "/v1/runs/run-foreign-1/logs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 404


async def test_get_run_logs_cache_reuse(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated calls reuse cached logs without re-polling."""
    registry = RunRegistry(tasks_db_path)
    # Terminal status — see test_get_run_logs_returns_reconciled_task_logs
    # for why a non-terminal run would hang the offline test.
    registry.create_run(
        RunSpec(
            run_id="run-cache-1",
            user_id="u1",
            agent="analyst",
            origin="remote",
        ),
        outcome=RunOutcome(status="succeeded"),
    )
    manager = TaskManager(tasks_db_path)
    manager.record(
        Submission(
            task_id="task-cached",
            status="running",
            output_dir="/tmp/task-cached",
            run_context=RunContext(run_id="run-cache-1"),
        )
    )
    manager.set_task_log(
        "task-cached",
        {"formatted": {"answer": "cached"}, "raw": {"internal": "data"}},
    )

    remote_calls = []

    async def fake_remote(task_id: str, **_: Any) -> dict[str, Any]:
        """Track remote calls (should not happen for cached logs)."""
        remote_calls.append(task_id)
        return {"formatted": {"answer": "fresh"}, "raw": {}}

    monkeypatch.setattr(
        "mcp_server_phytomni.agents.analyst.task_ops.task_log",
        fake_remote,
    )

    # First call
    response1 = await api_client.get(
        "/v1/runs/run-cache-1/logs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response1.status_code == 200
    # Second call
    response2 = await api_client.get(
        "/v1/runs/run-cache-1/logs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response2.status_code == 200

    # Both calls should return the same cached data
    assert response1.json() == response2.json()
    # No remote calls should have been made
    assert len(remote_calls) == 0
