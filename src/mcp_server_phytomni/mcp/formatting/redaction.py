# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Credential redaction and default/debug response projections."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from typing import Any

_SECRET_KEY_PATTERNS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "secret",
        "token",
        "bearer",
        "authorization",
        "session_id",
        "password",
        "credential",
    }
)
_NON_SECRET_OVERRIDES: frozenset[str] = frozenset(
    {
        "tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "max_tokens",
        "max_completion_tokens",
        "n_tokens",
    }
)


def _is_sensitive_key(key: Any) -> bool:
    """Return True when a mapping or dataclass field name carries a secret."""
    if not isinstance(key, str) or not key:
        return False
    lowered = key.lower()
    if lowered in _NON_SECRET_OVERRIDES:
        return False
    return any(pattern in lowered for pattern in _SECRET_KEY_PATTERNS)


is_sensitive_key = _is_sensitive_key


def sanitize_raw(payload: Any) -> Any:
    """Recursively remove credential-pattern fields from a payload.

    Mappings become fresh dictionaries; lists and tuples retain their
    sequence type. Dataclass instances become field mappings so sensitive
    fields can be dropped, while unknown scalar/object values pass through
    unchanged.
    """
    if is_dataclass(payload) and not isinstance(payload, type):
        return {
            item.name: sanitize_raw(getattr(payload, item.name))
            for item in fields(payload)
            if not _is_sensitive_key(item.name)
        }
    if isinstance(payload, Mapping):
        return {
            key: sanitize_raw(value)
            for key, value in payload.items()
            if not _is_sensitive_key(key)
        }
    if isinstance(payload, list):
        return [sanitize_raw(item) for item in payload]
    if isinstance(payload, tuple):
        return tuple(sanitize_raw(item) for item in payload)
    return payload


_DEBUG_ENV = "PHYTOMNI_DEBUG"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def resolve_debug(per_request: bool | None) -> bool:
    """Return True when the operator env or request flag enables debug."""
    raw = os.getenv(_DEBUG_ENV, "").strip().lower()
    return raw in _TRUTHY or bool(per_request)


def strip_agent_result(result: dict) -> dict:
    """Remove ``raw`` from a result without mutating the original."""
    return {key: value for key, value in result.items() if key != "raw"}


_CHAT_COMPLETION_KEEP = frozenset(
    {
        "id",
        "object",
        "created",
        "model",
        "choices",
        "usage",
        "formatted",
        "execution",
        "run_id",
        "degraded_tracking",
    }
)
_MESSAGE_KEEP = frozenset(
    {
        "role",
        "content",
        "reasoning_content",
        "tool_calls",
        "finish_reason",
        "index",
    }
)
_USAGE_KEEP = frozenset(
    {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    }
)


def strip_chat_completion(completion: dict) -> dict:
    """Project an OpenAI completion into its default public shape."""
    normalized_answer = _extract_formatted_answer(completion)
    result = {
        key: value
        for key, value in completion.items()
        if key in _CHAT_COMPLETION_KEEP
    }
    if "choices" in result:
        result["choices"] = [
            _strip_choice(choice, normalized_answer)
            for choice in result["choices"]
        ]
    if "usage" in result and isinstance(result["usage"], dict):
        result["usage"] = {
            key: value
            for key, value in result["usage"].items()
            if key in _USAGE_KEEP
        }
    if "formatted" in result and isinstance(result["formatted"], dict):
        result["formatted"] = {
            key: value
            for key, value in result["formatted"].items()
            if key != "answer"
        }
    return result


def _extract_formatted_answer(completion: dict) -> str | None:
    """Read ``formatted.answer`` for content normalization."""
    formatted = completion.get("formatted")
    if isinstance(formatted, dict):
        answer = formatted.get("answer")
        if isinstance(answer, str):
            return answer
    return None


def _strip_choice(choice: dict, normalized_answer: str | None) -> dict:
    """Keep standard fields in one choice and normalize its message."""
    stripped = {
        key: value for key, value in choice.items() if key != "message"
    }
    message = choice.get("message")
    if isinstance(message, dict):
        clean_message = {
            key: value
            for key, value in message.items()
            if key in _MESSAGE_KEEP
        }
        if normalized_answer is not None:
            clean_message["content"] = normalized_answer
        stripped["message"] = clean_message
    return stripped


__all__ = [
    "is_sensitive_key",
    "resolve_debug",
    "sanitize_raw",
    "strip_agent_result",
    "strip_chat_completion",
]
