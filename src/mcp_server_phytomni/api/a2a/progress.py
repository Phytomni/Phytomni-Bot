# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Project the existing AG-UI progress vocabulary onto A2A statuses."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

from a2a.types import StreamResponse, TaskState

from ...mcp.result_formatting import AguiEvent
from .events import build_status_update

__all__ = ["A2AProgressProjector", "project_agui_progress"]

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


@dataclass
class A2AProgressProjector:
    """Incrementally map AG-UI lifecycle events to A2A status wrappers."""

    task_id: str
    context_id: str
    submitted: bool = False
    terminal: bool = False

    def _ensure_submitted(self) -> list[StreamResponse]:
        if self.submitted:
            return []
        self.submitted = True
        return [
            build_status_update(
                self.task_id,
                self.context_id,
                TaskState.TASK_STATE_SUBMITTED,
            )
        ]

    def project(self, event: AguiEvent) -> list[StreamResponse]:
        """Project one event, preserving lifecycle and metadata ordering."""
        updates: list[StreamResponse] = []
        if not self.terminal:
            if event.type == "RunStarted":
                updates.extend(self._ensure_submitted())
            elif event.type == "StepStarted":
                updates.extend(self._ensure_submitted())
                phase = event.data.get("step_name")
                metadata = {"phase": phase} if isinstance(phase, str) else None
                updates.append(
                    build_status_update(
                        self.task_id,
                        self.context_id,
                        TaskState.TASK_STATE_WORKING,
                        metadata=metadata,
                    )
                )
            elif (
                event.type == "Custom"
                and event.data.get("name") == _PROGRESS_EVENT
            ):
                updates.extend(self._ensure_submitted())
                metadata = _status_metadata(event.data.get("value"))
                if metadata is not None:
                    updates.append(
                        build_status_update(
                            self.task_id,
                            self.context_id,
                            TaskState.TASK_STATE_WORKING,
                            metadata=metadata,
                        )
                    )
            elif event.type in {"RunError", "RunFinished"}:
                updates.extend(self._ensure_submitted())
                state = (
                    TaskState.TASK_STATE_FAILED
                    if event.type == "RunError"
                    else TaskState.TASK_STATE_COMPLETED
                )
                metadata = None
                if event.type == "RunError":
                    metadata = {
                        key: event.data[key]
                        for key in ("code", "message")
                        if key in event.data
                    }
                updates.append(
                    build_status_update(
                        self.task_id,
                        self.context_id,
                        state,
                        metadata=metadata or None,
                    )
                )
                self.terminal = True
        return updates


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
    projector = A2AProgressProjector(task_id, context_id)
    async for event in events:
        for update in projector.project(event):
            yield update
