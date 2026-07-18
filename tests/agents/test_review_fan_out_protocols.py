# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for FanOutWorker / TaskBuilder callback shapes."""

from typing import Any

import pytest

from mcp_server_phytomni.agents.shared.fan_out import (
    FanOutWorker,
    StateDelta,
    TaskBuilder,
    TaskPayload,
)

pytestmark = pytest.mark.agent


async def test_fan_out_worker_callback_invocation() -> None:
    """A real async worker satisfies and executes the callback alias."""

    async def worker_impl(state: dict[str, Any]) -> StateDelta:
        return {"seen": state["seen"]}

    worker: FanOutWorker = worker_impl
    assert await worker({"seen": True}) == {"seen": True}


def test_task_builder_callback_invocation() -> None:
    """A real builder satisfies and executes the callback alias."""

    def builder_impl(state: dict[str, Any]) -> list[TaskPayload]:
        return [{"task": state["task"]}]

    builder: TaskBuilder = builder_impl
    assert builder({"task": "retrieve"}) == [{"task": "retrieve"}]


def test_task_payload_and_state_delta_are_dict_aliases() -> None:
    """Type aliases must accept dict[str, Any] instances."""
    payload: TaskPayload = {"foo": "bar"}
    delta: StateDelta = {"x": 1}
    assert isinstance(payload, dict)
    assert isinstance(delta, dict)
