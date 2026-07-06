# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the shared HTTP retry helper transport-error coverage.

``request_response_with_retries`` historically retried only
``ConnectError`` / ``TimeoutException`` / retriable ``HTTPStatusError``,
so a mid-flight ``httpx.RemoteProtocolError`` slipped through uncaught.
These tests pin the broadened coverage: transient transport faults
retry, client-side protocol misuse does not, HTTP-status and exhaustion
behavior is unchanged. Shared scaffolding: ``tests/unit/conftest.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.common.http import (
    JsonPostRequest,
    JsonPostRetry,
    post_json_with_retries,
    request_response_with_retries,
)

_URL = "https://svc.invalid/endpoint"
_ClientFactory = Callable[[list[Any], dict[str, int]], type]


def _ok() -> httpx.Response:
    """Return a real 200 httpx.Response with a tiny JSON body."""
    return httpx.Response(
        200,
        content=b'{"ok": true}',
        request=httpx.Request("POST", _URL),
    )


def _status(code: int) -> httpx.Response:
    """Return a real httpx.Response whose raise_for_status raises ``code``."""
    return httpx.Response(code, request=httpx.Request("POST", _URL))


def _retry(max_retries: int = 3) -> JsonPostRetry:
    """Return a JsonPostRetry policy with 503 retriable."""
    return JsonPostRetry(
        timeout=1.0,
        max_retries=max_retries,
        retriable_codes=(503,),
        message="boom",
        network_message="neterr",
    )


async def _run(
    fake_client_factory: _ClientFactory, behaviors: list[Any]
) -> tuple[Any, dict[str, int]]:
    """Drive request_response_with_retries against scripted behaviors."""
    calls = {"n": 0}
    client = fake_client_factory(behaviors, calls)()
    request = JsonPostRequest(url=_URL, json_body={"q": 1})
    result = await request_response_with_retries(client, request, _retry())
    return result, calls


@pytest.mark.usefixtures("instant_retry_sleep")
@pytest.mark.parametrize(
    "transient",
    [
        httpx.RemoteProtocolError("server disconnected"),
        httpx.ProxyError("proxy boom"),
        httpx.ReadError("read reset"),
        httpx.ConnectError("connect blip"),
        httpx.ConnectTimeout("connect timed out"),
    ],
)
async def test_transient_transport_error_is_retried_then_succeeds(
    fake_client_factory: _ClientFactory,
    transient: Exception,
) -> None:
    """Each transient transport fault retries; the next 200 is returned.

    Args:
        fake_client_factory: Scripted fake-client builder.
        transient: The transient transport exception to inject first.
    """
    result, calls = await _run(fake_client_factory, [transient, _ok()])

    assert result is not None
    assert result.status_code == 200
    assert calls["n"] == 2


@pytest.mark.usefixtures("instant_retry_sleep")
async def test_local_protocol_error_is_not_retried(
    fake_client_factory: _ClientFactory,
) -> None:
    """Client-side protocol misuse propagates raw and is not retried.

    Args:
        fake_client_factory: Scripted fake-client builder.
    """
    calls = {"n": 0}
    client = fake_client_factory(
        [httpx.LocalProtocolError("bad request construction")], calls
    )()
    request = JsonPostRequest(url=_URL, json_body={"q": 1})

    with pytest.raises(httpx.LocalProtocolError):
        await request_response_with_retries(client, request, _retry())

    assert calls["n"] == 1  # no retry attempted


@pytest.mark.usefixtures("instant_retry_sleep")
async def test_transient_exhaustion_raises_mcperror(
    fake_client_factory: _ClientFactory,
) -> None:
    """Persistent server disconnect surfaces McpError after max retries.

    Args:
        fake_client_factory: Scripted fake-client builder.
    """
    calls = {"n": 0}
    client = fake_client_factory(
        [httpx.RemoteProtocolError("down")] * 4, calls
    )()
    request = JsonPostRequest(url=_URL, json_body={"q": 1})

    with pytest.raises(McpError) as excinfo:
        await request_response_with_retries(client, request, _retry(3))

    assert "neterr" in str(excinfo.value)
    assert calls["n"] == 4  # initial + 3 retries


@pytest.mark.usefixtures("instant_retry_sleep")
async def test_retriable_http_status_then_succeeds(
    fake_client_factory: _ClientFactory,
) -> None:
    """A retriable HTTP status still retries (behavior unchanged).

    Args:
        fake_client_factory: Scripted fake-client builder.
    """
    result, calls = await _run(fake_client_factory, [_status(503), _ok()])

    assert result is not None
    assert result.status_code == 200
    assert calls["n"] == 2


@pytest.mark.usefixtures("instant_retry_sleep")
async def test_post_json_returns_parsed_body_on_success(
    fake_client_factory: _ClientFactory,
) -> None:
    """``post_json_with_retries`` decodes the JSON body of a 200 response.

    Args:
        fake_client_factory: Scripted fake-client builder.
    """
    calls = {"n": 0}
    client = fake_client_factory([_ok()], calls)()

    body = await post_json_with_retries(
        client,
        JsonPostRequest(url=_URL, json_body={"q": 1}),
        _retry(),
    )

    assert body == {"ok": True}
    assert calls["n"] == 1


@pytest.mark.usefixtures("instant_retry_sleep")
async def test_post_json_raises_mcperror_on_retry_exhaustion(
    fake_client_factory: _ClientFactory,
) -> None:
    """Exhausted transient retries surface ``McpError``, never ``None``.

    Pins the post layer of the contract: ``request_response_with_retries``
    raises on exhaustion (no silent fallthrough) so ``post_json_with_retries``
    forwards the exception instead of yielding ``None`` to the caller.

    Args:
        fake_client_factory: Scripted fake-client builder.
    """
    calls = {"n": 0}
    client = fake_client_factory(
        [httpx.RemoteProtocolError("down")] * 4, calls
    )()

    with pytest.raises(McpError) as excinfo:
        await post_json_with_retries(
            client,
            JsonPostRequest(url=_URL, json_body={"q": 1}),
            _retry(3),
        )

    assert "neterr" in str(excinfo.value)
    assert calls["n"] == 4  # initial + 3 retries
