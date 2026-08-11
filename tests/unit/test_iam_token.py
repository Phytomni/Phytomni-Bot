# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the IAM ``get_token`` retry hardening.

``get_token`` previously issued one POST with no retry, so a single
transient ``httpx.ConnectError`` failed the whole request. These
offline tests pin: transient connect errors retry then succeed,
exhausted retries raise ``McpError``, and a 2xx response missing the
``X-Subject-Token`` header raises ``McpError``. Shared fake client and
instant-retry sleep live in ``tests/unit/conftest.py``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.auth import iam


@pytest.mark.usefixtures("instant_retry_sleep")
async def test_get_token_retries_transient_connect_error_then_succeeds(
    outbound_runtime: Any,
) -> None:
    """A transient ConnectError is retried; the next 2xx yields the token.

    Args:
        outbound_runtime: Recording process-owned outbound runtime.
    """
    outbound_runtime.transport.enqueue_error(
        httpx.ConnectError("transient connect blip")
    )
    outbound_runtime.transport.enqueue(
        status=201,
        headers={"X-Subject-Token": "tok-abc-123"},
    )

    token = await iam.get_token()

    assert token == "tok-abc-123"
    assert len(outbound_runtime.transport.requests) == 2


@pytest.mark.usefixtures("instant_retry_sleep")
async def test_get_token_raises_mcperror_after_exhausting_retries(
    outbound_runtime: Any,
) -> None:
    """Persistent ConnectError surfaces as McpError, not a raw exception.

    Args:
        outbound_runtime: Recording process-owned outbound runtime.
    """
    attempts = iam.SERVER_CONFIG.MAX_RETRIES + 1
    for _ in range(attempts):
        outbound_runtime.transport.enqueue_error(httpx.ConnectError("down"))

    with pytest.raises(McpError) as excinfo:
        await iam.get_token()

    assert "Failed to get token" in str(excinfo.value)
    assert len(outbound_runtime.transport.requests) == attempts


@pytest.mark.usefixtures("instant_retry_sleep")
async def test_get_token_raises_mcperror_when_header_missing(
    outbound_runtime: Any,
) -> None:
    """A 2xx response without X-Subject-Token is an McpError, not KeyError.

    Args:
        outbound_runtime: Recording process-owned outbound runtime.
    """
    outbound_runtime.transport.enqueue(status=200)

    with pytest.raises(McpError) as excinfo:
        await iam.get_token()

    assert "X-Subject-Token" in str(excinfo.value)


@pytest.mark.parametrize("legacy_keyword", ["timeout"])
async def test_get_token_rejects_legacy_timeout_keyword(
    outbound_runtime: Any,
    legacy_keyword: str,
) -> None:
    """The IAM boundary accepts only the typed request-timeout spelling.

    Args:
        outbound_runtime: Recording process-owned outbound runtime.
        legacy_keyword: Removed keyword spelling exercised dynamically.
    """
    del outbound_runtime
    legacy_options: dict[str, Any] = {legacy_keyword: 1.0}

    with pytest.raises(
        TypeError, match="unexpected keyword argument 'timeout'"
    ):
        await iam.get_token(**legacy_options)
