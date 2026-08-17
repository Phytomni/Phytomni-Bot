# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Edge contracts for DataAgent stage-trace classification and walks."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.runtime.stage_trace import (
    StageTraceSink,
    _safe_error_class,
    _safe_http_status,
    classify_stage_error,
    stage_failure_from_exception,
)

pytestmark = pytest.mark.unit


class _NamedSink:
    """Concrete sink used only to exercise the runtime protocol property."""

    def emit(self, event: object) -> None:
        """Ignore events; this fixture only exposes the contract name."""


def test_stage_trace_sink_contract_name_is_stable() -> None:
    """The runtime-only protocol property identifies the tracing contract."""
    helper = getattr(StageTraceSink, "contract_name")
    assert helper.fget(_NamedSink()) == "stage_trace"


def test_safe_error_class_and_http_status_fall_back() -> None:
    """Unsafe class names and invalid HTTP codes stay payload-free."""
    assert _safe_error_class("not a class") == "Exception"
    assert _safe_http_status(True) == 500
    assert _safe_http_status(99) == 500
    assert _safe_http_status(600) == 500
    assert _safe_http_status(404) == 404


def test_classify_stage_error_maps_oserror_to_unavailable() -> None:
    """Connection-like OS errors stay a retryable upstream failure."""
    assert classify_stage_error(OSError("down")) == (
        "upstream_unavailable",
        502,
    )


def test_stage_failure_walks_groups_and_causes() -> None:
    """Attached stage metadata is recovered from groups and causal chains."""
    leaf = RuntimeError("leaf")
    setattr(leaf, "_phytomni_stage_failure", ("database_query", "x", 502))
    grouped = ExceptionGroup("bundle", [ValueError("other"), leaf])
    wrapped = RuntimeError("outer")
    wrapped.__cause__ = grouped
    assert stage_failure_from_exception(wrapped) == (
        "database_query",
        "x",
        502,
    )


def test_stage_failure_walk_breaks_cycles() -> None:
    """A cyclic cause chain does not recurse forever."""
    first = RuntimeError("first")
    second = RuntimeError("second")
    first.__cause__ = second
    second.__cause__ = first
    assert stage_failure_from_exception(first) is None
