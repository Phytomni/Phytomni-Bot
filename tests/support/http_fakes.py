# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""ASGI HTTP-client fixtures shared by offline server tests."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from starlette.types import ASGIApp

from mcp_server_phytomni import server
from mcp_server_phytomni.api.lifecycle_contract import empty_agent_result

__all__ = [
    "assert_duplicate_attachment_response",
    "assert_degraded_tracking_response",
    "build_instant_chat_context_envelope",
    "expected_context_staged_value",
    "install_tool_handler",
    "install_rejection_handler",
    "open_asgi_client",
    "parse_sse_frames",
    "running_agent_run_body",
]

_REAL_ASYNC_REQUEST = httpx.AsyncClient.request


def running_agent_run_body(
    run_id: str,
    agent: str,
    *,
    task_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build one minimal accepted remote-agent response body."""
    return {
        "id": run_id,
        "run_id": run_id,
        "object": "agent.run",
        "agent": agent,
        "status": "running",
        "task_ids": [] if task_ids is None else list(task_ids),
        "result": empty_agent_result(),
    }


def build_instant_chat_context_envelope(
    turn_id: str,
    *,
    ledger_cursor: int = 1,
) -> dict[str, Any]:
    """Build the shared Instant Chat V1 envelope fixture."""
    return {
        "schema_version": 1,
        "conversation_key": "018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7",
        "dialogue_id": "018fdf9e-1f0b-7a63-a5a3-5e4625b43ad8",
        "turn_id": turn_id,
        "request_id": f"request-{turn_id}",
        "operation": "append",
        "mode": "instant",
        "current_message": {
            "content": "What is photosynthesis?",
            "locale": "en-US",
        },
        "requested_agent_id": None,
        "allowed_agent_ids": ["ChatAgent"],
        "ledger_cursor": ledger_cursor,
        "ledger_version": "a" * 64,
        "base_business_context_version": 0,
        "history_delta": [
            {
                "turn_id": turn_id,
                "role": "user",
                "content": "What is photosynthesis?",
            }
        ],
        "artifact_refs": [],
    }


def expected_context_staged_value(turn_id: str) -> dict[str, Any]:
    """Return the canonical context-staged custom-frame value."""
    return {
        "schema_version": 1,
        "turn_id": turn_id,
        "selected_agent_id": "ChatAgent",
        "route_source": "instant_lock",
        "route_reason_code": "INSTANT_LOCK",
        "base_business_context_version": 0,
        "proposed_business_context_version": 1,
        "last_applied_ledger_cursor": int(turn_id),
        "context_truncated": False,
        "context_rebuilt": True,
        "context_degraded": False,
    }


def parse_sse_frames(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse an SSE response body into semantic event/payload pairs."""
    frames: list[tuple[str, dict[str, Any]]] = []
    for chunk in body.split("\n\n"):
        lines = chunk.splitlines()
        if len(lines) < 2 or not lines[0].startswith("event: "):
            continue
        if not lines[1].startswith("data: "):
            continue
        frames.append(
            (
                lines[0].removeprefix("event: "),
                json.loads(lines[1].removeprefix("data: ")),
            )
        )
    return frames


def install_tool_handler(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    handler: Any,
) -> None:
    """Install one handler in the shared MCP tool registry."""
    monkeypatch.setitem(server.TOOL_HANDLERS, tool_name, handler)


def install_rejection_handler(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
) -> dict[str, bool]:
    """Install a tracked handler that must not run after validation."""
    marker = {"called": False}

    async def fake(_args: Any) -> dict[str, Any]:
        marker["called"] = True
        return {"answer": "must not run", "doc_list": []}

    install_tool_handler(monkeypatch, tool_name, fake)
    return marker


def assert_duplicate_attachment_response(response: httpx.Response) -> None:
    """Assert the common duplicate-attachment HTTP error projection."""
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "attachment_duplicate"


def assert_degraded_tracking_response(
    response: httpx.Response,
    task_id: str,
) -> dict[str, Any]:
    """Assert the shared 202 response for accepted work without a row."""
    assert response.status_code == 202
    body = response.json()
    assert body["id"] is None
    assert body["task_ids"] == [task_id]
    assert body["degraded_tracking"] is True
    assert "run_id" not in body
    return body


def minimal_tool_handler(
    answer: str,
) -> Callable[[Any], Awaitable[dict[str, Any]]]:
    """Return a handler with the minimal MCP tabular result shape."""

    async def fake(_args: Any) -> dict[str, Any]:
        return {"answer": answer, "doc_list": []}

    return fake


def tracked_tool_handler(
    result: dict[str, Any],
) -> tuple[dict[str, bool], Callable[[Any], Awaitable[dict[str, Any]]]]:
    """Return a handler plus a mutable call marker for rejection tests."""
    marker = {"called": False}

    async def fake(_args: Any) -> dict[str, Any]:
        marker["called"] = True
        return result

    return marker, fake


@asynccontextmanager
async def open_asgi_client(
    monkeypatch: pytest.MonkeyPatch,
    app: ASGIApp,
    *,
    base_url: str,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an in-process client after restoring real request dispatch."""
    monkeypatch.setattr(httpx.AsyncClient, "request", _REAL_ASYNC_REQUEST)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url=base_url,
    ) as client:
        yield client
