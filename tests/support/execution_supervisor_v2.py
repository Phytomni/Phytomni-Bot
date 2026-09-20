# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared Execution Supervisor V2 test setup."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, NotRequired, TypedDict, Unpack

from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
    invoke_public_agent,
)
from mcp_server_phytomni.runtime.execution_journal_v2 import WorkUnitStatus
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    ExecutionReservationRecord,
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    ExecutionCommand,
)
from mcp_server_phytomni.runtime.execution_work_store_v2 import (
    SpanRecord,
    SpanSpec,
    SQLiteExecutionWorkRepository,
    WorkUnitRecord,
)
from mcp_server_phytomni.runtime.sqlite import sqlite_transaction


class _TestReservationFields(TypedDict):
    execution_id: str
    agent_slug: str
    fingerprint: str
    arguments: Mapping[str, Any]
    owner: NotRequired[str]
    durable_command: NotRequired[Mapping[str, Any] | None]


def reserve_test_execution(
    reservations: SQLiteExecutionReservationRepository,
    **fields: Unpack[_TestReservationFields],
) -> ExecutionReservationRecord:
    """Reserve one canonical test execution with optional durable input."""
    return reservations.reserve(
        owner=fields.get("owner", "alice"),
        execution_id=fields["execution_id"],
        fingerprint_version=1,
        fingerprint=fields["fingerprint"],
        command=ExecutionCommand(
            agent_slug=fields["agent_slug"],
            arguments=fields["arguments"],
        ),
        durable_command=fields.get("durable_command"),
    )


def seed_running_remote_execution(
    db_path: str,
    *,
    execution_id: str,
    agent_slug: str,
    arguments: dict[str, Any],
    owner: str = "alice",
) -> ExecutionReservationRecord:
    """Start one remote execution and return its durable reservation."""

    async def accepted() -> tuple[dict[str, str], int]:
        return {"status": "running"}, 202

    asyncio.run(
        invoke_public_agent(
            db_path=db_path,
            owner=owner,
            execution_id=execution_id,
            agent_slug=agent_slug,
            arguments=arguments,
            transport="authenticated_http",
            call=accepted,
        )
    )
    record = SQLiteExecutionReservationRepository(db_path).get(
        owner=owner,
        execution_id=execution_id,
    )
    assert record is not None
    return record


def create_root_span(
    work: SQLiteExecutionWorkRepository,
    reservation: ExecutionReservationRecord,
    *,
    label_key: str,
) -> SpanRecord:
    """Create one canonical root Agent span for a reserved execution."""
    return work.create_span(
        SpanSpec(
            owner=reservation.owner,
            execution_id=reservation.execution_id,
            span_id=reservation.root_span_id,
            kind="agent",
            label_key=label_key,
        )
    )


def ready_result_delivery(
    inventory_digest: str,
    archive: Mapping[str, object],
) -> dict[str, object]:
    """Return the public ready-delivery projection used by join tests."""
    return {
        "schema_version": 1,
        "required": True,
        "status": "ready",
        "revision": 1,
        "inventory_digest": inventory_digest,
        "archive": archive,
        "error_code": None,
        "retryable": False,
    }


def set_provider_join_lease(
    db_path: str,
    *,
    execution_id: str,
    lease_token: str,
    owner: str = "alice",
    expires_at: datetime | None = None,
) -> None:
    """Write one provider-join lease for a fencing test."""
    resolved_expiry = expires_at or (datetime.now(UTC) + timedelta(minutes=5))
    with sqlite_transaction(db_path) as connection:
        connection.execute(
            "UPDATE runs SET execution_provider_join_lease_owner = ?, "
            "execution_provider_join_lease_expires_at = ? "
            "WHERE user_id = ? AND execution_id = ?",
            (
                lease_token,
                resolved_expiry.isoformat(),
                owner,
                execution_id,
            ),
        )
        connection.commit()


def mark_work_unit_succeeded(
    work: SQLiteExecutionWorkRepository,
    unit: WorkUnitRecord,
) -> WorkUnitRecord:
    """Set one work unit to succeeded using its current revision."""
    current = work.get_work_unit(
        unit.execution_id,
        unit.work_unit_id,
        owner=unit.owner,
    )
    return work.update_work_unit_status(
        unit.execution_id,
        unit.work_unit_id,
        owner=unit.owner,
        status=WorkUnitStatus.SUCCEEDED,
        expected_revision=current.revision,
    )


def bind_acknowledged_provider(
    work: SQLiteExecutionWorkRepository,
    unit: WorkUnitRecord,
    *,
    provider_task_id: str,
    provider_kind: str = "analysis",
) -> WorkUnitRecord:
    """Bind one provider task and advance its unit to acknowledged."""
    bound = work.bind_provider(
        unit.execution_id,
        unit.work_unit_id,
        owner=unit.owner,
        provider_kind=provider_kind,
        provider_task_id=provider_task_id,
        provider_revision=0,
        expected_revision=unit.revision,
    )
    return work.update_work_unit_status(
        bound.execution_id,
        bound.work_unit_id,
        owner=bound.owner,
        status=WorkUnitStatus.ACKNOWLEDGED,
        expected_revision=bound.revision,
    )
