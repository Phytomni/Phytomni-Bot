# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Normalize misplaced reasoning/content fields from chat completions.

Functions: normalize_message_fields, normalize_chat_completion_dict.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"


def normalize_message_fields(
    content: str | None,
    reasoning_content: str | None,
) -> tuple[str, str | None, bool]:
    """Return corrected message content and reasoning fields.

    Some OpenAI-compatible reasoner providers occasionally place the
    final answer after a closed ``<think>...</think>`` block in the
    wrong field. This helper only repairs that narrow shape.

    Args:
        content: Assistant ``message.content`` value.
        reasoning_content: Assistant ``message.reasoning_content`` value.

    Returns:
        Tuple of ``(content, reasoning_content, changed)``.
    """
    content_text = "" if content is None else content
    reasoning_text = reasoning_content

    if reasoning_text:
        split = _split_tagged_tail(reasoning_text)
        if split is not None:
            reasoning, tail = split
            content_stripped = content_text.strip()
            if not content_stripped or content_stripped == tail:
                return tail, reasoning or None, True

    if not reasoning_text:
        split = _split_prefix_tagged_tail(content_text)
        if split is not None:
            reasoning, tail = split
            return tail, reasoning or None, True

    return content_text, reasoning_text, False


def normalize_chat_completion_dict(payload: Any) -> Any:
    """Return ``payload`` with misplaced chat message fields repaired.

    Clean payloads are returned by identity. A deep copy is made only
    when at least one ``choices[*].message`` needs rewriting.

    Args:
        payload: Potential OpenAI-style ChatCompletion dictionary.

    Returns:
        Original payload for no-op cases, otherwise a repaired copy.
    """
    if not isinstance(payload, dict):
        return payload
    changes: list[tuple[int, str, str | None]] = []
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return payload
    for index, choice in enumerate(choices):
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        reasoning = message.get("reasoning_content")
        if content is not None and not isinstance(content, str):
            continue
        if reasoning is not None and not isinstance(reasoning, str):
            continue
        new_content, new_reasoning, changed = normalize_message_fields(
            content,
            reasoning,
        )
        if changed:
            changes.append((index, new_content, new_reasoning))

    if not changes:
        return payload

    normalized = deepcopy(payload)
    for index, content, reasoning in changes:
        message = normalized["choices"][index]["message"]
        message["content"] = content
        if reasoning is None:
            message.pop("reasoning_content", None)
        else:
            message["reasoning_content"] = reasoning
    return normalized


def _split_prefix_tagged_tail(text: str) -> tuple[str, str] | None:
    """Split a leading ``<think>...</think>`` block plus answer tail."""
    stripped = text.lstrip()
    if not stripped.startswith(_THINK_OPEN):
        return None
    return _split_tagged_tail(stripped)


def _split_tagged_tail(text: str) -> tuple[str, str] | None:
    """Return tag body and non-empty tail from one closed think block."""
    start = text.find(_THINK_OPEN)
    if start == -1:
        return None
    close = text.find(_THINK_CLOSE, start + len(_THINK_OPEN))
    if close == -1:
        return None
    body_start = start + len(_THINK_OPEN)
    tail_start = close + len(_THINK_CLOSE)
    tail = text[tail_start:].strip()
    if not tail:
        return None
    return text[body_start:close].strip(), tail
