# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""PhytomniMcpClient.call_tool threads progress_callback to the session."""

from __future__ import annotations

import pytest
from tests.support.client_fakes import build_client_call_fakes

pytestmark = pytest.mark.unit


async def test_call_tool_threads_progress_callback() -> None:
    """A supplied progress_callback reaches session.call_tool."""
    client, fake_session, fake_result = build_client_call_fakes()
    fake_session.call_tool.return_value = fake_result

    async def _cb(_p: float, _t: float | None, _m: str | None) -> None:
        return None

    await client.call_tool(
        "ChatAgent", {"user_query": "q"}, progress_callback=_cb
    )

    assert fake_session.call_tool.await_args.kwargs["progress_callback"] is _cb
