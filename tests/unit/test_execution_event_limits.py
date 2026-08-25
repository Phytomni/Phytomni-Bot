# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Boundary tests for centralized public execution-event limits."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

FIXTURES = (
    Path(__file__).parents[2]
    / "docs"
    / "contracts"
    / "execution-events"
    / "v1"
    / "fixtures.json"
)
TRACE_DETAIL_FIXTURES = (
    Path(__file__).parents[2]
    / "docs"
    / "contracts"
    / "execution-trace-detail"
    / "v1"
    / "fixtures.json"
)


def test_production_defaults_match_the_shared_contract() -> None:
    from mcp_server_phytomni.runtime.execution_event_limits import (
        DEFAULT_EXECUTION_EVENT_LIMITS,
    )

    fixture = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert (
        asdict(DEFAULT_EXECUTION_EVENT_LIMITS) == fixture["contract"]["limits"]
    )


def test_summary_boundary_accepts_limit_and_rejects_limit_plus_one() -> None:
    from mcp_server_phytomni.runtime.execution_event_limits import (
        DEFAULT_EXECUTION_EVENT_LIMITS,
        ExecutionEventLimitError,
    )

    limits = DEFAULT_EXECUTION_EVENT_LIMITS
    limits.validate_summary("x" * limits.max_summary_chars)
    with pytest.raises(ExecutionEventLimitError, match="summary_too_large"):
        limits.validate_summary("x" * (limits.max_summary_chars + 1))


def test_event_byte_boundary_accepts_limit_and_rejects_limit_plus_one() -> (
    None
):
    from mcp_server_phytomni.runtime.execution_event_limits import (
        DEFAULT_EXECUTION_EVENT_LIMITS,
        ExecutionEventLimitError,
    )

    limits = DEFAULT_EXECUTION_EVENT_LIMITS
    limits.validate_event_size(limits.max_event_bytes)
    with pytest.raises(
        ExecutionEventLimitError, match="event_payload_too_large"
    ):
        limits.validate_event_size(limits.max_event_bytes + 1)


def test_page_and_todo_boundaries_fail_closed() -> None:
    from mcp_server_phytomni.runtime.execution_event_limits import (
        DEFAULT_EXECUTION_EVENT_LIMITS,
        ExecutionEventLimitError,
    )

    limits = DEFAULT_EXECUTION_EVENT_LIMITS
    assert limits.resolve_page_size(None) == limits.default_page_size
    assert (
        limits.resolve_page_size(limits.max_page_size) == limits.max_page_size
    )
    limits.validate_todo_count(limits.max_todo_items)
    with pytest.raises(ExecutionEventLimitError, match="invalid_page_size"):
        limits.resolve_page_size(limits.max_page_size + 1)
    with pytest.raises(ExecutionEventLimitError, match="too_many_todo_items"):
        limits.validate_todo_count(limits.max_todo_items + 1)


def test_progress_coalescing_boundary_is_deterministic() -> None:
    from mcp_server_phytomni.runtime.execution_event_limits import (
        DEFAULT_EXECUTION_EVENT_LIMITS,
    )

    limits = DEFAULT_EXECUTION_EVENT_LIMITS
    assert limits.should_coalesce_progress(1_000, 1_499) is True
    assert limits.should_coalesce_progress(1_000, 1_500) is False


def test_trace_detail_defaults_match_the_shared_contract() -> None:
    from mcp_server_phytomni.runtime import execution_event_limits

    limits = getattr(
        execution_event_limits,
        "DEFAULT_EXECUTION_TRACE_DETAIL_LIMITS",
        None,
    )
    assert limits is not None, "central execution trace limits are missing"
    fixture = json.loads(TRACE_DETAIL_FIXTURES.read_text(encoding="utf-8"))

    assert asdict(limits) == fixture["contract"]["limits"]
    assert asdict(limits) == {
        "max_operations_per_run": 256,
        "max_attempt_history_per_operation": 8,
        "max_detail_fields_per_operation": 16,
        "liveness_coalesce_ms": 30_000,
        "max_execution_log_bytes": 1_048_576,
    }


def test_trace_detail_limit_boundaries_fail_closed() -> None:
    from mcp_server_phytomni.runtime.execution_event_limits import (
        DEFAULT_EXECUTION_TRACE_DETAIL_LIMITS,
        ExecutionEventLimitError,
    )

    limits = DEFAULT_EXECUTION_TRACE_DETAIL_LIMITS
    limits.validate_operation_count(limits.max_operations_per_run)
    limits.validate_attempt_history_count(
        limits.max_attempt_history_per_operation
    )
    limits.validate_detail_field_count(limits.max_detail_fields_per_operation)
    limits.validate_execution_log_size(limits.max_execution_log_bytes)

    for callback, reason in (
        (
            lambda: limits.validate_operation_count(
                limits.max_operations_per_run + 1
            ),
            "too_many_operation_records",
        ),
        (
            lambda: limits.validate_attempt_history_count(
                limits.max_attempt_history_per_operation + 1
            ),
            "too_many_operation_attempts",
        ),
        (
            lambda: limits.validate_detail_field_count(
                limits.max_detail_fields_per_operation + 1
            ),
            "too_many_operation_detail_fields",
        ),
        (
            lambda: limits.validate_execution_log_size(
                limits.max_execution_log_bytes + 1
            ),
            "execution_log_too_large",
        ),
    ):
        with pytest.raises(ExecutionEventLimitError, match=reason):
            callback()


def test_trace_detail_liveness_coalescing_boundary_is_deterministic() -> None:
    from mcp_server_phytomni.runtime.execution_event_limits import (
        DEFAULT_EXECUTION_TRACE_DETAIL_LIMITS,
    )

    limits = DEFAULT_EXECUTION_TRACE_DETAIL_LIMITS
    assert limits.should_coalesce_liveness(1_000, 30_999) is True
    assert limits.should_coalesce_liveness(1_000, 31_000) is False
