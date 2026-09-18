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

import asyncio
from dataclasses import asdict
from typing import Any, Literal, cast
from unittest.mock import AsyncMock

import httpx
import pytest
from tests.agents.shared.deep_genome_fixtures import (
    assert_report_metadata,
    attach_formatted_result,
    seed_partial_deep_genome_run,
)
from tests.support.http_fakes import open_asgi_client
from tests.support.run_registry_fakes import (
    assert_not_found_response,
    foreign_run_spec,
    remote_analyst_seed,
    seed_foreign_run,
    seed_remote_run_with_task,
)
from tests.support.sqlite import closed_sqlite_connection

from mcp_server_phytomni.api.app import create_app
from mcp_server_phytomni.api.lifecycle_contract import empty_agent_result
from mcp_server_phytomni.mcp.formatting.models import (
    ResultArchiveDescriptor,
    ResultDelivery,
)
from mcp_server_phytomni.runtime import run_registry as run_registry_module
from mcp_server_phytomni.runtime import (
    run_registry_reports as run_registry_reports_module,
)
from mcp_server_phytomni.runtime.deep_genome_store import DeepGenomeStore
from mcp_server_phytomni.runtime.execution_defaults import (
    empty_execution_projection,
)
from mcp_server_phytomni.runtime.live_tasks import (
    deregister_live_task,
    register_live_task,
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
from mcp_server_phytomni.runtime.terminal_artifacts import TerminalArtifactSet

pytestmark = pytest.mark.server


_DELIVERY_DIGEST = "sha256:" + "a" * 64


def _delivery_run_result(
    *,
    status: Literal["pending", "ready", "failed"],
    retryable: bool,
    revision: int = 1,
    error_code: str = "archive_publish_failed",
) -> dict[str, Any]:
    """Build one bounded result with private retry coordination state."""
    result = empty_execution_projection(result_archive_required=True)
    result["formatted"]["answer"] = "scientific answer"
    archive = (
        ResultArchiveDescriptor(
            role="result_archive",
            name="analyst-results.zip",
            media_type="application/zip",
            size_bytes=128,
            downloadable=True,
            report_context_eligible=False,
            download_ref=f"result-archive:{_DELIVERY_DIGEST}",
        )
        if status == "ready"
        else None
    )
    result["execution"]["delivery"] = asdict(
        ResultDelivery(
            schema_version=1,
            required=True,
            status=status,
            revision=revision,
            inventory_digest=(
                _DELIVERY_DIGEST if status != "failed" or retryable else ""
            ),
            archive=archive,
            error_code=(
                None if status in {"pending", "ready"} else error_code
            ),
            retryable=retryable,
        )
    )
    result["delivery_internal"] = {
        "inventory_ref": "/private/obs/inventory.json",
        "attempts_claimed": 3,
        "last_error_code": "archive_publish_failed",
    }
    return result


def _seed_delivery_run(
    tasks_db_path: str,
    run_id: str,
    *,
    owner: str = "u1",
    agent: str = "analyst",
    **delivery_options: Any,
) -> None:
    """Persist one terminal delivery state for the HTTP route tests."""
    status = cast(
        Literal["pending", "ready", "failed"],
        delivery_options.get("status", "failed"),
    )
    retryable = bool(delivery_options.get("retryable", True))
    error_code = str(
        delivery_options.get("error_code", "archive_publish_failed")
    )
    RunRegistry(tasks_db_path).create_run(
        RunSpec(run_id, owner, agent, "remote"),
        outcome=RunOutcome(
            status="succeeded",
            result=_delivery_run_result(
                status=status,
                retryable=retryable,
                error_code=error_code,
            ),
        ),
    )


async def test_retry_delivery_is_owner_scoped_idempotent_and_public(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Owner retry returns one public pending revision and is idempotent."""
    _seed_delivery_run(tasks_db_path, "run-delivery-retry")
    headers = {"Authorization": f"Bearer {issued_api_key}"}

    first = await api_client.post(
        "/v1/runs/run-delivery-retry/delivery/retry", headers=headers
    )
    second = await api_client.post(
        "/v1/runs/run-delivery-retry/delivery/retry", headers=headers
    )

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert set(first.json()) == {
        "schema_version",
        "required",
        "status",
        "revision",
        "inventory_digest",
        "archive",
        "error_code",
        "retryable",
    }
    assert first.json()["status"] == "pending"
    assert first.json()["revision"] == 2
    assert "/private/obs/inventory.json" not in first.text
    assert "archive_publish_failed" not in first.text


async def test_retry_legacy_inventory_failure_reconciles_children_only(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy inventory failures retry collection without resubmitting EI."""
    run_id = "run-legacy-inventory-retry"
    _seed_delivery_run(
        tasks_db_path,
        run_id,
        retryable=False,
        error_code="no_user_deliverables",
        agent="design",
    )
    child_output_dir = "/obs/runs/legacy/children/part-001"
    TaskManager(tasks_db_path).record(
        Submission(
            task_id="legacy-child",
            status="succeeded",
            output_dir=child_output_dir,
            run_context=RunContext(
                run_id,
                "u1",
                "design",
                "remote",
                "2026-09-15T00:00:00+00:00",
                "2026-09-15T00:00:00+00:00",
            ),
        )
    )
    reconcile_calls: list[str] = []

    async def reconcile_only(
        registry: RunRegistry, child_run_id: str, *, owner: str, **_: Any
    ) -> Any:
        """Model child polling and delivery reconstruction without EI."""
        reconcile_calls.append(child_run_id)
        current = registry.get_run(child_run_id, owner=owner)
        assert current is not None and current.result is not None
        result = current.result
        result["execution"]["delivery"] = asdict(
            ResultDelivery(
                schema_version=1,
                required=True,
                status="pending",
                revision=1,
                inventory_digest=_DELIVERY_DIGEST,
                archive=None,
                error_code=None,
                retryable=False,
            )
        )
        registry.settle_run(
            child_run_id,
            owner=owner,
            status="succeeded",
            result=result,
            expected_revision=current.revision,
        )
        return registry.get_run(child_run_id, owner=owner)

    monkeypatch.setattr(RunRegistry, "reconcile", reconcile_only)
    response = await api_client.post(
        f"/v1/runs/{run_id}/delivery/retry",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert reconcile_calls == [run_id]


async def test_retry_delivery_hides_missing_and_foreign_runs(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Missing and foreign owners receive the same safe 404 response."""
    _seed_delivery_run(
        tasks_db_path,
        "run-delivery-foreign",
        owner="someone-else",
    )
    headers = {"Authorization": f"Bearer {issued_api_key}"}
    foreign = await api_client.post(
        "/v1/runs/run-delivery-foreign/delivery/retry", headers=headers
    )
    missing = await api_client.post(
        "/v1/runs/run-delivery-missing/delivery/retry", headers=headers
    )

    assert foreign.status_code == missing.status_code == 404
    for field in ("code", "message", "stage", "retryable"):
        assert foreign.json()["error"].get(field) == missing.json()[
            "error"
        ].get(field)


@pytest.mark.parametrize(
    ("status", "retryable"),
    [("failed", False), ("ready", False)],
)
async def test_retry_delivery_rejects_non_retryable_states(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    status: str,
    retryable: bool,
) -> None:
    """Ready and non-retryable failed delivery cannot start a new revision."""
    _seed_delivery_run(
        tasks_db_path,
        f"run-delivery-{status}",
        status=cast(Literal["pending", "ready", "failed"], status),
        retryable=retryable,
    )
    response = await api_client.post(
        f"/v1/runs/run-delivery-{status}/delivery/retry",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "run_state_conflict"
    assert "/private/obs/inventory.json" not in response.text


async def test_injected_registry_factory_is_used_by_fetch_and_retry_routes(
    monkeypatch: pytest.MonkeyPatch,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """App construction applies one injected registry policy to both routes."""
    calls: list[str] = []

    class RegistryFactory:
        """Record and construct the registry instances requested by the app."""

        def __bool__(self) -> bool:
            return False

        def __call__(self, db_path: str) -> RunRegistry:
            calls.append(db_path)
            return RunRegistry(db_path)

    registry_factory = RegistryFactory()

    _seed_delivery_run(tasks_db_path, "run-injected-retry")
    RunRegistry(tasks_db_path).create_run(
        RunSpec("run-injected-fetch", "u1", "chat", "local"),
        outcome=RunOutcome(status="succeeded", result=empty_agent_result()),
    )
    app = create_app(run_registry_factory=registry_factory)
    headers = {"Authorization": f"Bearer {issued_api_key}"}

    async with open_asgi_client(
        monkeypatch, app, base_url="http://api.injected"
    ) as client:
        retry = await client.post(
            "/v1/runs/run-injected-retry/delivery/retry", headers=headers
        )
        fetch = await client.get(
            "/v1/runs/run-injected-fetch", headers=headers
        )

    assert retry.status_code == 200
    assert fetch.status_code == 200
    assert calls == [tasks_db_path, tasks_db_path]


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


async def test_get_run_strips_private_delivery_state(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Delivery inventory references are never exposed by either read mode."""
    result = empty_execution_projection(result_archive_required=True)
    result["delivery_internal"] = {
        "inventory_ref": "/obs/private/inventory.json",
        "attempts_claimed": 1,
        "last_error_code": "archive_publish_failed",
    }
    RunRegistry(tasks_db_path).create_run(
        RunSpec("run-private-delivery", "u1", "analyst", "remote"),
        outcome=RunOutcome(status="running", result=result),
    )

    for suffix in ("", "?debug=true"):
        response = await api_client.get(
            f"/v1/runs/run-private-delivery{suffix}",
            headers={"Authorization": f"Bearer {issued_api_key}"},
        )
        assert response.status_code == 200
        body = response.json()
        assert "delivery_internal" not in body["result"]
        assert "inventory_ref" not in str(body["result"])


async def test_list_runs_strips_private_delivery_refs(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """List projections remove recursively injected private references."""
    result = empty_execution_projection(result_archive_required=True)
    result["formatted"]["metadata"] = {"inventory_ref": "/obs/private/ref"}
    result["execution"]["diagnostics"] = [{"inventory_ref": "private"}]
    RunRegistry(tasks_db_path).create_run(
        RunSpec("run-list-private-delivery", "u1", "analyst", "remote"),
        outcome=RunOutcome(status="running", result=result),
    )

    for suffix in ("", "?debug=true"):
        response = await api_client.get(
            f"/v1/runs{suffix}",
            headers={"Authorization": f"Bearer {issued_api_key}"},
        )
        assert response.status_code == 200
        row = next(
            item
            for item in response.json()["data"]
            if item["run_id"] == "run-list-private-delivery"
        )
        assert "inventory_ref" not in str(row["result"])


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
    assert_not_found_response(response)


async def test_get_run_preserves_request_and_task_identity(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Polling keeps the exact request, run, and task correlation."""
    run_id = "run-analyst-correlation"
    task_id = "task-analyst-correlation"
    seed_remote_run_with_task(
        tasks_db_path,
        remote_analyst_seed(run_id, task_id, "running"),
    )

    async def fake_reconcile(task: str) -> dict[str, Any]:
        """Keep the child in flight without contacting the platform."""
        return {"task_id": task, "status": "submitted"}

    monkeypatch.setattr(run_registry_module, "reconcile_task", fake_reconcile)

    response = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == run_id
    assert body["request_id"] == "request-analyst-1"
    assert body["dialogue_id"] == "dialogue-analyst-1"
    assert body["task_ids"] == [task_id]


async def test_get_nonterminal_run_never_reaches_live_send(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A running child is projected without any provider status request."""
    run_id = "run-nonterminal-send"
    seed_remote_run_with_task(
        tasks_db_path,
        remote_analyst_seed(run_id, "task-nonterminal-send", "running"),
    )

    async def forbid_reconcile(_task_id: str) -> dict[str, Any]:
        raise AssertionError("GET must not reconcile provider tasks")

    monkeypatch.setattr(
        run_registry_module, "reconcile_task", forbid_reconcile
    )
    response = await asyncio.wait_for(
        api_client.get(
            f"/v1/runs/{run_id}",
            headers={"Authorization": f"Bearer {issued_api_key}"},
        ),
        timeout=2.0,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "running"


async def test_get_run_does_not_fuzzy_match_task_metadata(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """An orphan task is never correlated by title or output directory."""
    run_id = "run-exact-correlation"
    registry = RunRegistry(tasks_db_path)
    registry.create_run(
        RunSpec(run_id, "u1", "analyst", "remote"),
        outcome=RunOutcome(status="succeeded", result={"ok": True}),
        request_info=RunRequestInfo(query="same title"),
    )
    TaskManager(tasks_db_path).record(
        Submission(
            task_id="orphan-task",
            status="succeeded",
            analysis_id="same title",
            output_dir="/obs/analyst",
        )
    )

    response = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    assert response.json()["task_ids"] == []


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

    foreign = await api_client.get(
        "/v1/runs/run-other-1",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    unknown = await api_client.get(
        "/v1/runs/run-other-unknown",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert foreign.status_code == unknown.status_code == 404
    foreign_error = foreign.json()["error"]
    unknown_error = unknown.json()["error"]
    for field in ("code", "message", "stage", "retryable"):
        assert foreign_error.get(field) == unknown_error.get(field)
    assert foreign_error["request_id"]
    assert unknown_error["request_id"]


async def test_get_zero_child_live_background_run_remains_running(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """A live detached worker keeps its zero-child run in flight."""
    run_id = "run-live-background"
    RunRegistry(tasks_db_path).reserve_run(
        RunSpec(run_id, "u1", "analyst", "remote"),
        request_info=RunRequestInfo(),
        result=empty_execution_projection(),
    )
    release = asyncio.Event()
    task = asyncio.create_task(release.wait())
    register_live_task(run_id, task)
    try:
        response = await api_client.get(
            f"/v1/runs/{run_id}",
            headers={"Authorization": f"Bearer {issued_api_key}"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "running"
        assert body["task_ids"] == []
    finally:
        release.set()
        await task
        deregister_live_task(run_id)


async def test_get_zero_child_orphan_background_run_is_a_pure_read(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """A GET never settles a missing worker outside the supervisor."""
    run_id = "run-orphan-background"
    registry = RunRegistry(tasks_db_path)
    registry.reserve_run(
        RunSpec(run_id, "u1", "analyst", "remote"),
        request_info=RunRequestInfo(
            query="safe public query",
            request_json=(
                '{"query":"query-sentinel","authorization":'
                '"Bearer sentinel"}'
            ),
        ),
        result={
            **empty_execution_projection(),
            "raw": {"path": "/private/input.fa"},
        },
    )
    with closed_sqlite_connection(tasks_db_path) as conn:
        conn.execute(
            "UPDATE runs SET error = ? WHERE run_id = ?",
            ("error-sentinel: /private/input.fa", run_id),
        )

    response = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["query"] == "safe public query"
    record = registry.get_run(run_id, owner="u1")
    assert record is not None
    assert record.status == "running"
    assert record.error == "error-sentinel: /private/input.fa"
    assert "query-sentinel" not in response.text
    assert "/private/input.fa" not in response.text
    assert "Bearer sentinel" not in response.text
    assert "error-sentinel" not in response.text


async def test_get_run_does_not_reconcile_non_terminal_children(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A running GET projects state without executing supervisor work."""
    registry = RunRegistry(tasks_db_path)
    ctx = RunContext(
        run_id="run-r-1",
        user_id="u1",
        agent="analyst",
        origin="remote",
        created_at="2026-05-20T00:00:00+00:00",
        updated_at="2026-05-20T00:00:00+00:00",
    )
    for task_id in ("t-1", "t-2"):
        TaskManager(tasks_db_path).record(
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

    reconciled: list[str] = []

    async def fake(task_id: str) -> dict[str, Any]:
        """Return a terminal success for every child task with output_dir."""
        reconciled.append(task_id)
        return {
            "task_id": task_id,
            "status": "succeeded",
            "output_dir": output_dirs[task_id],
        }

    monkeypatch.setattr(run_registry_module, "reconcile_task", fake)

    async def fake_collect(**_kwargs: Any) -> TerminalArtifactSet:
        """Return no artifacts without touching OBS at the HTTP layer."""
        return TerminalArtifactSet(artifacts=(), warnings=())

    monkeypatch.setattr(
        run_registry_reports_module,
        "collect_terminal_artifact_set",
        fake_collect,
    )

    response = await api_client.get(
        "/v1/runs/run-r-1",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["origin"] == "remote"
    assert sorted(body["task_ids"]) == ["t-1", "t-2"]
    assert reconciled == []
    record = registry.get_run("run-r-1", owner="u1")
    assert record is not None
    assert record.status == "running"


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
    result = body["result"]
    assert result["formatted"]["answer"].startswith("#")
    assert result["execution"]["report"]["state"] == "intermediate"
    assert result["formatted"]["metadata"]["deep_genome"]["revision"] == 3
    assert result["formatted"]["metadata"]["deep_genome"]["degraded"] is True
    assert body["answer"] == result["formatted"]["answer"]
    assert "intermediate_report" not in result
    assert "final_report" not in result
    assert "degraded_reason" not in result
    assert "raw" not in body["result"]
    assert "task_results" not in body["result"]
    assert "live_status" not in body["result"]
    assert "artifacts" not in body["result"]
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
    assert result["formatted"]["metadata"]["deep_genome"]["revision"] == 3
    assert report == result["execution"]["report"]


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
    result = body["result"]
    assert result["formatted"]["answer"].startswith("#")
    assert result["execution"]["report"]["state"] == "intermediate"
    assert result["execution"]["report"]["degraded"] is True
    assert "intermediate_report" not in result
    assert "final_report" not in result
    remote_status_mock.assert_not_awaited()
