# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Classify memory-class remote failures and the next compute tier."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

__all__ = [
    "START_COMPUTE_RESOURCE",
    "TIERS",
    "is_memory_class_failure",
    "next_compute_resource",
]

START_COMPUTE_RESOURCE = "small"
TIERS = ("small", "medium", "large")

_MEMORY_TOKENS = (
    "memoryerror",
    "out of memory",
    "oom",
    "killed",
    "cannot allocate",
    "std::bad_alloc",
    "exit code 137",
    "signal 9",
)


def next_compute_resource(current: str) -> str | None:
    """Return the next compute tier, or None at large / unknown names.

    Args:
        current: Persisted compute-resource name for one child.

    Returns:
        The next allowlisted tier, or ``None`` when ``current`` is
        ``large`` or not one of ``TIERS``.
    """
    try:
        index = TIERS.index(current)
    except ValueError:
        return None
    nxt = index + 1
    if nxt >= len(TIERS):
        return None
    return TIERS[nxt]


def is_memory_class_failure(
    status_payload: object,
    log_payload: object = None,
) -> bool:
    """Return whether status/log text matches a memory-class failure.

    A bare ``FAILED`` status is not memory-class. Tokens are matched as
    case-insensitive substrings of one haystack built from status values,
    ``logs[].content``, and the log ``text`` field.

    Args:
        status_payload: Live task-status body, or ``None``.
        log_payload: Optional task-log body.

    Returns:
        True when a documented memory-class token is present.
    """
    haystack = " ".join(
        _haystack_parts(status_payload, log_payload)
    ).lower()
    if not haystack.strip():
        return False
    return any(token in haystack for token in _MEMORY_TOKENS)


def _haystack_parts(
    status_payload: object, log_payload: object
) -> tuple[str, ...]:
    """Collect stringified status values plus log content and text."""
    parts: list[str] = []
    parts.extend(_status_values(status_payload))
    parts.extend(_log_texts(status_payload))
    parts.extend(_log_texts(log_payload))
    return tuple(parts)


def _status_values(payload: object) -> tuple[str, ...]:
    """Stringify status-payload values without treating keys as evidence."""
    if payload is None:
        return ()
    if isinstance(payload, Mapping):
        collected: list[str] = []
        for value in payload.values():
            collected.extend(_flatten_strings(value))
        return tuple(collected)
    if isinstance(payload, (bytes, bytearray)):
        return ()
    return (str(payload),)


def _log_texts(payload: object) -> tuple[str, ...]:
    """Return ``logs[].content`` strings and the log ``text`` field."""
    if isinstance(payload, str):
        return (payload,)
    if not isinstance(payload, Mapping):
        return ()
    parts: list[str] = []
    logs = payload.get("logs")
    if isinstance(logs, list):
        for item in logs:
            if not isinstance(item, Mapping):
                continue
            content = item.get("content")
            if isinstance(content, str):
                parts.append(content)
    text = payload.get("text")
    if isinstance(text, str):
        parts.append(text)
    return tuple(parts)


def _flatten_strings(value: object) -> tuple[str, ...]:
    """Walk mappings and sequences into string fragments."""
    if value is None or isinstance(value, (bytes, bytearray)):
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Mapping):
        collected: list[str] = []
        for item in value.values():
            collected.extend(_flatten_strings(item))
        return tuple(collected)
    if isinstance(value, Sequence):
        collected = []
        for item in value:
            collected.extend(_flatten_strings(item))
        return tuple(collected)
    return (str(value),)
