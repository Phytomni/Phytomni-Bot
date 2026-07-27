# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Private native-role conversation-history helpers for agent internals."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def normalize_conversation_messages(
    conversation_messages: Sequence[Mapping[str, object]] | None = None,
) -> list[dict[str, str]]:
    """Return bounded native-role history in provider-ready dict form.

    Only ``user`` and ``assistant`` turns with string content survive. The
    helper is private-history plumbing for internal graph/model inputs and
    intentionally does not add fields to public MCP or HTTP schemas.
    """
    if conversation_messages is None:
        return []
    normalized: list[dict[str, str]] = []
    for message in conversation_messages:
        role = message.get("role")
        content = message.get("content")
        if role in {"user", "assistant"} and isinstance(content, str):
            normalized.append({"role": role, "content": content})
    return normalized


def build_model_messages(
    *,
    system_prompt: str,
    user_query: str,
    conversation_messages: Sequence[Mapping[str, object]] | None = None,
) -> list[dict[str, str]]:
    """Build the non-streaming provider message list with prior turns."""
    return [
        {"role": "system", "content": system_prompt},
        *normalize_conversation_messages(conversation_messages),
        {"role": "user", "content": user_query},
    ]
