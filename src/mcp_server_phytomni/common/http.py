# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared HTTP request and retry helpers."""

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from random import uniform
from typing import Any

from httpx import (
    AsyncClient,
    ConnectError,
    HTTPStatusError,
    Response,
    TimeoutException,
)
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData


@dataclass(frozen=True)
class JsonPostRequest:
    """HTTP request payload for retry helpers."""

    url: str
    method: str = "POST"
    headers: Mapping[str, str] | None = None
    json_body: Any = None
    data: Any = None


@dataclass(frozen=True)
class JsonPostRetry:
    """Retry policy and error messages for JSON POST calls."""

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
    """Sleep for a retriable HTTP status error or raise an MCP error."""
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
    exc: ConnectError | TimeoutException,
    *,
    attempt: int,
    max_retries: int,
    message: str = "Network error",
) -> bool:
    """Sleep for a retriable network error or raise an MCP error."""
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
    """Request with shared HTTP/network retry handling and return response."""
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
        except (ConnectError, TimeoutException) as exc:
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
    """POST with shared HTTP/network retry handling and return JSON."""
    response = await request_response_with_retries(client, request, retry)
    return response.json() if response is not None else None
