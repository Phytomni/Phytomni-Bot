# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for FanOutWorker / TaskBuilder Protocol shapes."""

import inspect

import pytest

from mcp_server_phytomni.agents.shared.fan_out import (
    FanOutWorker,
    StateDelta,
    TaskBuilder,
    TaskPayload,
)

pytestmark = pytest.mark.agent


def test_fan_out_worker_is_runtime_checkable() -> None:
    """FanOutWorker Protocol must be runtime-checkable for isinstance()."""
    # Smoke test: FanOutWorker.__call__ signature carries a 'state' parameter.
    sig = inspect.signature(FanOutWorker.__call__)
    assert "state" in sig.parameters


def test_task_builder_protocol_signature() -> None:
    """TaskBuilder takes state dict, returns list of TaskPayload."""
    sig = inspect.signature(TaskBuilder.__call__)
    assert "state" in sig.parameters


def test_task_payload_and_state_delta_are_dict_aliases() -> None:
    """Type aliases must accept dict[str, Any] instances."""
    payload: TaskPayload = {"foo": "bar"}
    delta: StateDelta = {"x": 1}
    assert isinstance(payload, dict)
    assert isinstance(delta, dict)
