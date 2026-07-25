# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Compatibility contracts for extracted HTTP run-lifecycle helpers."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from mcp_server_phytomni.api import run_lifecycle as lifecycle_module
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunRequestInfo,
    RunSpec,
)

pytestmark = pytest.mark.server


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


def test_run_lifecycle_stream_settlement_and_owner_scope(
    tmp_path: Any,
) -> None:
    """Streaming settlement preserves request info and owner isolation."""
    db_path = str(tmp_path / "runs.db")
    request_info = RunRequestInfo(
        dialogue_id="dialogue-1",
        query="plant height",
        tool_name="ChatAgent",
        model="phyto-chat",
    )
    lifecycle_module.create_running_stream_run(
        "run-stream",
        "chat",
        "alice",
        request_info,
        db_path=db_path,
    )
    lifecycle_module.stamp_remote_request_info(
        run_id="run-stream",
        owner="alice",
        request_info=request_info,
        db_path=db_path,
    )
    purges: list[bool] = []
    lifecycle_module.settle_stream_run(
        "run-stream",
        "bob",
        "failed",
        {"error": "foreign"},
        context=lifecycle_module.RunLifecycleContext(
            db_path=db_path,
            purge=lambda: purges.append(True),
        ),
    )
    untouched = RunRegistry(db_path).get_run("run-stream", owner="alice")
    assert untouched is not None
    assert untouched.status == "running"
    lifecycle_module.settle_stream_run(
        "run-stream",
        "alice",
        "succeeded",
        {"answer": "done"},
        context=lifecycle_module.RunLifecycleContext(
            db_path=db_path,
            purge=lambda: purges.append(True),
        ),
    )
    settled = RunRegistry(db_path).get_run("run-stream", owner="alice")
    assert settled is not None
    assert settled.status == "succeeded"
    assert settled.result == {"answer": "done"}
    assert settled.request_info.dialogue_id == "dialogue-1"
    assert len(purges) == 2


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
