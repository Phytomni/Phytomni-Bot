# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for in-request context vs detached Data/Review dispatch."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.api.agent_runs import (
    _has_in_request_context_execution,
)

pytestmark = pytest.mark.unit


def test_native_payload_does_not_look_like_context_execution() -> None:
    """Bare native/Expert POSTs keep the detached 202 worker."""
    assert (
        _has_in_request_context_execution(
            {"agent": "data", "arguments": {"user_query": "q"}}
        )
        is False
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"agent_thread_id": "ctx-data-1"},
        {"private_agent_state": {"data_adapter": object()}},
        {"conversation_messages": ({"role": "user", "content": "q"},)},
    ],
)
def test_v1_context_fields_select_in_request_execution(
    payload: dict[str, object],
) -> None:
    """Context Data/Review stage a completed outcome, not a 202 worker."""
    assert _has_in_request_context_execution(payload) is True
