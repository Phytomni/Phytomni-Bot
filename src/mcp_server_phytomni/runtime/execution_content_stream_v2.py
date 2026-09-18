# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Bounded process-local delivery for transient public content deltas.

Durable message snapshots/completion remain in the execution journal.  This
buffer only improves live delivery and therefore never allocates journal
sequence numbers.
"""

from __future__ import annotations

import os
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .execution_event_limits import EXECUTION_CONTENT_DELTA_MAX_BYTES


class ExecutionContentConflictError(ValueError):
    """A producer attempted to publish a stale revision or offset."""


class ExecutionContentDeltaV2(BaseModel):
    """One transient, resumable output fragment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2] = 2
    execution_id: str = Field(min_length=1, max_length=128)
    output_revision: int = Field(ge=1)
    offset: int = Field(ge=1)
    delta: str = Field(
        min_length=1,
        max_length=EXECUTION_CONTENT_DELTA_MAX_BYTES,
    )


@dataclass(slots=True)
class ExecutionContentStreamV2:
    """Thread-safe bounded content buffer scoped by owner and execution."""

    max_frames_per_execution: int = 256
    _frames: dict[tuple[str, str], deque[ExecutionContentDeltaV2]] = field(
        default_factory=lambda: defaultdict(deque), init=False
    )
    _lock: threading.RLock = field(
        default_factory=threading.RLock,
        init=False,
        repr=False,
    )

    def publish(
        self,
        *,
        owner: str,
        execution_id: str,
        output_revision: int,
        offset: int,
        delta: str,
    ) -> ExecutionContentDeltaV2:
        """Publish a monotonic fragment without creating a durable fact."""
        if not owner:
            raise ValueError("owner is required")
        if len(delta.encode("utf-8")) > EXECUTION_CONTENT_DELTA_MAX_BYTES:
            raise ValueError("content delta exceeds size limit")
        frame = ExecutionContentDeltaV2(
            execution_id=execution_id,
            output_revision=output_revision,
            offset=offset,
            delta=delta,
        )
        key = (owner, execution_id)
        with self._lock:
            frames = self._frames[key]
            if frames:
                latest = frames[-1]
                if output_revision < latest.output_revision or (
                    output_revision == latest.output_revision
                    and offset <= latest.offset
                ):
                    raise ExecutionContentConflictError(
                        "content revision or offset regressed"
                    )
            frames.append(frame)
            while len(frames) > self.max_frames_per_execution:
                frames.popleft()
        return frame

    def publish_next(
        self,
        *,
        owner: str,
        execution_id: str,
        output_revision: int,
        delta: str,
    ) -> ExecutionContentDeltaV2:
        """Append one fragment and allocate its Unicode-scalar end offset."""
        key = (owner, execution_id)
        with self._lock:
            frames = self._frames[key]
            prior_offset = (
                frames[-1].offset
                if frames and frames[-1].output_revision == output_revision
                else 0
            )
            return self.publish(
                owner=owner,
                execution_id=execution_id,
                output_revision=output_revision,
                # Durable message snapshots, Go's rune counts, and the Web
                # client all define output offsets in Unicode scalar values.
                # Keep the byte limit above for transport safety, but never
                # mix that byte count into the resumable content cursor.
                offset=prior_offset + len(delta),
                delta=delta,
            )

    def list_after(
        self,
        *,
        owner: str,
        execution_id: str,
        output_revision: int,
        after_offset: int,
    ) -> tuple[ExecutionContentDeltaV2, ...]:
        """Return resumable frames, switching to a newer revision if present."""
        with self._lock:
            frames = tuple(self._frames.get((owner, execution_id), ()))
        if not frames:
            return ()
        latest_revision = frames[-1].output_revision
        accepted_revision = max(output_revision, latest_revision)
        accepted_offset = (
            after_offset if accepted_revision == output_revision else 0
        )
        return tuple(
            frame
            for frame in frames
            if frame.output_revision == accepted_revision
            and frame.offset > accepted_offset
        )

    def clear(self, *, owner: str, execution_id: str) -> None:
        """Release transient state after terminal delivery or deletion."""
        with self._lock:
            self._frames.pop((owner, execution_id), None)


_STREAMS: dict[str, ExecutionContentStreamV2] = {}
_STREAMS_LOCK = threading.Lock()


def execution_content_stream_for_db(db_path: str) -> ExecutionContentStreamV2:
    """Return the process-local stream shared by one runtime database."""
    key = os.path.normcase(os.path.abspath(db_path))
    with _STREAMS_LOCK:
        return _STREAMS.setdefault(key, ExecutionContentStreamV2())


__all__ = [
    "ExecutionContentConflictError",
    "ExecutionContentDeltaV2",
    "ExecutionContentStreamV2",
    "execution_content_stream_for_db",
]
