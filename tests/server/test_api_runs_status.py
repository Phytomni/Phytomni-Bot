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

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from tests.agents.shared.deep_genome_fixtures import (
    assert_report_metadata,
    attach_formatted_result,
    seed_partial_deep_genome_run,
)
from tests.support.run_registry_fakes import foreign_run_spec, seed_foreign_run

from mcp_server_phytomni.api.lifecycle_contract import empty_agent_result
from mcp_server_phytomni.runtime import run_registry as run_registry_module
from mcp_server_phytomni.runtime.deep_genome_store import DeepGenomeStore
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
    result = empty_agent_result()
    result["formatted"]["answer"] = "hello"
    registry.create_run(
        RunSpec(
            run_id="run-sync-1",
            user_id="u1",
            agent="chat",
            origin="local",
        ),
        outcome=RunOutcome(status="succeeded", result=result),
    )
    calls = {"n": 0}

    async def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
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
    assert body["result"]["formatted"]["answer"] == "hello"
    assert "raw" not in body["result"]
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
    assert response.json()["error"]["code"] == "not_found"


async def test_get_run_foreign_owner_is_404(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """A run owned by another user is invisible (same 404 envelope)."""
    seed_foreign_run(
        tasks_db_path,
        spec=foreign_run_spec("run-other-1", "someone-else", "chat", "local"),
        result={"answer": "secret"},
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

    async def fake(task_id: str) -> dict[str, Any]:
        """Return a terminal success for every child task with output_dir."""
        return {
            "task_id": task_id,
            "status": "succeeded",
            "output_dir": output_dirs[task_id],
        }

    monkeypatch.setattr(run_registry_module, "reconcile_task", fake)

    async def fake_enumerate(live: list, **_kwargs: Any) -> list:
        """Populate artifact paths without touching OBS at the HTTP layer."""
        for row in live:
            if row.get("output_dir"):
                row["artifact_paths"] = [f"{row['output_dir']}/fig.png"]
        return live

    monkeypatch.setattr(
        run_registry_module, "enumerate_artifact_paths", fake_enumerate
    )

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
    # The public terminal projection keeps accepted work identity but never
    # leaks the registry's live task rows or tenant artifact paths.
    execution = body["result"]["execution"]
    assert execution["tasks"] == [
        {"id": "t-1", "accepted": True},
        {"id": "t-2", "accepted": True},
    ]
    assert execution["artifacts"] == []
    assert "task_results" not in body["result"]
    assert "live_status" not in body["result"]
    # WO-1 contract: an analyst-class terminal run synthesizes a terminal
    # report whose compact answer _extract_answer lifts to the top-level
    # "answer" field chat-ai reads.
    answer = body["result"]["formatted"]["answer"]
    assert answer == "Analysis complete: 2/2 tasks succeeded."
    assert body["answer"] == answer


async def test_get_deep_genome_run_refreshes_intermediate_snapshot(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET projects the local report without probing the remote platform."""
    run_id = seed_partial_deep_genome_run(tasks_db_path)
    remote_status_mock = AsyncMock()
    monkeypatch.setattr(
        run_registry_module, "reconcile_task", remote_status_mock
    )

    response = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["result"]["intermediate_report"].startswith("#")
    assert body["result"]["final_report"] is None
    assert body["result"]["report_revision"] == 3
    assert body["result"]["degraded_reason"] == (
        "1 of 12 optional analyses unavailable"
    )
    assert body["answer"] == body["result"]["intermediate_report"]
    assert "raw" not in body["result"]
    assert "task_results" not in body["result"]
    assert "live_status" not in body["result"]
    assert "formatted" not in body["result"]
    remote_status_mock.assert_not_awaited()

    debug_response = await api_client.get(
        f"/v1/runs/{run_id}?debug=true",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert debug_response.status_code == 200
    assert "task_results" in debug_response.json()["result"]
    remote_status_mock.assert_not_awaited()


async def test_get_deep_genome_run_adds_report_metadata_to_formatted_result(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """The single-run projection adds metadata without replacing data."""
    run_id = seed_partial_deep_genome_run(tasks_db_path)
    attach_formatted_result(tasks_db_path, run_id)

    response = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    result = response.json()["result"]
    report = assert_report_metadata(result, stage="intermediate", revision=3)
    assert result["report_revision"] == report["revision"]


async def test_foreign_owner_cannot_probe_deep_genome_snapshot(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Owner filtering happens before the local snapshot lookup."""
    run_id = seed_partial_deep_genome_run(tasks_db_path, owner="u2")
    remote_status_mock = AsyncMock()
    monkeypatch.setattr(
        run_registry_module, "reconcile_task", remote_status_mock
    )

    response = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 404
    remote_status_mock.assert_not_awaited()


async def test_get_deep_genome_run_preserves_failed_intermediate_snapshot(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed umbrella still exposes its last local report snapshot."""
    run_id = seed_partial_deep_genome_run(tasks_db_path)
    DeepGenomeStore(tasks_db_path).fail_umbrella(
        "dg-u1", reason="final synthesis failed"
    )
    remote_status_mock = AsyncMock()
    monkeypatch.setattr(
        run_registry_module, "reconcile_task", remote_status_mock
    )

    response = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["result"]["intermediate_report"].startswith("#")
    assert body["result"]["final_report"] is None
    assert body["result"]["degraded"] is True
    remote_status_mock.assert_not_awaited()
