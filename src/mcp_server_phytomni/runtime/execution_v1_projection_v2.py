# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Read-only V1 compatibility views over the canonical V2 journal."""

from __future__ import annotations

from typing import Any

from .execution_event_projection import fold_execution_events
from .execution_event_store import (
    ExecutionEventPage,
    SQLiteExecutionEventStore,
)
from .execution_events import ExecutionEventV1, parse_execution_event
from .execution_journal_store_v2 import (
    ExecutionJournalNotFoundError,
    SQLiteExecutionJournal,
)
from .execution_journal_v2 import ExecutionEventV2
from .run_registry import RunRegistry

_TYPE_MAP = {
    "execution.admitted": "run.accepted",
    "execution.started": "run.started",
    "execution.waiting_input": "run.waiting_input",
    "execution.resumed": "run.resumed",
    "execution.succeeded": "run.succeeded",
    "execution.partial": "run.failed",
    "execution.failed": "run.failed",
    "execution.cancelled": "run.cancelled",
    "execution.timed_out": "run.failed",
    "span.started": "phase.started",
    "span.progress": "phase.progress",
    "span.succeeded": "phase.completed",
    "span.failed": "phase.failed",
    "work_unit.attempt_started": "tool.started",
    "work_unit.succeeded": "tool.completed",
    "work_unit.failed": "tool.failed",
    "todo.snapshot": "todo.snapshot",
    "reasoning.summary": "reasoning.summary",
    "decision.note": "decision.note",
    "input.required": "input.required",
    "input.resolved": "input.resolved",
    "artifact.published": "artifact.published",
    "result.published": "artifact.published",
    "tracking.degraded": "tracking.degraded",
}


class V1ExecutionCompatibilityReader:
    """Prefer V2 facts for new runs and fall back to retained V1 history."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._v1 = SQLiteExecutionEventStore(db_path)
        self._v2 = SQLiteExecutionJournal(db_path)
        self._runs = RunRegistry(db_path)

    def list_events(
        self,
        run_id: str,
        *,
        owner: str,
        after_seq: int = 0,
        limit: int | None = None,
    ) -> ExecutionEventPage | None:
        binding = self._binding(run_id, owner)
        if binding is None:
            return self._v1.list_events(
                run_id,
                owner=owner,
                after_seq=after_seq,
                limit=limit,
            )
        page = self._v2.list_events(
            binding,
            owner=owner,
            after_seq=after_seq,
            limit=limit,
        )
        assert page is not None
        return ExecutionEventPage(
            items=tuple(_project_event(item, run_id) for item in page.items),
            next_after_seq=page.next_after_seq,
            has_more=page.has_more,
        )

    def get_event(
        self,
        run_id: str,
        event_id: str,
        *,
        owner: str,
    ) -> ExecutionEventV1 | None:
        binding = self._binding(run_id, owner)
        if binding is None:
            return self._v1.get_event(run_id, event_id, owner=owner)
        event = self._v2.get_event(binding, event_id, owner=owner)
        return None if event is None else _project_event(event, run_id)

    def get_projection(self, run_id: str, *, owner: str):
        binding = self._binding(run_id, owner)
        if binding is None:
            return self._v1.get_projection(run_id, owner=owner)
        events: list[ExecutionEventV1] = []
        cursor = 0
        while True:
            page = self._v2.list_events(
                binding,
                owner=owner,
                after_seq=cursor,
                limit=200,
            )
            assert page is not None
            events.extend(_project_event(item, run_id) for item in page.items)
            cursor = page.next_after_seq
            if not page.has_more:
                break
        return fold_execution_events(run_id, events)

    def _binding(self, run_id: str, owner: str) -> str | None:
        record = self._runs.get_run(run_id, owner=owner)
        if record is None:
            return None
        execution_id = record.request_info.execution_id
        if execution_id is None:
            return None
        try:
            self._v2.get_projection(execution_id, owner=owner)
        except ExecutionJournalNotFoundError:
            return None
        return execution_id


def _project_event(event: ExecutionEventV2, run_id: str) -> ExecutionEventV1:
    kind = _TYPE_MAP.get(event.type.value, f"v2.{event.type.value}")
    payload = _payload(event, kind)
    target = (
        None if event.target is None else event.target.model_dump(mode="json")
    )
    return parse_execution_event(
        {
            "schema_version": 1,
            "event_id": event.event_id,
            "run_id": run_id,
            "seq": event.seq,
            "idempotency_key": event.idempotency_key,
            "occurred_at": event.occurred_at,
            "kind": kind,
            "status": _status(event.status.value),
            "summary": event.summary.model_dump(mode="json"),
            "payload": payload,
            "ignorable": kind.startswith("v2."),
            "target": target,
        }
    )


def _payload(event: ExecutionEventV2, kind: str) -> dict[str, Any]:
    source = event.public_payload.model_dump(mode="json", exclude_none=True)
    if kind in {
        "run.started",
        "run.accepted",
        "run.waiting_input",
        "run.resumed",
        "run.succeeded",
        "run.cancelled",
    }:
        return {}
    if kind == "run.failed":
        return {
            "code": str(
                source.get("code") or event.type.value.replace(".", "_")
            ),
            "retryable": bool(source.get("retryable", False)),
        }
    if kind in {"phase.started", "phase.completed"}:
        return {
            "phase": str(source.get("phase") or event.span_id),
            "label_key": event.summary.key,
        }
    if kind == "phase.progress":
        return {
            "phase": str(source.get("phase") or event.span_id),
            "completed": int(source.get("completed", 0)),
            "total": max(1, int(source.get("total", 1))),
        }
    if kind == "phase.failed":
        return {
            "phase": str(source.get("phase") or event.span_id),
            "code": str(source.get("code") or "phase_failed"),
        }
    if kind in {"tool.started", "tool.completed", "tool.failed"}:
        payload: dict[str, Any] = {
            "tool_key": str(source.get("operation_key") or "operation"),
            "call_id": event.work_unit_id or event.span_id,
        }
        if kind != "tool.started":
            payload["duration_ms"] = int(source.get("duration_ms", 0))
        if kind == "tool.failed":
            payload["code"] = str(source.get("code") or "operation_failed")
        return payload
    if kind == "todo.snapshot":
        return {
            "items": [
                item
                for item in source.get("items", [])
                if item.get("status")
                in {"pending", "in_progress", "completed"}
            ]
        }
    if kind in {"reasoning.summary", "decision.note"}:
        return {"text": source["text"]}
    if kind == "input.required":
        return {"surface_id": source["surface_id"], "widget": source["widget"]}
    if kind == "input.resolved":
        return {
            "surface_id": source["surface_id"],
            "outcome": source["outcome"],
        }
    if kind == "artifact.published":
        return {
            "name": source["name"],
            "media_type": source["media_type"],
            "size_bytes": source["size_bytes"],
        }
    if kind == "tracking.degraded":
        return {
            "code": str(source.get("code") or "tracking_degraded"),
            "retryable": bool(source.get("retryable", False)),
        }
    return {}


def _status(value: str) -> str:
    if value in {
        "admitted",
        "queued",
        "dispatching",
        "pending",
        "submitted",
        "acknowledged",
    }:
        return "queued"
    if value == "waiting_input":
        return "waiting"
    if value in {"succeeded", "partial", "skipped"}:
        return "succeeded"
    if value in {"failed", "timed_out"}:
        return "failed"
    if value == "cancelled":
        return "cancelled"
    return "running"


__all__ = ["V1ExecutionCompatibilityReader"]
