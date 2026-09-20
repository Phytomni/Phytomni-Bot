# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Bounded owner-scoped artifacts made only from public operation facts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from .execution_event_limits import DEFAULT_EXECUTION_TRACE_DETAIL_LIMITS
from .execution_journal_v2 import ExecutionEventV2
from .execution_store_support_v2 import initialize_execution_v2_store
from .execution_trace_detail import OPERATION_PRESENTER_REGISTRY
from .sqlite import sqlite_transaction


@dataclass(frozen=True, slots=True)
class ExecutionLogArtifactV2:
    """Private storage result used to bind an opaque public target."""

    target_id: str
    media_type: str
    name: str
    size_bytes: int
    delivery_ref: str


@runtime_checkable
class ExecutionLogArtifactStore(Protocol):
    """Storage-neutral boundary used by Runtime and authorized delivery."""

    def put(
        self,
        *,
        owner: str,
        execution_id: str,
        payload: bytes,
    ) -> ExecutionLogArtifactV2:
        """Persist immutable execution-log bytes and return their binding."""
        raise NotImplementedError

    def get(
        self,
        *,
        owner: str,
        execution_id: str,
        target_id: str,
    ) -> bytes | None:
        """Read immutable log bytes through an owner-scoped target."""


def build_execution_log_document(
    execution_id: str,
    events: Iterable[ExecutionEventV2],
) -> bytes:
    """Serialize a finite log from validated public work-unit events only."""
    operation_by_work_unit: dict[str, str] = {}
    accepted_work_units: set[str] = set()
    records: list[dict[str, object]] = []
    truncated = False

    for event in events:
        if event.work_unit_id is None or not event.type.value.startswith(
            "work_unit."
        ):
            continue
        payload = event.public_payload.model_dump(
            mode="json", exclude_none=True
        )
        candidate = payload.get("operation_key") or payload.get("phase")
        if isinstance(candidate, str):
            presented = OPERATION_PRESENTER_REGISTRY.resolve(candidate)
            if presented.operation_key == candidate:
                operation_by_work_unit[event.work_unit_id] = candidate
        operation_key = operation_by_work_unit.get(event.work_unit_id)
        if operation_key is None:
            operation_key = "operation.unknown"
            safe_payload: dict[str, object] = {}
        else:
            safe_payload = payload

        if event.work_unit_id not in accepted_work_units:
            if (
                len(accepted_work_units)
                >= DEFAULT_EXECUTION_TRACE_DETAIL_LIMITS.max_operations_per_run
            ):
                truncated = True
                continue
            accepted_work_units.add(event.work_unit_id)

        record: dict[str, object] = {
            "seq": event.seq,
            "type": event.type.value,
            "status": event.status.value,
            "occurred_at": event.occurred_at,
            "operation_key": operation_key,
            "work_unit_id": event.work_unit_id,
            "attempt": event.attempt,
            "summary": event.summary.model_dump(mode="json"),
            "public_payload": safe_payload,
        }
        if event.target is not None:
            record["target"] = event.target.model_dump(mode="json")
        records.append(record)
        candidate_document = _serialize_document(
            execution_id, records, truncated=truncated
        )
        if (
            len(candidate_document)
            > DEFAULT_EXECUTION_TRACE_DETAIL_LIMITS.max_execution_log_bytes
        ):
            records.pop()
            truncated = True
            break

    document = _serialize_document(execution_id, records, truncated=truncated)
    DEFAULT_EXECUTION_TRACE_DETAIL_LIMITS.validate_execution_log_size(
        len(document)
    )
    return document


def _serialize_document(
    execution_id: str,
    records: list[dict[str, object]],
    *,
    truncated: bool,
) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "execution_id": execution_id,
            "truncated": truncated,
            "records": records,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


class SQLiteExecutionLogArtifactStore:
    """Immutable execution-log bytes with owner/tombstone authorization."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        initialize_execution_v2_store(self.db_path)

    def put(
        self,
        *,
        owner: str,
        execution_id: str,
        payload: bytes,
    ) -> ExecutionLogArtifactV2:
        """Persist content-addressed bytes once for a live owned execution."""
        DEFAULT_EXECUTION_TRACE_DETAIL_LIMITS.validate_execution_log_size(
            len(payload)
        )
        digest = hashlib.sha256(payload).hexdigest()
        target_id = f"log-{digest[:32]}"
        with sqlite_transaction(self.db_path, timeout=10) as connection:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            authorized = connection.execute(
                "SELECT 1 FROM runs WHERE user_id = ? AND execution_id = ? "
                "AND execution_tombstoned_at IS NULL",
                (owner, execution_id),
            ).fetchone()
            if authorized is None:
                connection.rollback()
                raise LookupError("execution log artifact unavailable")
            existing = connection.execute(
                "SELECT content_sha256, size_bytes FROM "
                "execution_log_artifacts_v2 WHERE owner_ref = ? "
                "AND execution_id = ? AND target_id = ?",
                (owner, execution_id, target_id),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO execution_log_artifacts_v2 "
                    "(owner_ref, execution_id, target_id, content_blob, "
                    "content_sha256, size_bytes, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        owner,
                        execution_id,
                        target_id,
                        payload,
                        digest,
                        len(payload),
                        datetime.now(UTC).isoformat(),
                    ),
                )
            elif tuple(existing) != (digest, len(payload)):
                connection.rollback()
                raise RuntimeError("execution log artifact conflict")
            connection.commit()
        return ExecutionLogArtifactV2(
            target_id=target_id,
            media_type="application/json",
            name="execution-log.json",
            size_bytes=len(payload),
            delivery_ref=f"execution-log:{target_id}",
        )

    def get(
        self,
        *,
        owner: str,
        execution_id: str,
        target_id: str,
    ) -> bytes | None:
        """Return bytes only for a live execution owned by the caller."""
        with sqlite_transaction(self.db_path) as connection:
            row = connection.execute(
                "SELECT a.content_blob FROM execution_log_artifacts_v2 a "
                "JOIN runs r ON r.user_id = a.owner_ref "
                "AND r.execution_id = a.execution_id "
                "WHERE a.owner_ref = ? AND a.execution_id = ? "
                "AND a.target_id = ? AND r.execution_tombstoned_at IS NULL",
                (owner, execution_id, target_id),
            ).fetchone()
        return None if row is None else bytes(row[0])
