# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared HTTP request and retry helpers.

Classes: JsonPostRequest, JsonPostRetry.
Functions: retry_http_status_or_raise, retry_network_or_raise,
    request_response_with_retries, post_json_with_retries.
"""

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from random import uniform
from typing import Any

from httpx import (
    AsyncClient,
    HTTPStatusError,
    NetworkError,
    ProxyError,
    RemoteProtocolError,
    Response,
    TimeoutException,
    TransportError,
)
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

# Transient transport faults worth retrying: connect / read / write /
# close errors and all timeouts (NetworkError, TimeoutException), a
# mid-flight server disconnect (RemoteProtocolError), and proxy
# failures (ProxyError). Deliberately excludes LocalProtocolError and
# UnsupportedProtocol — client-side misuse a retry cannot fix.
# ``ConnectError`` stays covered as a ``NetworkError`` subclass.
_RETRIABLE_TRANSPORT_ERRORS = (
    TimeoutException,
    NetworkError,
    RemoteProtocolError,
    ProxyError,
)


@dataclass(frozen=True)
class JsonPostRequest:
    """HTTP request payload for retry helpers.

    Attributes:
        url: Target URL for the HTTP request.
        method: HTTP method (default 'POST').
        headers: Optional mapping of HTTP headers.
        json_body: JSON-serializable body for POST requests.
        data: Optional raw data body.
    """

    url: str
    method: str = "POST"
    headers: Mapping[str, str] | None = None
    json_body: Any = None
    data: Any = None


@dataclass(frozen=True)
class JsonPostRetry:
    """Retry policy and error messages for JSON POST calls.

    Attributes:
        timeout: Request timeout in seconds.
        max_retries: Maximum number of retry attempts.
        retriable_codes: Iterable of HTTP status codes
            that should trigger retry.
        message: Error message prefix for MCP error on
            non-retriable status.
        network_message: Error message prefix for network errors
            (default 'Network error').
    """

    timeout: float
    max_retries: int
    retriable_codes: Iterable[int]
    message: str
    network_message: str = "Network error"


async def _send_retry_request(
    client: AsyncClient,
    request: JsonPostRequest,
    timeout: float,
) -> Response:
    """Send one HTTP request using the common retry payload."""
    method = request.method.upper()
    headers = dict(request.headers or {})
    if method == "GET":
        return await client.get(
            request.url,
            headers=headers,
            timeout=timeout,
        )
    if method == "POST":
        return await client.post(
            request.url,
            json=request.json_body,
            data=request.data,
            headers=headers,
            timeout=timeout,
        )
    return await client.request(
        method,
        request.url,
        json=request.json_body,
        data=request.data,
        headers=headers,
        timeout=timeout,
    )


async def retry_http_status_or_raise(
    exc: HTTPStatusError,
    *,
    attempt: int,
    max_retries: int,
    retriable_codes: Iterable[int],
    message: str,
) -> bool:
    """Sleep for a retriable HTTP status error or raise an MCP error.

    Args:
        exc: The HTTPStatusError exception to evaluate.
        attempt: Current attempt number (0-indexed).
        max_retries: Maximum number of retry attempts
            before raising.
        retriable_codes: HTTP status codes that should
            trigger retry.
        message: Error message prefix for MCP error on
            non-retriable status.

    Returns:
        bool: True if the error was retriable and sleep
            occurred, triggering retry.
            Returns are only meaningful internally;
            caller should check attempt count.

    Raises:
        McpError: Raised when the HTTP status code is
            not retriable or max retries exceeded.
    """
    if (
        exc.response is not None
        and exc.response.status_code in retriable_codes
        and attempt < max_retries
    ):
        await asyncio.sleep((2**attempt) + uniform(0, 1))
        return True
    raise McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message=f"{message}: {str(exc)}",
        )
    ) from exc


async def retry_network_or_raise(
    exc: TransportError,
    *,
    attempt: int,
    max_retries: int,
    message: str = "Network error",
) -> bool:
    """Sleep for a retriable network error or raise an MCP error.

    Args:
        exc: The transient transport exception (timeout, network,
            server disconnect, or proxy error) to evaluate.
        attempt: Current attempt number (0-indexed).
        max_retries: Maximum number of retry attempts
            before raising.
        message: Error message prefix for MCP error
            (default 'Network error').

    Returns:
        bool: True if retriable and sleep occurred,
            triggering retry.

    Raises:
        McpError: Raised when max retries exceeded.
    """
    if attempt < max_retries:
        await asyncio.sleep(1.5**attempt)
        return True
    raise McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message=f"{message}: {str(exc)}",
        )
    ) from exc


async def request_response_with_retries(
    client: AsyncClient,
    request: JsonPostRequest,
    retry: JsonPostRetry,
) -> Response | None:
    """Request with shared HTTP/network retry handling and return response.

    Args:
        client: Async HTTP client (httpx.AsyncClient).
        request: JSON POST request payload including url,
            method, headers, body.
        retry: Retry policy including timeout, max_retries,
            retriable_codes, and messages.

    Returns:
        Response | None: httpx.Response on success,
            None after all retries exhausted.
    """
    attempt = 0
    while attempt <= retry.max_retries:
        try:
            response = await _send_retry_request(
                client, request, retry.timeout
            )
            response.raise_for_status()
            return response
        except HTTPStatusError as exc:
            if await retry_http_status_or_raise(
                exc,
                attempt=attempt,
                max_retries=retry.max_retries,
                retriable_codes=retry.retriable_codes,
                message=retry.message,
            ):
                attempt += 1
                continue
        except _RETRIABLE_TRANSPORT_ERRORS as exc:
            retry_network = await retry_network_or_raise(
                exc,
                attempt=attempt,
                max_retries=retry.max_retries,
                message=retry.network_message,
            )
            if retry_network:
                attempt += 1
                continue
        attempt += 1
    return None


async def post_json_with_retries(
    client: AsyncClient,
    request: JsonPostRequest,
    retry: JsonPostRetry,
) -> Any:
    """POST with shared HTTP/network retry handling and return JSON.

    Args:
        client: Async HTTP client (httpx.AsyncClient).
        request: JSON POST request payload including url,
            method, headers, body.
        retry: Retry policy including timeout, max_retries,
            retriable_codes, and messages.

    Returns:
        Any: Parsed JSON response body, or None if all
            retries exhausted.
    """
    response = await request_response_with_retries(client, request, retry)
    return response.json() if response is not None else None
