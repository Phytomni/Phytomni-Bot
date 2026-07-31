# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for localized public lifecycle error messages."""

from __future__ import annotations

from mcp_server_phytomni.api.lifecycle_contract import (
    SafeErrorCode,
    conversation_context_unavailable_error,
)
from mcp_server_phytomni.runtime.locale import SUPPORTED_LOCALES, message_for


def test_every_public_lifecycle_error_has_localized_safe_text() -> None:
    """Every lifecycle error code has bounded locale-specific public text."""
    for error_code in SafeErrorCode:
        for locale in SUPPORTED_LOCALES:
            message = message_for(error_code.value, locale)
            assert message
            assert error_code.value not in message


def test_context_store_unavailable_is_retryable_and_sanitized() -> None:
    """Pre-outcome context failure has one stable public projection."""
    error = conversation_context_unavailable_error()

    assert error.status_code == 503
    assert error.code == SafeErrorCode.CONVERSATION_CONTEXT_UNAVAILABLE.value
    assert error.message == "conversation context unavailable"
    assert error.stage == "context"
    assert error.retryable is True
