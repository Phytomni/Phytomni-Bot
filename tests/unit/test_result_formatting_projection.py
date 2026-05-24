# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for response projection helpers.

Covers ``resolve_debug`` (env override + per-request flag) and
``strip_agent_result`` (raw removal without mutation).
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.mcp.result_formatting import (
    resolve_debug,
    strip_agent_result,
)

pytestmark = pytest.mark.unit


# --- resolve_debug ---


def test_resolve_debug_returns_false_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_debug(None) returns False when env var is not set."""
    monkeypatch.delenv("PHYTOMNI_DEBUG", raising=False)
    assert resolve_debug(None) is False


def test_resolve_debug_returns_false_for_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_debug(False) returns False when env var is not set."""
    monkeypatch.delenv("PHYTOMNI_DEBUG", raising=False)
    assert resolve_debug(False) is False


def test_resolve_debug_per_request_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_debug(True) returns True even without env var."""
    monkeypatch.delenv("PHYTOMNI_DEBUG", raising=False)
    assert resolve_debug(True) is True


def test_resolve_debug_env_override_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PHYTOMNI_DEBUG=1 overrides per_request=False."""
    monkeypatch.setenv("PHYTOMNI_DEBUG", "1")
    assert resolve_debug(False) is True


def test_resolve_debug_env_override_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PHYTOMNI_DEBUG=true overrides per_request=None."""
    monkeypatch.setenv("PHYTOMNI_DEBUG", "true")
    assert resolve_debug(None) is True


def test_resolve_debug_env_override_yes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PHYTOMNI_DEBUG=yes is also truthy."""
    monkeypatch.setenv("PHYTOMNI_DEBUG", "yes")
    assert resolve_debug(None) is True


def test_resolve_debug_env_override_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PHYTOMNI_DEBUG=on is also truthy."""
    monkeypatch.setenv("PHYTOMNI_DEBUG", "on")
    assert resolve_debug(None) is True


def test_resolve_debug_env_zero_is_falsy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PHYTOMNI_DEBUG=0 does not enable debug mode."""
    monkeypatch.setenv("PHYTOMNI_DEBUG", "0")
    assert resolve_debug(None) is False


def test_resolve_debug_env_empty_is_falsy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PHYTOMNI_DEBUG= (empty) does not enable debug mode."""
    monkeypatch.setenv("PHYTOMNI_DEBUG", "")
    assert resolve_debug(None) is False


# --- strip_agent_result ---


def test_strip_agent_result_removes_raw() -> None:
    """strip_agent_result removes the 'raw' key."""
    result = {
        "formatted": {"answer": "hello", "references": []},
        "raw": {"some": "data"},
    }
    stripped = strip_agent_result(result)
    assert "raw" not in stripped
    assert "formatted" in stripped
    assert stripped["formatted"]["answer"] == "hello"


def test_strip_agent_result_keeps_formatted_only() -> None:
    """strip_agent_result keeps formatted when raw is absent."""
    result = {"formatted": {"answer": "x"}}
    stripped = strip_agent_result(result)
    assert stripped == {"formatted": {"answer": "x"}}


def test_strip_agent_result_does_not_mutate_input() -> None:
    """strip_agent_result returns a new dict without modifying input."""
    original = {
        "formatted": {"answer": "keep"},
        "raw": {"data": "remove"},
    }
    copy = {
        "formatted": {"answer": "keep"},
        "raw": {"data": "remove"},
    }
    strip_agent_result(original)
    assert original == copy


def test_strip_agent_result_preserves_extra_keys() -> None:
    """strip_agent_result only removes 'raw', keeps other keys."""
    result = {
        "formatted": {"answer": "a"},
        "raw": {"data": "b"},
        "extra": "kept",
    }
    stripped = strip_agent_result(result)
    assert stripped == {"formatted": {"answer": "a"}, "extra": "kept"}
