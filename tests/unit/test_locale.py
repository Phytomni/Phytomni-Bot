# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for effective locale resolution and request-local state."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.runtime.locale import (
    UnsupportedLocaleError,
    bind_effective_locale,
    current_effective_locale,
    locale_instruction,
    message_for,
    normalize_accept_language,
    resolve_effective_locale,
)
from mcp_server_phytomni.runtime.request_context import reset_request_var

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("explicit", "header", "query", "expected"),
    [
        ("zh-CN", "en-US", "hello", "zh-CN"),
        (None, "fr, zh;q=0.8", "hello", "zh-CN"),
        (None, "en-GB, zh-CN", "\u6c34\u7a3b\u57fa\u56e0", "en-US"),
        (
            None,
            "fr-FR",
            "\u6c34\u7a3b\u57fa\u56e0\u529f\u80fd\u662f\u4ec0\u4e48\uff1f",
            "zh-CN",
        ),
        (None, None, "What is this rice gene?", "en-US"),
    ],
)
def test_effective_locale_precedence(
    explicit: str | None,
    header: str | None,
    query: str,
    expected: str,
) -> None:
    """Body, header, and query precedence are deterministic."""
    assert (
        resolve_effective_locale(
            explicit=explicit,
            accept_language=header,
            latest_user_query=query,
        )
        == expected
    )


def test_explicit_locale_is_exact() -> None:
    """An explicit unsupported locale fails instead of being normalized."""
    with pytest.raises(UnsupportedLocaleError):
        resolve_effective_locale(
            explicit="en-GB",
            accept_language=None,
            latest_user_query="hello",
        )


def test_header_first_supported_item_wins() -> None:
    """Unsupported header items are skipped in source order."""
    assert normalize_accept_language("fr, zh-Hans, en-US") == "zh-CN"
    assert normalize_accept_language("fr-FR") is None


def test_prompt_instruction_preserves_structured_values() -> None:
    """Prompt instructions constrain prose without changing identifiers."""
    for locale in ("en-US", "zh-CN"):
        instruction = locale_instruction(locale)
        assert "identifiers" in instruction
        assert "sequences" in instruction
        assert "numbers" in instruction
        assert "citations" in instruction
        assert "structured keys" in instruction


def test_localized_messages_are_fixed_and_bilingual() -> None:
    """Known messages contain no request-derived text."""
    english = message_for("unsupported_locale", "en-US")
    chinese = message_for("unsupported_locale", "zh-CN")
    assert english == "The requested locale is not supported."
    assert (
        chinese
        == "\u4e0d\u652f\u6301\u8bf7\u6c42\u7684\u8bed\u8a00\u533a\u57df\u3002"
    )
    with pytest.raises(KeyError, match="unknown localized message code"):
        message_for("unknown", "en-US")


def test_locale_context_token_restores_previous_value() -> None:
    """Nested locale bindings do not leak across request scopes."""
    outer = bind_effective_locale("zh-CN")
    try:
        assert current_effective_locale() == "zh-CN"
        inner = bind_effective_locale("en-US")
        try:
            assert current_effective_locale() == "en-US"
        finally:
            reset_request_var(inner)
        assert current_effective_locale() == "zh-CN"
    finally:
        reset_request_var(outer)
    assert current_effective_locale() == "en-US"
