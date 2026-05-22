# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for MCP error sanitization in common.http retry helpers.

The retry helpers must never echo upstream URLs, response bodies, or
raw transport exception text into the MCP-facing error message; that
detail belongs in operator logs only. These tests pin both the
non-retriable HTTP-status path and the network-exhaustion path, plus
the defensive raise that replaces the loop's former silent `return
None`.
"""

from __future__ import annotations

from typing import Any, Callable

import httpx
import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.common import http as common_http
from mcp_server_phytomni.common.http import (
    JsonPostRequest,
    JsonPostRetry,
    request_response_with_retries,
    retry_http_status_or_raise,
    retry_network_or_raise,
)

pytestmark = pytest.mark.unit

_URL_SECRET = "https://internal-backend.invalid/super-secret/path"
_BODY_SECRET = b'{"api_key": "INTERNAL_TOKEN_XYZ"}'
_SAFE_PREFIX = "upstream call failed"
_NETWORK_PREFIX = "upstream connection failed"

_ClientFactory = Callable[[list[Any], dict[str, int]], type]


async def test_http_status_error_message_excludes_response_body() -> None:
    """Non-retriable status raises McpError without leaking URL or body.

    The HTTPStatusError carries the sensitive request URL and a body
    that mimics an upstream secret; the sanitized McpError message
    must contain only the caller-provided prefix.
    """
    response = httpx.Response(
        500,
        content=_BODY_SECRET,
        request=httpx.Request("POST", _URL_SECRET),
    )
    exc = httpx.HTTPStatusError(
        "internal", request=response.request, response=response
    )

    with pytest.raises(McpError) as excinfo:
        await retry_http_status_or_raise(
            exc,
            attempt=0,
            max_retries=0,
            retriable_codes=(503,),
            message=_SAFE_PREFIX,
        )

    err_msg = str(excinfo.value)
    assert err_msg == _SAFE_PREFIX
    assert _URL_SECRET not in err_msg
    assert "INTERNAL_TOKEN_XYZ" not in err_msg


async def test_network_error_message_excludes_exception_text() -> None:
    """Network exhaustion raises McpError without leaking transport text.

    The transport exception text references a sensitive URL; the
    sanitized McpError message exposes only the caller-provided prefix.
    """
    sensitive_msg = (
        "tcp connect to "
        "https://internal-backend.invalid/super-secret/path failed"
    )
    exc = httpx.ConnectError(sensitive_msg)

    with pytest.raises(McpError) as excinfo:
        await retry_network_or_raise(
            exc,
            attempt=0,
            max_retries=0,
            message=_NETWORK_PREFIX,
        )

    err_msg = str(excinfo.value)
    assert err_msg == _NETWORK_PREFIX
    assert _URL_SECRET not in err_msg
    assert "tcp connect" not in err_msg


@pytest.mark.usefixtures("instant_retry_sleep")
async def test_request_loop_exit_raises_instead_of_returning_none(
    fake_client_factory: _ClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loop completion without an exception raises a sanitized McpError.

    The retry helpers normally either return True or raise McpError on
    exhaustion, so the loop never exits naturally. This test forces the
    pathological path by stubbing the network helper to always return
    True, then asserts the defensive guard raises instead of silently
    returning None to the caller.

    Args:
        fake_client_factory: Scripted fake-client builder.
        monkeypatch: Pytest monkeypatch fixture used to swap the
            network retry helper.
    """

    async def always_retry(
        exc: httpx.TransportError,
        *,
        attempt: int,
        max_retries: int,
        message: str = "Network error",
    ) -> bool:
        """Force the retry loop to never raise via the helper path."""
        del exc, attempt, max_retries, message
        return True

    monkeypatch.setattr(common_http, "retry_network_or_raise", always_retry)

    calls = {"n": 0}
    client = fake_client_factory(
        [httpx.RemoteProtocolError("down")] * 5, calls
    )()
    request = JsonPostRequest(url=_URL_SECRET, json_body={"q": 1})
    retry = JsonPostRetry(
        timeout=1.0,
        max_retries=2,
        retriable_codes=(503,),
        message=_NETWORK_PREFIX,
        network_message=_NETWORK_PREFIX,
    )

    with pytest.raises(McpError) as excinfo:
        await request_response_with_retries(client, request, retry)

    assert str(excinfo.value) == _NETWORK_PREFIX
