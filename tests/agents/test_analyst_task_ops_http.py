# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP-wrapper tests for ``task_log`` and ``task_delete``.

Pins the 200 happy path (parsed JSON / success-message string) and
the post-retry exhaust path (``McpError`` raise). Sibling
``task_status`` shares the same shape and is e2e-covered.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.analyst import task_ops as task_ops_module

pytestmark = pytest.mark.unit


def _patch_token_and_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub out IAM token + HTTP client factory used by every wrapper."""

    async def fake_token(*_args: Any, **_kwargs: Any) -> str:
        """Return a fixed test token instead of hitting IAM."""
        return "test-token"

    monkeypatch.setattr(task_ops_module, "get_token", fake_token)

    class _NoopClient:
        """Async context manager that yields a sentinel client."""

        async def __aenter__(self) -> Any:
            """Return a stand-in client object (never actually used)."""
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            """Exit cleanly without propagating exceptions."""
            return None

    def fake_factory(*_args: Any, **_kwargs: Any) -> _NoopClient:
        """Return a fresh no-op client per call."""
        return _NoopClient()

    monkeypatch.setattr(task_ops_module, "get_async_client", fake_factory)


async def test_task_log_returns_json_on_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful 200 response is unwrapped to the parsed JSON body."""
    _patch_token_and_client(monkeypatch)

    payload = {"logs": ["round 1 output"], "task_id": "t-1"}

    async def fake_request(*_args: Any, **_kwargs: Any) -> Any:
        """Return a fake 200 response carrying ``payload``."""
        return SimpleNamespace(status_code=200, json=lambda: payload)

    monkeypatch.setattr(
        task_ops_module, "request_response_with_retries", fake_request
    )

    result = await task_ops_module.task_log("t-1")

    assert result == payload


async def test_task_log_raises_mcperror_when_all_retries_exhaust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``None`` retry result surfaces as the documented McpError."""
    _patch_token_and_client(monkeypatch)

    async def fake_request(*_args: Any, **_kwargs: Any) -> Any:
        """Mimic the all-retries-exhausted state by returning ``None``."""
        return None

    monkeypatch.setattr(
        task_ops_module, "request_response_with_retries", fake_request
    )

    with pytest.raises(
        McpError, match="Failed to check task log after all retries"
    ):
        await task_ops_module.task_log("t-2")


async def test_task_delete_returns_success_string_on_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful 200 response yields the ``Delete task ... success.``
    summary string the analyst-platform CLI surfaces to the operator.
    """
    _patch_token_and_client(monkeypatch)

    async def fake_request(*_args: Any, **_kwargs: Any) -> Any:
        """Return a fake 200 response (json body ignored on delete)."""
        return SimpleNamespace(status_code=200, json=lambda: {})

    monkeypatch.setattr(
        task_ops_module, "request_response_with_retries", fake_request
    )

    result = await task_ops_module.task_delete("t-3")

    assert result == "Delete task t-3 success."


async def test_task_delete_raises_mcperror_when_all_retries_exhaust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``None`` retry result surfaces as the documented McpError."""
    _patch_token_and_client(monkeypatch)

    async def fake_request(*_args: Any, **_kwargs: Any) -> Any:
        """Mimic the all-retries-exhausted state by returning ``None``."""
        return None

    monkeypatch.setattr(
        task_ops_module, "request_response_with_retries", fake_request
    )

    with pytest.raises(
        McpError, match="Failed to delete task after all retries"
    ):
        await task_ops_module.task_delete("t-4")
