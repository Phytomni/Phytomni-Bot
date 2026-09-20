# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared V2 facts for checkpoints and owner-authorized input actions."""

from __future__ import annotations

from collections.abc import Mapping

from .execution_instrumentation_v2 import current_execution_boundary
from .execution_journal_v2 import parse_execution_event_intent_v2


def record_input_required(
    *,
    surface_id: str,
    widget: str,
    action_revision: int | None = None,
) -> None:
    """Publish one durable checkpoint interrupt after its surface persists."""
    revision = _action_revision(action_revision)
    if revision is None:
        return
    _append_input_required(surface_id, widget, revision)


def record_projected_input_required(value: Mapping[str, object]) -> None:
    """Find one already-validated public A2UI surface in a projection."""
    interrupt = value.get("interrupt")
    draft = interrupt.get("draft") if isinstance(interrupt, Mapping) else None
    candidates = (
        value.get("a2ui"),
        interrupt.get("a2ui") if isinstance(interrupt, Mapping) else None,
        draft.get("a2ui") if isinstance(draft, Mapping) else None,
    )
    surface = next(
        (
            candidate
            for candidate in candidates
            if isinstance(candidate, Mapping)
        ),
        None,
    )
    if surface is None:
        return
    surface_id = surface.get("surface_id")
    widget = surface.get("widget")
    if isinstance(surface_id, str) and isinstance(widget, str):
        record_input_required(surface_id=surface_id, widget=widget)


def _append_input_required(
    surface_id: str,
    widget: str,
    action_revision: int,
) -> None:
    boundary = current_execution_boundary()
    if boundary is None:
        return
    boundary.services.journal.append(
        boundary.context.execution_id,
        owner=boundary.context.owner_ref,
        intent=parse_execution_event_intent_v2(
            {
                "type": "input.required",
                "status": "waiting_input",
                "source": "checkpoint",
                "span_id": boundary.context.current_span_id,
                "parent_span_id": boundary.context.parent_span_id,
                "attempt": 1,
                "summary": {
                    "key": "input.required",
                    "text": "Input Required",
                },
                "public_payload": {
                    "surface_id": surface_id,
                    "widget": widget,
                    "action_revision": action_revision,
                },
                "idempotency_key": (
                    f"input:{surface_id}:required:{action_revision}"
                ),
            }
        ),
    )


def _action_revision(explicit: int | None) -> int | None:
    if explicit is not None:
        return explicit
    boundary = current_execution_boundary()
    if boundary is None:
        return None
    record = boundary.services.reservations.get(
        owner=boundary.context.owner_ref,
        execution_id=boundary.context.execution_id,
    )
    return record.supervisor_revision
