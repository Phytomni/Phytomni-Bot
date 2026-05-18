# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the DeepGenome BI SQL helper.

Pin the _post_bi_sql guards: a non-2xx status or a non-JSON body (an
HTML 502/504 gateway page) must surface as McpError with the status and
a body excerpt, not the opaque "Expecting value: line 1 column 1".
"""

from __future__ import annotations

from typing import Any

import pytest
import requests
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.deep_genome import profile

pytestmark = pytest.mark.unit


class _FakeResponse:
    """Minimal requests.Response stand-in for _post_bi_sql tests.

    Attributes:
        status_code: HTTP status code reported by the fake.
        text: Raw response body used for error excerpts.
    """

    def __init__(
        self,
        status_code: int,
        text: str,
        json_value: Any = None,
        json_error: Exception | None = None,
    ):
        """Store the simulated response behaviour.

        Args:
            status_code: HTTP status code to report.
            text: Raw body text for error excerpts.
            json_value: Value returned by ``json()`` on success.
            json_error: Exception raised by ``json()`` when set.
        """
        self.status_code = status_code
        self.text = text
        self._json_value = json_value
        self._json_error = json_error

    def raise_for_status(self) -> None:
        """Raise HTTPError for non-2xx statuses like requests does.

        Raises:
            requests.HTTPError: When the status code is >= 400.
        """
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Server Error")

    def json(self) -> Any:
        """Return the decoded body or raise the configured error.

        Returns:
            The configured JSON value on success.

        Raises:
            Exception: The configured ``json_error`` when set.
        """
        if self._json_error is not None:
            raise self._json_error
        return self._json_value


def _patch_post(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse):
    """Point profile.requests.post at a fixed fake response.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        response: Fake response every post call should return.
    """

    def fake_post(*args: Any, **kwargs: Any) -> _FakeResponse:
        """Return the fixed fake response.

        Args:
            *args: Ignored positional args.
            **kwargs: Ignored keyword args.

        Returns:
            The pre-built fake response.
        """
        _ = (args, kwargs)
        return response

    monkeypatch.setattr(profile.requests, "post", fake_post)


def test_post_bi_sql_returns_payload_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a 2xx JSON body is returned unchanged.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None after the decoded payload assertion passes.
    """
    _patch_post(
        monkeypatch,
        _FakeResponse(200, '{"data": []}', json_value={"data": []}),
    )

    result = profile._post_bi_sql("https://bi", {}, "SELECT 1")

    assert result == {"data": []}


def test_post_bi_sql_raises_mcperror_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a 504 gateway page surfaces as a clear McpError.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None after the error message assertions pass.
    """
    _patch_post(
        monkeypatch,
        _FakeResponse(504, "<html>504 Gateway Time-out</html>"),
    )

    with pytest.raises(McpError) as excinfo:
        profile._post_bi_sql("https://bi", {}, "SELECT 1")

    message = excinfo.value.error.message
    assert "HTTP 504" in message
    assert "Gateway Time-out" in message


def test_post_bi_sql_raises_mcperror_on_non_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a 200 non-JSON body surfaces as a clear McpError.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None after the non-JSON error message assertions pass.
    """
    _patch_post(
        monkeypatch,
        _FakeResponse(
            200,
            "<html>proxy error</html>",
            json_error=ValueError("Expecting value: line 1 column 1 (char 0)"),
        ),
    )

    with pytest.raises(McpError) as excinfo:
        profile._post_bi_sql("https://bi", {}, "SELECT 1")

    message = excinfo.value.error.message
    assert "non-JSON" in message
    assert "status=200" in message
