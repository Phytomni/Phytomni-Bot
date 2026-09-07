# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for MCP error sanitization in common.http retry helpers.

The retry helpers must never echo upstream URLs, response bodies, or
raw transport exception text into the MCP-facing error message or logs.
Operator logs retain only fixed event metadata. These tests pin both the
non-retriable HTTP-status path and the network-exhaustion path, plus
the defensive raise that replaces the loop's former silent `return
None`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR
from openai import APIConnectionError, APITimeoutError
from tests.support.logging_helpers import capture_non_propagating_logger

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


@pytest.fixture(autouse=True)
def _attach_http_log_handler(
    caplog: pytest.LogCaptureFixture,
) -> Iterator[None]:
    """Capture HTTP logs after package logging disables propagation."""
    with capture_non_propagating_logger(
        common_http.logger.name, caplog.handler
    ):
        yield


async def test_http_status_error_message_excludes_response_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
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

    with (
        caplog.at_level("ERROR", logger=common_http.logger.name),
        pytest.raises(McpError) as excinfo,
    ):
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
    assert "exception=HTTPStatusError" in caplog.text
    assert "status_code=500" in caplog.text
    assert _URL_SECRET not in caplog.text
    assert "INTERNAL_TOKEN_XYZ" not in caplog.text
    assert "internal" not in caplog.text


async def test_network_error_message_excludes_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Network exhaustion raises McpError without leaking transport text.

    The transport exception text references a sensitive URL; the
    sanitized McpError message exposes only the caller-provided prefix.
    """
    sensitive_msg = (
        "tcp connect to "
        "https://internal-backend.invalid/super-secret/path failed"
    )
    exc = httpx.ConnectError(sensitive_msg)

    with (
        caplog.at_level("ERROR", logger=common_http.logger.name),
        pytest.raises(McpError) as excinfo,
    ):
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
    assert "exception=ConnectError" in caplog.text
    assert "retries=0" in caplog.text
    assert _URL_SECRET not in caplog.text
    assert "tcp connect" not in caplog.text


@pytest.mark.parametrize("error_type", [APIConnectionError, APITimeoutError])
async def test_sdk_transport_preserves_safe_mcp_error_and_cause(
    error_type: type[APIConnectionError],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SDK exhaustion retains the MCP contract and private diagnostic cause."""
    exc = error_type(request=httpx.Request("POST", _URL_SECRET))
    with (
        caplog.at_level("ERROR", logger=common_http.logger.name),
        pytest.raises(McpError) as raised,
    ):
        await retry_network_or_raise(
            exc, attempt=2, max_retries=2, message=_NETWORK_PREFIX
        )
    assert raised.value.error.code == INTERNAL_ERROR
    assert str(raised.value) == _NETWORK_PREFIX
    assert raised.value.__cause__ is exc
    assert _URL_SECRET not in caplog.text
    assert f"exception={error_type.__name__}" in caplog.text
    assert "retries=2" in caplog.text


async def test_transport_retries_keep_existing_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Classification happens only after the original retries are exhausted."""
    sleeps: list[float] = []

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(common_http.asyncio, "sleep", record_sleep)
    exc = APITimeoutError(httpx.Request("POST", _URL_SECRET))
    for attempt in range(3):
        assert await retry_network_or_raise(
            exc, attempt=attempt, max_retries=3
        )
    with pytest.raises(McpError):
        await retry_network_or_raise(exc, attempt=3, max_retries=3)
    assert sleeps == [1.0, 1.5, 2.25]


async def test_request_and_retry_sleep_cancellation_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation is never converted into a transport or MCP error."""
    cancellation = asyncio.CancelledError()

    async def cancel(*_args: Any, **_kwargs: Any) -> Any:
        raise cancellation

    request = JsonPostRequest(url=_URL_SECRET, json_body={})
    retry = JsonPostRetry(
        timeout=1.0, max_retries=2, retriable_codes=(503,), message="failed"
    )
    with pytest.raises(asyncio.CancelledError) as raised:
        await request_response_with_retries(
            cast(Any, SimpleNamespace(request=cancel)), request, retry
        )
    assert raised.value is cancellation
    monkeypatch.setattr(common_http.asyncio, "sleep", cancel)
    with pytest.raises(asyncio.CancelledError) as raised:
        await retry_network_or_raise(
            httpx.ConnectError("x"), attempt=0, max_retries=2
        )
    assert raised.value is cancellation


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
