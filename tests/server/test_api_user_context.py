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
from types import SimpleNamespace

import httpx
import pytest

from mcp_server_phytomni.mcp import handlers as mcp_handlers
from mcp_server_phytomni.runtime.request_context import (
    current_request_id,
    current_request_user,
    request_context,
)
from mcp_server_phytomni.storage import scratch as scratch_module

pytestmark = pytest.mark.server

scratch_server_dir = mcp_handlers.scratch_server_dir


def _fake_config(tmp_path: Path) -> SimpleNamespace:
    """Return a minimal config namespace usable by scratch_server_dir."""
    return SimpleNamespace(
        BUCKET_NAME="phytomni",
        TEMP_DIR=str(tmp_path / "fallback"),
    )


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
    path = scratch_server_dir(_fake_config(tmp_path), "chat")

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
        path = scratch_server_dir(_fake_config(tmp_path), "chat")

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
