# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Compatibility contracts for extracted HTTP run-lifecycle helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from tests.support.sqlite import closed_sqlite_connection
from tests.unit.test_deep_genome_store_transitions import (
    _completed_store,
    store_run_result,
)

from mcp_server_phytomni.api import run_lifecycle as lifecycle_module
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunSpec,
)

pytestmark = pytest.mark.server


async def test_failed_deep_genome_polling_retains_citations_and_ready_delivery(
    tmp_path: Path,
) -> None:
    """Stored canonical failure can be projected repeatedly without loss."""
    store, reservation, before = _completed_store(tmp_path)
    references = [
        {"file_id": "unused", "title": "Unused paper"},
        {"file_id": "evidence", "title": "Evidence paper"},
    ]
    archive = {
        "role": "result_archive",
        "name": "analyst-results.zip",
        "media_type": "application/zip",
        "size_bytes": 32,
        "downloadable": True,
        "report_context_eligible": False,
        "download_ref": "result-archive:sha256:" + "a" * 64,
    }
    delivery = {
        "schema_version": 1,
        "required": True,
        "status": "ready",
        "revision": 1,
        "inventory_digest": "sha256:" + "a" * 64,
        "archive": archive,
        "error_code": None,
        "retryable": False,
    }
    store_run_result(
        store,
        reservation,
        {
            "formatted": {"references": references},
            "execution": {"artifacts": [archive], "delivery": delivery},
        },
    )
    store.fail_umbrella(
        reservation.umbrella_task_id, reason="final synthesis failed"
    )
    first = await lifecycle_module.fetch_owner_run(
        reservation.run_id, owner="alice", db_path=store.db_path
    )
    second = await lifecycle_module.fetch_owner_run(
        reservation.run_id, owner="alice", db_path=store.db_path
    )
    assert first == second
    assert first["status"] == "failed"
    result = first["result"]
    assert before.intermediate_report is not None
    assert result["formatted"]["answer"] == before.intermediate_report.replace(
        "<sup>2</sup>", "<sup>1</sup>"
    )
    assert [ref["file_id"] for ref in result["formatted"]["references"]] == [
        "evidence"
    ]
    assert result["execution"]["delivery"] == delivery
    assert result["execution"]["artifacts"] == [archive]
    assert result["execution"]["tasks"] == [
        {
            "id": reservation.umbrella_task_id,
            "accepted": True,
            "status": "failed",
        }
    ]
    assert result["execution"]["report"]["degraded"] is True
    metadata = result["formatted"]["metadata"]["deep_genome"]
    assert metadata["progress"]["succeeded"] == 12
    assert metadata["failure_count"] == metadata["progress"]["failed"] == 0
    with closed_sqlite_connection(store.db_path) as connection:
        stored = json.loads(
            connection.execute(
                "SELECT result_json FROM runs WHERE run_id = ?",
                (reservation.run_id,),
            ).fetchone()[0]
        )
    assert stored["formatted"]["answer"] == before.intermediate_report
    assert stored["formatted"]["references"] == references
    assert stored["execution"]["delivery"] == delivery
    with pytest.raises(HTTPException) as foreign:
        await lifecycle_module.fetch_owner_run(
            reservation.run_id, owner="bob", db_path=store.db_path
        )
    assert foreign.value.status_code == 404


@pytest.mark.parametrize(
    "status",
    ("running", "input_required", "succeeded", "failed"),
)
def test_run_lifecycle_projection_preserves_statuses(
    tmp_path: Any, status: str
) -> None:
    """The extracted projection keeps every public registry status."""
    db_path = str(tmp_path / "runs.db")
    RunRegistry(db_path).create_run(
        RunSpec("run-status", "alice", "chat", "local"),
        outcome=RunOutcome(
            status=status,
            result={"formatted": {"answer": "answer"}},
        ),
    )
    record = RunRegistry(db_path).get_run("run-status", owner="alice")
    assert record is not None
    projected = lifecycle_module.project_public_run_record(
        record, db_path=db_path
    )
    assert projected["status"] == status
    assert projected["answer"] == "answer"


async def test_run_lifecycle_owner_lookup_and_task_log_projection(
    tmp_path: Any,
) -> None:
    """Missing/foreign owners fail closed and logs honor debug stripping."""
    db_path = str(tmp_path / "runs.db")
    RunRegistry(db_path).create_run(
        RunSpec("run-logs", "alice", "analyst", "remote"),
        outcome=RunOutcome(status="succeeded"),
    )
    public = await lifecycle_module.fetch_owner_run(
        "run-logs", owner="alice", db_path=db_path
    )
    assert public["run_id"] == "run-logs"
    with pytest.raises(HTTPException) as foreign:
        await lifecycle_module.fetch_owner_run(
            "run-logs", owner="bob", db_path=db_path
        )
    assert foreign.value.status_code == 404
    with pytest.raises(HTTPException) as missing:
        await lifecycle_module.fetch_owner_run(
            "missing", owner="alice", db_path=db_path
        )
    assert missing.value.status_code == 404

    class PureReadRegistry:
        """Read-only registry that rejects lifecycle reconciliation."""

        def __init__(self, _path: str) -> None:
            self.inner = RunRegistry(db_path)

        def get_run(self, target: str, *, owner: str):
            """Return the configured run without side effects."""
            return self.inner.get_run(target, owner=owner)

        async def reconcile(self, *_args: Any, **_kwargs: Any) -> Any:
            """Record any unexpected reconciliation attempt."""
            raise AssertionError("GET must not reconcile")

    first = await lifecycle_module.fetch_owner_run(
        "run-logs",
        owner="alice",
        db_path=db_path,
        registry_factory=PureReadRegistry,
    )
    second = await lifecycle_module.fetch_owner_run(
        "run-logs",
        owner="alice",
        db_path=db_path,
        registry_factory=PureReadRegistry,
    )
    assert first == second

    async def fetch(_run_id: str) -> dict[str, Any]:
        return {"task_ids": ["task-1"]}

    async def reconcile(_task_id: str) -> dict[str, Any]:
        return {"formatted": {"answer": "ok"}, "raw": {"secret": True}}

    def strip(result: dict[str, Any]) -> dict[str, Any]:
        return {"formatted": result["formatted"]}

    public_logs = await lifecycle_module.reconcile_run_task_logs(
        "run-logs",
        False,
        fetch=fetch,
        reconcile=reconcile,
        strip=strip,
    )
    debug_logs = await lifecycle_module.reconcile_run_task_logs(
        "run-logs",
        True,
        fetch=fetch,
        reconcile=reconcile,
        strip=strip,
    )
    assert public_logs["task_logs"] == [{"formatted": {"answer": "ok"}}]
    assert debug_logs["task_logs"][0]["raw"] == {"secret": True}


def test_run_lifecycle_gc_claim_release() -> None:
    """The extracted GC slot coalesces and releases process-local work."""
    assert lifecycle_module.claim_run_gc() is True
    try:
        assert lifecycle_module.claim_run_gc() is False
    finally:
        lifecycle_module.release_run_gc()
    assert lifecycle_module.claim_run_gc() is True
    lifecycle_module.release_run_gc()
