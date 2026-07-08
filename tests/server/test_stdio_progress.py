# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the MCP stdio in-band progress-notification path."""

# pylint: disable=protected-access
# Test exercises ``_drive_stdio_progress`` directly to assert the
# per-tick notification contract and the dispatch fallback.

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.mcp import app as app_mod

pytestmark = pytest.mark.server


async def test_progress_forwarded_when_token_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A progressToken drives send_progress_notification per tick."""
    ticks = [
        {
            "kind": "phyto.progress",
            "phase": "retrieving",
            "current": 3,
            "total": 8,
            "detail": "g 3/8",
        },
        {
            "kind": "phyto.progress",
            "phase": "generating",
            "current": 1,
            "total": 1,
            "detail": None,
        },
    ]

    async def _fake_astream_progress(*_a: Any, **_k: Any):
        for t in ticks:
            yield t

    monkeypatch.setattr(
        app_mod, "_astream_progress_ticks", _fake_astream_progress
    )
    # capture terminal payload path
    monkeypatch.setattr(
        app_mod,
        "_stdio_terminal_payload",
        AsyncMock(return_value=[]),
    )
    session = AsyncMock()
    notify = session.send_progress_notification

    await app_mod._drive_stdio_progress(
        "KnowledgeAgent",
        {"user_query": "q", "obs_file_list": []},
        progress_token="tok-1",
        session=session,
        run_id="r",
    )

    assert notify.await_count == 2
    first = notify.await_args_list[0].kwargs
    assert first["progress_token"] == "tok-1"
    assert first["progress"] == 3
    assert first["total"] == 8
    assert first["message"] == "retrieving"


async def test_dispatch_tool_falls_back_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No progressToken -> dispatch_tool takes the blocking path.

    The stdio progress driver must not run when the client did not
    request progress; the byte-identical blocking envelope path stays.
    """
    called = {"drive": False}

    async def _guard(*_a: Any, **_k: Any) -> Any:
        called["drive"] = True

    monkeypatch.setattr(app_mod, "_drive_stdio_progress", _guard)
    # request_ctx.get() raising LookupError == no active MCP request
    # is the real no-token shape; dispatch_tool must swallow it.
    result = await app_mod.dispatch_tool("GetTaskStatus", {"task_id": "t1"})
    assert called["drive"] is False
    assert result  # a normal serialized response
