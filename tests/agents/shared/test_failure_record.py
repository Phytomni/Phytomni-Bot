# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the universal FailureRecord schema + failures channel."""

import operator
from typing import get_args, get_type_hints

import pytest

from mcp_server_phytomni.agents.shared.parallel_dispatch import (
    FailureRecord,
    ParallelDispatchState,
)

pytestmark = pytest.mark.unit


def test_failure_record_required_keys() -> None:
    """FailureRecord pins the four-field schema for cross-agent use."""
    hints = get_type_hints(FailureRecord)
    assert set(hints.keys()) == {
        "task_label",
        "message",
        "kind",
        "traceback_digest",
    }


def test_parallel_dispatch_state_has_failures_channel() -> None:
    """failures: Annotated[list[FailureRecord], operator.add] is present."""
    hints = get_type_hints(ParallelDispatchState, include_extras=True)
    assert (
        "failures" in hints
    ), "failures channel missing on ParallelDispatchState"


def test_failures_reducer_is_operator_add() -> None:
    """Reducer must be list-concat so N concurrent Send tasks accumulate."""
    hints = get_type_hints(ParallelDispatchState, include_extras=True)
    failures_hint = hints["failures"]
    metadata_args = get_args(failures_hint)
    assert any(
        arg is operator.add for arg in metadata_args
    ), f"failures channel reducer is not operator.add: {metadata_args}"
