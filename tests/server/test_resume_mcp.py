# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the MCP elicitation resume adapter + graceful degrade."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.mcp import app as app_mod
from mcp_server_phytomni.runtime.resume import elicit_review_decision

pytestmark = pytest.mark.server


class _FakeSession:
    def __init__(self, *, capable: bool, action: str) -> None:
        self._capable = capable
        self._action = action
        self.elicit_calls: list[dict[str, Any]] = []

    def check_client_capability(self, capability: Any) -> bool:
        """Return the configured elicitation capability flag."""
        del capability
        return self._capable

    async def elicit(self, message: str, **kwargs: Any) -> Any:
        """Record the elicitation request and return the configured action."""
        requested_schema = kwargs["requestedSchema"]
        self.elicit_calls.append(
            {"message": message, "schema": requested_schema}
        )

        return SimpleNamespace(
            action=self._action,
            content={"edits": None},
        )


@pytest.mark.asyncio
async def test_elicit_review_decision_accept() -> None:
    """An accepted elicitation maps to an approved resume payload."""
    session = _FakeSession(capable=True, action="accept")
    payload = await elicit_review_decision(session, {"draft": "DRAFT"})
    assert payload == {"approved": True, "edits": None}
    assert session.elicit_calls


@pytest.mark.asyncio
async def test_elicit_review_decision_decline() -> None:
    """A declined elicitation maps to a rejected resume payload."""
    session = _FakeSession(capable=True, action="decline")
    payload = await elicit_review_decision(session, {"draft": "DRAFT"})
    assert payload == {"approved": False, "edits": None}


@pytest.mark.asyncio
async def test_elicit_review_decision_degrades_without_capability() -> None:
    """A client without elicitation support auto-approves without a prompt."""
    session = _FakeSession(capable=False, action="accept")
    payload = await elicit_review_decision(session, {"draft": "DRAFT"})
    assert payload == {"approved": True, "edits": None}
    assert not session.elicit_calls


@pytest.mark.asyncio
async def test_review_stdio_interrupt_elicits_and_resumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ReviewAgent stdio interrupts elicit a decision before formatting."""
    session = AsyncMock()
    fake_app = AsyncMock()
    interrupted = {
        "__interrupt__": [SimpleNamespace(value={"draft": "DRAFT"})]
    }
    resumed_state = {"final_response": "done"}
    decisions: list[dict[str, Any]] = []
    resumes: list[tuple[str, dict[str, Any]]] = []

    async def _fake_astream_progress(*_args: Any, **_kwargs: Any):
        sink = _args[3]
        sink.append(interrupted)
        if _kwargs.get("yield_tick"):
            yield {"kind": "phyto.progress"}

    async def _fake_elicit(session_arg: Any, draft: Any) -> dict[str, Any]:
        assert session_arg is session
        decisions.append({"draft": draft})
        return {"approved": True, "edits": None}

    async def _fake_resume(
        app_arg: Any,
        run_id: str,
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        assert app_arg is fake_app
        resumes.append((run_id, decision))
        return resumed_state

    terminal_payload = AsyncMock(return_value=[])
    monkeypatch.setattr(
        app_mod,
        "_graph_stream_target",
        lambda _tool, _args: (fake_app, {"user_query": "q"}),
    )
    monkeypatch.setattr(
        app_mod, "_astream_progress_ticks", _fake_astream_progress
    )
    monkeypatch.setattr(
        app_mod,
        "elicit_review_decision",
        _fake_elicit,
        raising=False,
    )
    monkeypatch.setattr(app_mod, "aresume_graph", _fake_resume, raising=False)
    monkeypatch.setattr(app_mod, "_stdio_terminal_payload", terminal_payload)

    drive_stdio_progress = getattr(app_mod, "_drive_stdio_progress")
    await drive_stdio_progress(
        "ReviewAgent",
        {"user_query": "q", "obs_file_list": []},
        progress_token="tok-1",
        session=session,
        run_id="run-review",
    )

    assert decisions == [{"draft": {"draft": "DRAFT"}}]
    assert resumes == [
        ("run-review", {"approved": True, "edits": None}),
    ]
    terminal_payload.assert_awaited_once_with("ReviewAgent", resumed_state)


@pytest.mark.asyncio
async def test_review_dispatch_without_progress_token_elicits_and_resumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ReviewAgent stdio calls elicit even without progressToken."""
    session = AsyncMock()
    fake_app = AsyncMock()
    interrupted = {
        "__interrupt__": [SimpleNamespace(value={"draft": "DRAFT"})]
    }
    resumed_state = {"final_response": "done"}
    decisions: list[dict[str, Any]] = []
    resumes: list[tuple[str, dict[str, Any]]] = []

    async def _fake_astream_progress(*_args: Any, **_kwargs: Any):
        sink = _args[3]
        sink.append(interrupted)
        if _kwargs.get("yield_tick"):
            yield {"kind": "phyto.progress"}

    async def _fake_elicit(session_arg: Any, draft: Any) -> dict[str, Any]:
        assert session_arg is session
        decisions.append({"draft": draft})
        return {"approved": True, "edits": None}

    async def _fake_resume(
        app_arg: Any,
        run_id: str,
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        assert app_arg is fake_app
        resumes.append((run_id, decision))
        return resumed_state

    async def _guard_blocking_path(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("ReviewAgent should not use blocking path")

    terminal_payload = AsyncMock(return_value=[])
    monkeypatch.setattr(
        app_mod,
        "_stdio_progress_context",
        lambda: (None, session, "run-review"),
    )
    monkeypatch.setattr(
        app_mod,
        "_graph_stream_target",
        lambda _tool, _args: (fake_app, {"user_query": "q"}),
    )
    monkeypatch.setattr(
        app_mod, "_astream_progress_ticks", _fake_astream_progress
    )
    monkeypatch.setattr(
        app_mod,
        "elicit_review_decision",
        _fake_elicit,
        raising=False,
    )
    monkeypatch.setattr(app_mod, "aresume_graph", _fake_resume, raising=False)
    monkeypatch.setattr(app_mod, "_stdio_terminal_payload", terminal_payload)
    monkeypatch.setattr(app_mod, "invoke_tool_enveloped", _guard_blocking_path)

    await app_mod.dispatch_tool(
        "ReviewAgent",
        {"user_query": "q", "obs_file_list": []},
    )

    assert decisions == [{"draft": {"draft": "DRAFT"}}]
    assert resumes == [
        ("run-review", {"approved": True, "edits": None}),
    ]
    session.send_progress_notification.assert_not_awaited()
    terminal_payload.assert_awaited_once_with("ReviewAgent", resumed_state)
