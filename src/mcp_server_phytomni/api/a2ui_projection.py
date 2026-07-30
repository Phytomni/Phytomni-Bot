# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pure HTTP projections for Chat and Review A2UI runs.

The lifecycle runtime owns graph execution, persistence, and concurrency.
This module keeps the consumer-visible interrupt, result, and submitted-value
shapes in one stateless surface so those contracts can evolve independently
of the pause/resume orchestration.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from ..agents.shared.a2ui import (
    A2uiSurfaceValidationError,
    build_review_a2ui_interrupt,
    build_submitted_value,
    validate_a2ui_surface,
)
from ..agents.shared.intermediate_state import merge_intermediate_state
from ..mcp.result_formatting import build_tool_result_envelope
from .lifecycle_contract import build_agent_run_response


class ReviewSurfaceProjectionError(RuntimeError):
    """Raised when Review cannot produce a valid public pause surface."""


def chat_interrupt_result(interrupt: Mapping[str, Any]) -> dict[str, Any]:
    """Return the registry result for a paused Chat A2UI run."""
    return {
        "interrupt": dict(interrupt),
        "status": "input_required",
    }


def review_interrupt_result(interrupt: Mapping[str, Any]) -> dict[str, Any]:
    """Return the registry result for a paused Review A2UI run."""
    return {
        "interrupt": dict(interrupt),
        "status": "input_required",
    }


def submitted_a2ui_value(
    prior_surface: Mapping[str, Any],
    resume_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the submitted downlink echoed in a terminal result."""
    accepted = resume_payload.get("accepted")
    if not isinstance(accepted, bool):
        approved = resume_payload.get("approved")
        if (
            isinstance(approved, bool)
            and resume_payload.get("cancelled") is not True
            and "fields" not in resume_payload
            and "selected" not in resume_payload
        ):
            accepted = approved
    fields = resume_payload.get("fields")
    return build_submitted_value(
        prior_surface,
        accepted=accepted if isinstance(accepted, bool) else None,
        cancelled=(True if resume_payload.get("cancelled") is True else None),
        fields=fields if isinstance(fields, Mapping) else None,
        selected=resume_payload.get("selected"),
    )


def format_chat_result(
    final_state: Mapping[str, Any],
    *,
    prior_surface: Mapping[str, Any],
    resume_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Format a terminal Chat A2UI graph state for registry storage."""
    raw_payload = merge_intermediate_state(
        final_state,
        final_response_key="response",
    )
    envelope = build_tool_result_envelope("ChatAgent", raw_payload)
    return {
        "formatted": asdict(envelope.formatted),
        "execution": asdict(envelope.execution),
        "raw": envelope.raw,
        "a2ui": submitted_a2ui_value(prior_surface, resume_payload),
    }


def format_review_result(
    final_state: Mapping[str, Any],
    *,
    arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Format a terminal ReviewAgent graph state for registry storage."""
    raw_payload = merge_intermediate_state(final_state)
    envelope = build_tool_result_envelope(
        "ReviewAgent",
        raw_payload,
        arguments=arguments,
    )
    return {
        "formatted": asdict(envelope.formatted),
        "execution": asdict(envelope.execution),
        "raw": envelope.raw,
    }


def chat_interrupt_body(
    *, run_id: str, interrupt: Mapping[str, Any]
) -> dict[str, Any]:
    """Return the HTTP body for a paused Chat A2UI run."""
    return {
        "id": run_id,
        "run_id": run_id,
        "object": "agent.run",
        "agent": "chat",
        "status": "input_required",
        "task_ids": [],
        "interrupt": dict(interrupt),
    }


def review_interrupt_body(
    *, thread_id: str, interrupt: Mapping[str, Any]
) -> dict[str, Any]:
    """Return the HTTP body for a paused ReviewAgent run."""
    body = build_agent_run_response(
        run_id=thread_id,
        agent="review",
        status="input_required",
        task_ids=(),
        result=review_interrupt_result(interrupt),
        persisted=True,
    )
    # Keep the legacy top-level alias for direct callers. The factory's
    # canonicalizer also accepts this shape and preserves the old HTTP body.
    body["interrupt"] = dict(interrupt)
    return body


def project_review_interrupt(
    interrupt: Mapping[str, Any],
) -> dict[str, Any]:
    """Build and validate a Review surface regardless of feature flags."""
    try:
        projected = build_review_a2ui_interrupt(interrupt)
        draft = projected.get("draft")
        surface = draft.get("a2ui") if isinstance(draft, Mapping) else None
        if not isinstance(surface, Mapping):
            raise ReviewSurfaceProjectionError(
                "review surface projection failed"
            )
        validate_a2ui_surface(surface)
        return projected
    except ReviewSurfaceProjectionError:
        raise
    except A2uiSurfaceValidationError as exc:
        raise ReviewSurfaceProjectionError(
            "review surface validation failed"
        ) from exc
    except Exception as exc:
        raise ReviewSurfaceProjectionError(
            "review surface projection failed"
        ) from exc


__all__ = [
    "chat_interrupt_body",
    "chat_interrupt_result",
    "format_chat_result",
    "format_review_result",
    "project_review_interrupt",
    "ReviewSurfaceProjectionError",
    "review_interrupt_body",
    "review_interrupt_result",
    "submitted_a2ui_value",
]
