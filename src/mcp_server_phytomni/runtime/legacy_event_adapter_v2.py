# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Translate approved legacy public producers into canonical V2 facts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .execution_events import ExecutionEventIntent
from .execution_instrumentation_v2 import current_execution_boundary
from .execution_journal_v2 import parse_execution_event_intent_v2

_TYPE_MAP = {
    "phase.started": "span.started",
    "phase.progress": "span.progress",
    "phase.completed": "span.succeeded",
    "phase.failed": "span.failed",
    "todo.snapshot": "todo.snapshot",
    "reasoning.summary": "reasoning.summary",
    "decision.note": "decision.note",
}
_STATUS_MAP = {
    "queued": "queued",
    "running": "running",
    "waiting": "waiting_input",
    "succeeded": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
}


def adapt_legacy_event_intent_to_v2(intent: ExecutionEventIntent) -> bool:
    """Mirror one finite V1 producer through the active execution boundary."""
    boundary = current_execution_boundary()
    event_type = _TYPE_MAP.get(intent.kind)
    if boundary is None or event_type is None:
        return False
    payload = intent.payload.model_dump(mode="json", exclude_none=True)
    public_payload = _public_payload(intent.kind, payload)
    if public_payload is None:
        return False
    stable_key = intent.idempotency_key or _stable_key(
        event_type, public_payload
    )
    boundary.services.journal.append(
        boundary.context.execution_id,
        owner=boundary.context.owner_ref,
        intent=parse_execution_event_intent_v2(
            {
                "type": event_type,
                "status": _STATUS_MAP[intent.status],
                "source": "compatibility",
                "span_id": boundary.context.current_span_id,
                "parent_span_id": boundary.context.parent_span_id,
                "attempt": 1,
                "summary": intent.summary.model_dump(mode="json"),
                "public_payload": public_payload,
                "idempotency_key": f"adapted-v2:{stable_key}",
            }
        ),
    )
    return True


def adapt_agui_custom_to_v2(name: str, value: Any) -> bool:
    """Recognize only explicitly public AG-UI custom event names."""
    if not isinstance(value, Mapping):
        return False
    if name == "phyto.progress":
        phase = value.get("phase")
        completed = value.get("completed", value.get("current"))
        total = value.get("total")
        if (
            not isinstance(phase, str)
            or isinstance(completed, bool)
            or not isinstance(completed, int)
            or isinstance(total, bool)
            or not isinstance(total, int)
            or total < 1
            or completed < 0
        ):
            return False
        return _append_custom(
            event_type="span.progress",
            payload={"phase": phase, "completed": completed, "total": total},
        )
    if name in {"phyto.reasoning_summary", "phyto.decision_note"}:
        text = value.get("text")
        if not isinstance(text, str) or not text or len(text) > 512:
            return False
        return _append_custom(
            event_type=(
                "reasoning.summary"
                if name == "phyto.reasoning_summary"
                else "decision.note"
            ),
            payload={"text": text},
        )
    return False


def _append_custom(*, event_type: str, payload: Mapping[str, object]) -> bool:
    boundary = current_execution_boundary()
    if boundary is None:
        return False
    stable_key = _stable_key(event_type, payload)
    boundary.services.journal.append(
        boundary.context.execution_id,
        owner=boundary.context.owner_ref,
        intent=parse_execution_event_intent_v2(
            {
                "type": event_type,
                "status": "running",
                "source": "compatibility",
                "span_id": boundary.context.current_span_id,
                "parent_span_id": boundary.context.parent_span_id,
                "attempt": 1,
                "summary": {
                    "key": event_type,
                    "text": event_type.replace(".", " ")
                    .replace("_", " ")
                    .title(),
                },
                "public_payload": dict(payload),
                "idempotency_key": f"adapted-v2:{stable_key}",
            }
        ),
    )
    return True


def _public_payload(
    kind: str, payload: Mapping[str, object]
) -> dict[str, object] | None:
    if kind == "phase.started" or kind == "phase.completed":
        phase = payload.get("phase")
        return {"phase": phase} if isinstance(phase, str) else None
    if kind == "phase.progress":
        phase = payload.get("phase")
        completed = payload.get("completed")
        total = payload.get("total")
        if (
            isinstance(phase, str)
            and isinstance(completed, int)
            and not isinstance(completed, bool)
            and isinstance(total, int)
            and not isinstance(total, bool)
        ):
            return {"phase": phase, "completed": completed, "total": total}
        return None
    if kind == "phase.failed":
        code = payload.get("code")
        return {
            "code": code if isinstance(code, str) else "phase_failed",
            "retryable": False,
        }
    if kind == "todo.snapshot":
        items = payload.get("items")
        return {"items": items} if isinstance(items, list) else None
    if kind in {"reasoning.summary", "decision.note"}:
        text = payload.get("text")
        return {"text": text} if isinstance(text, str) else None
    return None


def _stable_key(kind: str, payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        {"kind": kind, "payload": payload},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
