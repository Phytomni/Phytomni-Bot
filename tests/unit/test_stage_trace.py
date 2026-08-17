# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the payload-free DataAgent stage trace."""

# pylint: disable=protected-access

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field

import pytest

from mcp_server_phytomni.runtime.request_context import request_context
from mcp_server_phytomni.runtime.stage_trace import (
    _STAGE_FAILURE_ATTRIBUTE,
    DataStage,
    StageTraceEvent,
    classify_stage_error,
    current_stage_trace,
    stage_failure_from_exception,
    trace_data_stage,
)

pytestmark = pytest.mark.unit


@dataclass
class CapturingStageTraceSink:
    """Small structural sink used to inspect emitted safe events."""

    events: list[StageTraceEvent] = field(default_factory=list)

    def emit(self, event: StageTraceEvent) -> None:
        """Capture the event without changing it."""
        self.events.append(event)


def test_data_stage_taxonomy_is_exact() -> None:
    """The trace has one stable name for each DataAgent boundary."""
    assert tuple(stage.value for stage in DataStage) == (
        "native_request",
        "data_rewrite",
        "nl2sql_request",
        "database_query",
        "result_format",
        "run_persist",
    )


async def test_stage_trace_records_only_safe_fields() -> None:
    """Success events contain bounded metadata and no query payload."""
    sink = CapturingStageTraceSink()
    with request_context("user-1", "request-data-1"):
        async with trace_data_stage(
            DataStage.DATABASE_QUERY,
            dependency="gaussdb",
            sink=sink,
        ):
            await asyncio.sleep(0)

        event = asdict(sink.events[0])
        assert set(event) == {
            "request_id",
            "agent",
            "stage",
            "dependency",
            "duration_ms",
            "error_code",
            "error_class",
            "final_http_status",
        }
        assert event["request_id"] == "request-data-1"
        assert event["agent"] == "data"
        assert event["stage"] == "database_query"
        assert event["duration_ms"] >= 0
        assert "SELECT" not in json.dumps(event)
        assert current_stage_trace() == tuple(sink.events)

    assert current_stage_trace() == ()


async def test_stage_trace_classifies_failure_without_message() -> None:
    """Failure events retain type/status but never exception text."""

    class FakeUpstreamError(RuntimeError):
        """Failure carrying a payload that must not enter the trace."""

    sink = CapturingStageTraceSink()
    with request_context("user-1", "request-data-2"):
        with pytest.raises(FakeUpstreamError):
            async with trace_data_stage(
                DataStage.NL2SQL_REQUEST,
                dependency="nl2sql",
                sink=sink,
                error_classifier=lambda _exc: (
                    "nl2sql_upstream_failed",
                    502,
                ),
            ):
                raise FakeUpstreamError("PRIVATE-PAYLOAD-SENTINEL")

        serialized = json.dumps(asdict(sink.events[0]))
        assert "PRIVATE-PAYLOAD-SENTINEL" not in serialized
        assert sink.events[0].error_class == "FakeUpstreamError"
        assert sink.events[0].error_code == "nl2sql_upstream_failed"
        assert sink.events[0].final_http_status == 502


def test_default_error_classifier_uses_only_exception_type() -> None:
    """Default classification is stable even when messages contain data."""
    assert classify_stage_error(TimeoutError("PRIVATE-PAYLOAD")) == (
        "upstream_timeout",
        504,
    )
    assert classify_stage_error(ValueError("PRIVATE-PAYLOAD")) == (
        "invalid_stage_input",
        400,
    )
    assert classify_stage_error(ConnectionError("PRIVATE-PAYLOAD")) == (
        "upstream_unavailable",
        502,
    )
    assert classify_stage_error(RuntimeError("PRIVATE-PAYLOAD")) == (
        "stage_failed",
        500,
    )


def test_stage_failure_from_exception_walks_cause_and_groups() -> None:
    """Attached stage metadata is recovered from cause and exception groups."""
    inner = RuntimeError("inner")
    setattr(
        inner,
        _STAGE_FAILURE_ATTRIBUTE,
        ("database_query", "upstream_failed", 502),
    )
    outer = RuntimeError("outer")
    outer.__cause__ = inner
    assert stage_failure_from_exception(outer) == (
        "database_query",
        "upstream_failed",
        502,
    )
    grouped = ExceptionGroup("stages", [ValueError("x"), inner])
    assert stage_failure_from_exception(grouped) == (
        "database_query",
        "upstream_failed",
        502,
    )


async def test_stage_trace_preserves_order_and_sanitizes_labels() -> None:
    """Nested stages append in completion order with safe label fallbacks."""
    sink = CapturingStageTraceSink()
    with request_context("user-1", "request-data-3"):
        async with (
            trace_data_stage(
                DataStage.NATIVE_REQUEST,
                dependency="native",
                sink=sink,
            ),
            trace_data_stage(
                DataStage.DATA_REWRITE,
                dependency="SELECT sensitive",
                sink=sink,
            ),
        ):
            pass

        assert [event.stage for event in current_stage_trace()] == [
            "data_rewrite",
            "native_request",
        ]
        assert current_stage_trace()[0].dependency == "unknown"
