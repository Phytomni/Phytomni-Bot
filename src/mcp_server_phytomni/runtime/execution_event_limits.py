# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Central production limits for public execution events and replay."""

from __future__ import annotations

from dataclasses import dataclass

EXECUTION_CONTENT_DELTA_MAX_BYTES = 16_384
EXECUTION_STREAM_HEARTBEAT_SECONDS = 15


class ExecutionEventLimitError(ValueError):
    """A caller exceeded a public execution-event contract limit."""


@dataclass(frozen=True, slots=True)
class ExecutionEventLimits:
    """Finite resource limits shared by storage, transport, and decoders."""

    max_event_bytes: int
    max_summary_chars: int
    max_todo_items: int
    default_page_size: int
    max_page_size: int
    max_events_per_run: int
    max_live_backlog: int
    progress_coalesce_ms: int

    def validate_summary(self, value: str) -> None:
        """Reject a user-visible summary over the character limit."""
        if len(value) > self.max_summary_chars:
            raise ExecutionEventLimitError("summary_too_large")

    def validate_event_size(self, size_bytes: int) -> None:
        """Reject a serialized event over the byte limit."""
        if size_bytes < 0 or size_bytes > self.max_event_bytes:
            raise ExecutionEventLimitError("event_payload_too_large")

    def validate_todo_count(self, count: int) -> None:
        """Reject invalid or excessive atomic Todo snapshots."""
        if count < 0 or count > self.max_todo_items:
            raise ExecutionEventLimitError("too_many_todo_items")

    def resolve_page_size(self, requested: int | None) -> int:
        """Resolve an optional bounded event-history page size."""
        size = self.default_page_size if requested is None else requested
        if size < 1 or size > self.max_page_size:
            raise ExecutionEventLimitError("invalid_page_size")
        return size

    def should_coalesce_progress(
        self, previous_millis: int, current_millis: int
    ) -> bool:
        """Whether a newer informational progress event is too soon."""
        return current_millis - previous_millis < self.progress_coalesce_ms


@dataclass(frozen=True, slots=True)
class ExecutionTraceDetailLimits:
    """Finite bounds for grouped operation records and redacted logs."""

    max_operations_per_run: int
    max_attempt_history_per_operation: int
    max_detail_fields_per_operation: int
    liveness_coalesce_ms: int
    max_execution_log_bytes: int

    def validate_operation_count(self, count: int) -> None:
        """Reject an invalid or excessive grouped-operation count."""
        if count < 0 or count > self.max_operations_per_run:
            raise ExecutionEventLimitError("too_many_operation_records")

    def validate_attempt_history_count(self, count: int) -> None:
        """Reject an invalid or excessive attempt-history count."""
        if count < 0 or count > self.max_attempt_history_per_operation:
            raise ExecutionEventLimitError("too_many_operation_attempts")

    def validate_detail_field_count(self, count: int) -> None:
        """Reject an invalid or excessive public detail-field count."""
        if count < 0 or count > self.max_detail_fields_per_operation:
            raise ExecutionEventLimitError("too_many_operation_detail_fields")

    def validate_execution_log_size(self, size_bytes: int) -> None:
        """Reject an invalid or oversized redacted execution log."""
        if size_bytes < 0 or size_bytes > self.max_execution_log_bytes:
            raise ExecutionEventLimitError("execution_log_too_large")

    def should_coalesce_liveness(
        self, previous_millis: int, current_millis: int
    ) -> bool:
        """Whether a quiet-operation observation is too soon to persist."""
        return current_millis - previous_millis < self.liveness_coalesce_ms


DEFAULT_EXECUTION_EVENT_LIMITS = ExecutionEventLimits(
    max_event_bytes=16_384,
    max_summary_chars=512,
    max_todo_items=100,
    default_page_size=50,
    max_page_size=200,
    max_events_per_run=10_000,
    max_live_backlog=1_000,
    progress_coalesce_ms=500,
)

DEFAULT_EXECUTION_TRACE_DETAIL_LIMITS = ExecutionTraceDetailLimits(
    max_operations_per_run=256,
    max_attempt_history_per_operation=8,
    max_detail_fields_per_operation=16,
    liveness_coalesce_ms=30_000,
    max_execution_log_bytes=1_048_576,
)
