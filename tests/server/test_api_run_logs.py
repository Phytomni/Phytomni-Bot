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
from tests.support.run_registry_fakes import (
    assert_not_found_response,
    foreign_run_spec,
    seed_foreign_run,
)

from mcp_server_phytomni.runtime.execution_defaults import (
    empty_execution_projection,
)
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunRequestInfo,
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
    # at the terminal-status guard and never probes live task status.
    # A non-terminal run still calls ``reconcile_task`` per child; the
    # offline ``send`` guard now fails that probe closed instead of
    # hanging after ``api_client`` restores ``request`` for ASGI.
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


async def test_get_run_logs_projects_remote_analyst_content_as_text(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The HTTP contract exposes ordered analyst log chunks as text."""
    RunRegistry(tasks_db_path).create_run(
        RunSpec("run-platform-log", "u1", "analyst", "remote"),
        outcome=RunOutcome(status="succeeded"),
    )
    TaskManager(tasks_db_path).record(
        Submission(
            task_id="task-local-log",
            status="succeeded",
            output_dir="/tmp/task-local-log",
            run_context=RunContext(run_id="run-platform-log"),
            source_task_id="task-remote-log",
        )
    )
    fetched_ids: list[str] = []

    async def fake_remote_log(task_id: str, **_: Any) -> dict[str, Any]:
        fetched_ids.append(task_id)
        return {
            "logs": [
                {"content": "Get conda environment finish!\n"},
                {"content": "[MCP] Loaded 35 tool(s).\n"},
            ]
        }

    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.task_log",
        fake_remote_log,
    )

    response = await api_client.get(
        "/v1/runs/run-platform-log/logs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    assert fetched_ids == ["task-remote-log"]
    assert response.json()["task_logs"] == [
        {
            "logs": [
                {"content": "Get conda environment finish!\n"},
                {"content": "[MCP] Loaded 35 tool(s).\n"},
            ],
            "text": (
                "Get conda environment finish!\n[MCP] Loaded 35 tool(s).\n"
            ),
        }
    ]


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


async def test_get_orphan_run_logs_are_finite_and_status_is_terminal(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """An orphaned detached run yields finite empty logs and settles."""
    registry = RunRegistry(tasks_db_path)
    registry.reserve_run(
        RunSpec("run-orphan-logs", "u1", "analyst", "remote"),
        request_info=RunRequestInfo(request_id="request-orphan-logs"),
        result=empty_execution_projection(),
    )
    headers = {"Authorization": f"Bearer {issued_api_key}"}
    logs = await api_client.get(
        "/v1/runs/run-orphan-logs/logs", headers=headers
    )
    status = await api_client.get("/v1/runs/run-orphan-logs", headers=headers)
    assert logs.status_code == status.status_code == 200
    assert logs.json() == {
        "run_id": "run-orphan-logs",
        "task_ids": [],
        "task_logs": [],
    }
    assert status.json()["status"] == "failed"
    assert status.json()["error"] == "run failed"
    record = registry.get_run("run-orphan-logs", owner="u1")
    assert record is not None
    assert record.error == "background_submission_worker_lost"


async def test_get_run_logs_default_strips_private_sentinel(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default task-log projection removes the complete raw payload."""
    registry = RunRegistry(tasks_db_path)
    registry.create_run(
        RunSpec("run-redacted-logs", "u1", "analyst", "remote"),
        outcome=RunOutcome(status="succeeded"),
    )
    TaskManager(tasks_db_path).record(
        Submission(
            task_id="task-redacted-log",
            status="succeeded",
            output_dir="/tmp/task-redacted-log",
            run_context=RunContext(run_id="run-redacted-logs"),
        )
    )

    async def fake_reconcile(_task_id: str) -> dict[str, Any]:
        """Return an intentionally private task-log payload."""
        return {
            "formatted": {"answer": "safe"},
            "raw": {
                "authorization": "Bearer sentinel",
                "path": "/private/input.fa",
                "arguments": {"secret": "value"},
            },
        }

    monkeypatch.setattr(
        "mcp_server_phytomni.api.app.reconcile_task_log", fake_reconcile
    )
    response = await api_client.get(
        "/v1/runs/run-redacted-logs/logs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["task_logs"] == [{"formatted": {"answer": "safe"}}]
    assert "sentinel" not in response.text
    assert "/private/input.fa" not in response.text
    assert "arguments" not in response.text


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
    assert_not_found_response(response)


async def test_get_run_logs_foreign_owner_is_404(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """A run owned by another user is invisible (404)."""
    seed_foreign_run(
        tasks_db_path,
        spec=foreign_run_spec("run-foreign-1", "u2", "analyst", "remote"),
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
