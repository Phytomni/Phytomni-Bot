# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared fixture builders for execution event and store tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, cast

from mcp_server_phytomni.runtime.execution_events import (
    ExecutionEventIntent,
    parse_execution_event_intent,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry
from mcp_server_phytomni.runtime.run_registry_models import (
    RunRequestInfo,
    local_run_spec,
)
from mcp_server_phytomni.runtime.sqlite import sqlite_transaction

_CONTRACTS_ROOT = Path(__file__).parents[2] / "docs" / "contracts"
EXECUTION_EVENTS_V1_FIXTURES = (
    _CONTRACTS_ROOT / "execution-events" / "v1" / "fixtures.json"
)
EXECUTION_RUNTIME_V2_FIXTURES = (
    _CONTRACTS_ROOT / "execution-runtime" / "v2" / "fixtures.json"
)
EXECUTION_TRACE_DETAIL_V1_FIXTURES = (
    _CONTRACTS_ROOT / "execution-trace-detail" / "v1" / "fixtures.json"
)


def _runtime_operation_record_contract() -> dict[str, object]:
    """Load the frozen operation-record capability contract."""
    document = cast(
        dict[str, object],
        json.loads(EXECUTION_RUNTIME_V2_FIXTURES.read_text(encoding="utf-8")),
    )
    capabilities = cast(dict[str, object], document["capabilities"])
    return cast(dict[str, object], capabilities["operation_records"])


def expected_unknown_operation_presenter() -> dict[str, object]:
    """Return a fresh frozen unknown-presenter descriptor."""
    contract = _runtime_operation_record_contract()
    return dict(cast(dict[str, object], contract["unknown_presenter"]))


def expected_trace_detail_limits() -> dict[str, int]:
    """Return fresh frozen limits shared by discovery and validation."""
    contract = _runtime_operation_record_contract()
    return dict(cast(dict[str, int], contract["limits"]))


def seed_execution_run(
    db_path: Path,
    owner: str,
    execution_id: str,
    *,
    include_request_info: bool = False,
) -> None:
    """Create and bind one owner-scoped run for repository tests."""
    request_info = (
        RunRequestInfo(execution_id=execution_id)
        if include_request_info
        else None
    )
    registry = RunRegistry(str(db_path))
    registry.create_run(
        local_run_spec(f"run-{execution_id}", owner, "chat"),
        request_info=request_info,
    )
    with sqlite_transaction(db_path) as connection:
        connection.execute(
            "UPDATE runs SET execution_id = ? WHERE run_id = ?",
            (execution_id, f"run-{execution_id}"),
        )
        connection.commit()


def todo_snapshot_intent(
    item_id: str,
    label_key: str,
    status: Literal["pending", "in_progress", "completed"],
) -> ExecutionEventIntent:
    """Build a valid one-item V1 Todo snapshot intent."""
    return parse_execution_event_intent(
        {
            "kind": "todo.snapshot",
            "status": "running",
            "summary": {
                "key": "activity.todo.snapshot",
                "text": "todo.snapshot",
            },
            "payload": {
                "items": [
                    {
                        "id": item_id,
                        "label_key": label_key,
                        "status": status,
                    }
                ]
            },
        }
    )
