# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Project the existing AG-UI progress vocabulary onto A2A statuses."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

from a2a.types import StreamResponse, TaskState

from ...mcp.result_formatting import AguiEvent
from .events import build_status_update

__all__ = ["project_agui_progress"]

_PROGRESS_EVENT = "phyto.progress"


def _status_metadata(value: Any) -> dict[str, Any] | None:
    """Extract progress fields without coercing their values."""
    if not isinstance(value, Mapping):
        return None
    metadata = {
        key: value[key]
        for key in ("phase", "current", "total", "detail")
        if key in value
    }
    return metadata or None


async def project_agui_progress(
    events: AsyncIterator[AguiEvent],
    *,
    task_id: str,
    context_id: str,
) -> AsyncIterator[StreamResponse]:
    """Yield A2A lifecycle updates for an existing AG-UI event stream.

    ``StepStarted`` and ``phyto.progress`` remain status metadata; neither
    event is allowed to redefine the A2A lifecycle enum. Text and artifact
    frames are intentionally ignored here and are projected by the stream
    transport layer in the next phase.
    """
    submitted = False

    def ensure_submitted() -> StreamResponse | None:
        nonlocal submitted
        if submitted:
            return None
        submitted = True
        return build_status_update(
            task_id,
            context_id,
            TaskState.TASK_STATE_SUBMITTED,
        )

    async for event in events:
        if event.type == "RunStarted":
            update = ensure_submitted()
            if update is not None:
                yield update
            continue

        if event.type == "StepStarted":
            update = ensure_submitted()
            if update is not None:
                yield update
            phase = event.data.get("step_name")
            metadata = {"phase": phase} if isinstance(phase, str) else None
            yield build_status_update(
                task_id,
                context_id,
                TaskState.TASK_STATE_WORKING,
                metadata=metadata,
            )
            continue

        if (
            event.type == "Custom"
            and event.data.get("name") == _PROGRESS_EVENT
        ):
            update = ensure_submitted()
            if update is not None:
                yield update
            metadata = _status_metadata(event.data.get("value"))
            if metadata is not None:
                yield build_status_update(
                    task_id,
                    context_id,
                    TaskState.TASK_STATE_WORKING,
                    metadata=metadata,
                )
            continue

        if event.type == "RunError":
            update = ensure_submitted()
            if update is not None:
                yield update
            error_metadata = {
                key: event.data[key]
                for key in ("code", "message")
                if key in event.data
            }
            yield build_status_update(
                task_id,
                context_id,
                TaskState.TASK_STATE_FAILED,
                metadata=error_metadata or None,
            )
            return

        if event.type == "RunFinished":
            update = ensure_submitted()
            if update is not None:
                yield update
            yield build_status_update(
                task_id,
                context_id,
                TaskState.TASK_STATE_COMPLETED,
            )
            return
