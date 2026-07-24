# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for authenticated user and request-id context propagation.

Covers the contextvar helpers, their flow into scratch RunIdentity paths,
the MCP zero-regression (no context implies anonymous), and the HTTP
X-Request-Id header / error-envelope correlation.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import httpx
import pytest
from starlette.types import Message, Receive, Scope, Send
from tests.support.config_fakes import fake_scratch_config

from mcp_server_phytomni.api.app import request_context_middleware
from mcp_server_phytomni.mcp import handlers as mcp_handlers
from mcp_server_phytomni.runtime.request_context import (
    bind_accepted_task_ids,
    bind_pre_recorded_task_id,
    bind_recorder_degraded,
    current_accepted_task_ids,
    current_pre_recorded_task_id,
    current_recorder_degraded,
    current_request_id,
    current_request_user,
    request_context,
)
from mcp_server_phytomni.storage import scratch as scratch_module

pytestmark = pytest.mark.server

scratch_server_dir = mcp_handlers.scratch_server_dir


def test_context_helpers_default_to_none() -> None:
    """Verify the contextvars are unset outside a request context."""
    assert current_request_user() is None
    assert current_request_id() is None


def test_request_context_sets_and_restores() -> None:
    """Verify request_context binds then restores both contextvars."""
    with request_context(user_id="alice", request_id="rid-1"):
        assert current_request_user() == "alice"
        assert current_request_id() == "rid-1"
    assert current_request_user() is None
    assert current_request_id() is None


def test_no_context_scratch_is_anonymous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the MCP path stays anonymous when no context is bound."""
    monkeypatch.setattr(
        scratch_module,
        "obsfs_bucket_available",
        lambda *_a, **_k: False,
    )
    path = scratch_server_dir(fake_scratch_config(tmp_path), "chat")

    assert "anonymous" in path


def test_request_context_user_reaches_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a bound user id flows into the scratch RunIdentity path."""
    monkeypatch.setattr(
        scratch_module,
        "obsfs_bucket_available",
        lambda *_a, **_k: False,
    )
    with request_context(user_id="alice", request_id="rid-2"):
        path = scratch_server_dir(fake_scratch_config(tmp_path), "chat")

    assert "alice" in path
    assert "anonymous" not in path


async def test_healthz_echoes_request_id_header(
    api_client: httpx.AsyncClient,
) -> None:
    """Verify every response carries a generated X-Request-Id."""
    response = await api_client.get("/healthz")

    request_id = response.headers.get("X-Request-Id")
    assert request_id is not None
    assert "-request-" in request_id


async def test_error_envelope_carries_request_id(
    api_client: httpx.AsyncClient,
) -> None:
    """Verify the error envelope request_id matches the header."""
    response = await api_client.get("/v1/nope")

    assert response.status_code == 404
    header_id = response.headers.get("X-Request-Id")
    body_id = response.json()["error"]["request_id"]
    assert body_id is not None
    assert body_id == header_id


async def test_http_middleware_resets_pre_recorded_task_marker() -> None:
    """Remote tracking state from one HTTP request cannot leak to another."""
    observed: list[tuple[str | None, tuple[str, ...], bool]] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        del receive
        observed.append(
            (
                current_pre_recorded_task_id(),
                current_accepted_task_ids(),
                current_recorder_degraded(),
            )
        )
        if scope["path"] == "/owners/alice":
            bind_pre_recorded_task_id("task-alice")
            bind_accepted_task_ids(("task-alice", "task-alice"))
            bind_recorder_degraded(True)
        await send(
            {"type": "http.response.start", "status": 200, "headers": []}
        )

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = request_context_middleware(downstream)
    alice_scope = cast(
        Scope, {"type": "http", "method": "GET", "path": "/owners/alice"}
    )
    bob_scope = cast(
        Scope, {"type": "http", "method": "GET", "path": "/owners/bob"}
    )
    await middleware(alice_scope, receive, send)
    await middleware(bob_scope, receive, send)

    assert observed == [(None, (), False), (None, (), False)]
    assert [message["type"] for message in sent] == [
        "http.response.start",
        "http.response.start",
    ]
    assert current_pre_recorded_task_id() is None
    assert current_accepted_task_ids() == ()
    assert current_recorder_degraded() is False
