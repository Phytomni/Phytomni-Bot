# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared fail-closed validation for public execution data.

V1 compatibility events and the V2 journal use this single boundary so a new
contract cannot accidentally weaken redaction while adding event types.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime

FORBIDDEN_PUBLIC_KEYS = frozenset(
    {
        "api_key",
        "arguments",
        "authorization",
        "authorization_header",
        "chain_of_thought",
        "console_output",
        "credential",
        "credentials",
        "encrypted_reasoning",
        "exception_text",
        "headers",
        "internal_path",
        "password",
        "path",
        "prompt",
        "provider_body",
        "provider_payload",
        "query",
        "raw_log",
        "raw_reasoning",
        "result",
        "secret",
        "source_passage",
        "sql",
        "stderr",
        "stdout",
        "storage_key",
        "system_prompt",
        "token",
        "tool_args",
        "tool_arguments",
        "tool_result",
        "tool_results",
        "url",
    }
)

FORBIDDEN_PUBLIC_VALUE_PATTERNS = (
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"\bbearer\s+[a-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(
        r"\b(?:password|passwd|api[_-]?key|secret|token)\s*[:=]",
        re.IGNORECASE,
    ),
    re.compile(r"\b[a-z]:[\\/]", re.IGNORECASE),
    re.compile(
        r"(?<![a-z0-9._-])/(?:[a-z0-9._-]+/)+[a-z0-9._-]+",
        re.IGNORECASE,
    ),
)


class PublicExecutionDataError(ValueError):
    """A value cannot cross the public execution-data boundary."""


def is_utc_timestamp(value: object) -> bool:
    """Return whether a value is an ISO-8601 timestamp at UTC offset zero."""
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    offset = parsed.utcoffset()
    return offset is not None and offset.total_seconds() == 0


def validate_public_execution_value(
    value: object,
    *,
    max_string_chars: int,
) -> None:
    """Recursively reject private keys, unsafe text, and unbounded types."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise PublicExecutionDataError("forbidden_public_payload")
            try:
                key.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise PublicExecutionDataError("invalid_public_utf8") from exc
            if key.casefold() in FORBIDDEN_PUBLIC_KEYS:
                raise PublicExecutionDataError("forbidden_public_payload")
            validate_public_execution_value(
                child,
                max_string_chars=max_string_chars,
            )
        return
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for child in value:
            validate_public_execution_value(
                child,
                max_string_chars=max_string_chars,
            )
        return
    if isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise PublicExecutionDataError("invalid_public_utf8") from exc
        if len(value) > max_string_chars:
            raise PublicExecutionDataError("public_string_too_large")
        if any(
            pattern.search(value)
            for pattern in FORBIDDEN_PUBLIC_VALUE_PATTERNS
        ):
            raise PublicExecutionDataError("forbidden_public_value")
        return
    if value is not None and not isinstance(value, (int, float, bool)):
        raise PublicExecutionDataError("forbidden_public_payload")


def validate_public_execution_size(value: str, *, max_bytes: int) -> None:
    """Reject invalid UTF-8 and serialized values above their byte budget."""
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise PublicExecutionDataError("invalid_public_utf8") from exc
    if len(encoded) > max_bytes:
        raise PublicExecutionDataError("event_payload_too_large")
