# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Structural success policy for cached Chat completions."""

from typing import Any, Literal

__all__ = [
    "InvalidChatCompletionError",
    "is_cacheable_chat_completion",
    "require_successful_chat_completion",
]

_InvalidReason = Literal[
    "invalid_payload",
    "top_level_error",
    "missing_choices",
    "invalid_choice",
    "invalid_message",
]

_CANONICAL_OUTPUT_KEYS = frozenset(
    {
        "content",
        "reasoning_content",
        "tool_calls",
        "function_call",
        "refusal",
    }
)


class InvalidChatCompletionError(Exception):
    """Safe internal signal for one invalid completion structure."""

    def __init__(self, reason: _InvalidReason) -> None:
        """Store only a fixed structural reason.

        Args:
            reason: Fixed reason identifying the rejected structure.
        """
        self.reason = reason
        super().__init__(reason)


def _invalid_reason(payload: Any) -> _InvalidReason | None:
    """Return a fixed reason when a completion is not cacheable."""
    if not isinstance(payload, dict):
        return "invalid_payload"
    if _has_top_level_error(payload):
        return "top_level_error"
    return _choices_error(payload)


def _has_top_level_error(payload: dict[str, Any]) -> bool:
    """Return whether a provider set any explicit failure marker."""
    return (
        bool(payload.get("error"))
        or bool(payload.get("error_msg"))
        or not _is_success_error_code(payload.get("error_code"))
    )


def _choices_error(payload: dict[str, Any]) -> _InvalidReason | None:
    """Return a fixed reason for an invalid first-choice structure."""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return "missing_choices"
    if not isinstance(choices[0], dict):
        return "invalid_choice"
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return "invalid_message"
    if not _CANONICAL_OUTPUT_KEYS.intersection(message):
        return "invalid_message"
    return None


def _is_success_error_code(value: Any) -> bool:
    """Return whether a legacy error code is an explicit success sentinel."""
    if value is None:
        return True
    if isinstance(value, str):
        return value in {"", "0"}
    return (
        isinstance(value, int) and not isinstance(value, bool) and value == 0
    )


def is_cacheable_chat_completion(payload: Any) -> bool:
    """Return whether a payload satisfies the cache success policy."""
    return _invalid_reason(payload) is None


def require_successful_chat_completion(
    payload: Any,
) -> dict[str, Any]:
    """Return a valid payload or raise a reason-only internal error.

    Args:
        payload: Normalized provider completion candidate.

    Returns:
        Structurally valid completion dictionary.

    Raises:
        InvalidChatCompletionError: If the payload is structurally invalid.
    """
    reason = _invalid_reason(payload)
    if reason is not None:
        raise InvalidChatCompletionError(reason)
    return payload
