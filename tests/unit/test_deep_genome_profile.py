# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the DeepGenome BI SQL helper.

``_post_bi_sql`` routes through the shared ``bi_query`` helper (direct
GaussDB query + relay-mode branch). Pin the surviving A-5 guarantee:
retry exhaustion, a non-2xx status, and an empty payload surface as the
helper's ``McpError``, and a 2xx non-JSON body still surfaces as a clear
``McpError`` rather than an opaque decode error.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from mcp_server_phytomni.agents.deep_genome import profile
from mcp_server_phytomni.agents.deep_genome.profile import _post_bi_sql

pytestmark = pytest.mark.unit


def _patch_helper(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: Any = None,
    error: Exception | None = None,
) -> dict[str, Any]:
    """Replace profile.bi_query with a signature-agnostic async fake.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        result: Value the fake helper returns when no error is set.
        error: Exception the fake helper raises instead of returning.
    """

    captured: dict[str, Any] = {}

    async def fake_helper(*args: Any, **kwargs: Any) -> Any:
        """Return the configured payload or raise the configured error.

        Args:
            *args: Ignored positional args.
            **kwargs: Ignored keyword args.

        Returns:
            The configured ``result`` when no error is set.

        Raises:
            Exception: The configured ``error`` when set.
        """
        captured.update(args=args, kwargs=kwargs)
        if error is not None:
            raise error
        return result

    monkeypatch.setattr(profile, "bi_query", fake_helper)
    return captured


async def test_post_bi_sql_returns_payload_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a decoded JSON payload is returned unchanged.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None after the decoded payload assertion passes.
    """
    _patch_helper(monkeypatch, result={"data": []})

    result = await _post_bi_sql("SELECT 1")

    assert result == {"data": []}


async def test_post_bi_sql_preserves_caller_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The profile timeout reaches the shared BI retry policy."""
    captured = _patch_helper(monkeypatch, result={"data": []})

    result = await _post_bi_sql("SELECT 1", request_timeout=4.25)

    assert result == {"data": []}
    retry = captured["kwargs"]["retry"]
    assert retry.timeout == 4.25


async def test_post_bi_sql_rejects_legacy_timeout_keyword(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The BI lookup boundary rejects the removed timeout spelling."""
    _patch_helper(monkeypatch, result={"data": []})

    with pytest.raises(
        TypeError, match="unexpected keyword argument 'timeout'"
    ):
        await cast(Any, _post_bi_sql)("SELECT 1", timeout=4.25)


async def test_post_bi_sql_propagates_helper_mcperror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the retry helper's McpError propagates unchanged.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None after the propagated error assertion passes.
    """
    _patch_helper(
        monkeypatch,
        error=McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message="BI query failed after all retries",
            )
        ),
    )

    with pytest.raises(McpError) as excinfo:
        await _post_bi_sql("SELECT 1")

    assert "BI query failed" in excinfo.value.error.message


async def test_post_bi_sql_raises_mcperror_on_non_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a 2xx non-JSON body surfaces as a clear McpError.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None after the non-JSON error message assertions pass.
    """
    _patch_helper(
        monkeypatch,
        error=ValueError("Expecting value: line 1 column 1 (char 0)"),
    )

    with pytest.raises(McpError) as excinfo:
        await _post_bi_sql("SELECT 1")

    assert "non-JSON" in excinfo.value.error.message


async def test_post_bi_sql_raises_mcperror_on_empty_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a missing payload surfaces as a clear McpError.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None after the empty-payload error message assertion passes.
    """
    _patch_helper(monkeypatch, result=None)

    with pytest.raises(McpError) as excinfo:
        await _post_bi_sql("SELECT 1")

    assert "no payload" in excinfo.value.error.message
