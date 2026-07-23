# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Test that the stdio client declares elicitation capability."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any

import pytest
from tests.support.client_fakes import build_client_call_fakes

from mcp_client_phytomni.client import PhytomniMcpClient

pytestmark = pytest.mark.unit


def test_connect_passes_elicitation_callback() -> None:
    """connect() constructs ClientSession with an elicitation_callback."""
    source = inspect.getsource(PhytomniMcpClient.connect)
    assert "elicitation_callback" in source


async def test_elicitation_callback_auto_approves_by_default() -> None:
    """The default callback accepts approval prompts unchanged."""
    client = PhytomniMcpClient()
    callback = getattr(client, "_elicitation_callback")

    result = await callback(None, None)

    assert result.action == "accept"
    assert result.content == {"approved": True, "edits": None}


async def test_elicitation_callback_uses_approval_decider() -> None:
    """A configured decider supplies the elicitation content."""
    client = PhytomniMcpClient()
    context = SimpleNamespace(request_id="request-1")
    params = SimpleNamespace(message="Approve?")
    decision = {"approved": False, "edits": None}

    def _decider(decider_context: Any, decider_params: Any) -> dict[str, Any]:
        assert decider_context is context
        assert decider_params is params
        return decision

    setattr(client, "_approval_decider", _decider)
    callback = getattr(client, "_elicitation_callback")

    result = await callback(context, params)

    assert result.action == "accept"
    assert result.content == decision


async def test_call_tool_scopes_approval_decider() -> None:
    """call_tool() exposes the decider only for one tool call."""
    client, fake_session, fake_result = build_client_call_fakes()

    def _decider(_context: Any, _params: Any) -> dict[str, Any]:
        return {"approved": True, "edits": None}

    async def _call_tool(*_args: Any, **_kwargs: Any) -> Any:
        assert getattr(client, "_approval_decider") is _decider
        return fake_result

    fake_session.call_tool.side_effect = _call_tool
    await client.call_tool(
        "ChatAgent",
        {"user_query": "q"},
        approval_decider=_decider,
    )

    assert getattr(client, "_approval_decider") is None
