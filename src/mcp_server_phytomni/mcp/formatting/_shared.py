# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Private scalar helpers shared by result-formatting leaves."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

_PHYTOMNI_STATE_KEY = "phytomni_state"
_METADATA_TEXT_TRUNCATE_BYTES = 4096


def payload_mapping(payload: Any) -> Mapping[str, Any]:
    """Return a mapping payload or an empty mapping for non-objects."""
    return payload if isinstance(payload, Mapping) else {}


def phytomni_state(content: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the ``phytomni_state`` mapping or an empty mapping."""
    state = content.get(_PHYTOMNI_STATE_KEY)
    return state if isinstance(state, Mapping) else {}


def first_message(content: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the first OpenAI-style message from a response payload."""
    choices = content.get("choices")
    if not isinstance(choices, Sequence) or isinstance(choices, str):
        return {}
    if not choices:
        return {}
    first_choice = choices[0]
    if not isinstance(first_choice, Mapping):
        return {}
    message = first_choice.get("message")
    return message if isinstance(message, Mapping) else {}


def follow_up_questions(message: Mapping[str, Any]) -> tuple[str, ...]:
    """Return normalized follow-up questions from a message payload."""
    questions = message.get("follow_up_questions")
    if not isinstance(questions, Sequence) or isinstance(questions, str):
        return ()
    return tuple(str(question) for question in questions)


def mapping_sequence(value: Any) -> tuple[Mapping[str, Any], ...]:
    """Return a sequence containing only mapping items."""
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def truncate_text(text: str, byte_limit: int, raw_pointer: str) -> str:
    """Return ``text`` truncated to ``byte_limit`` UTF-8 bytes."""
    encoded = text.encode("utf-8")
    if len(encoded) <= byte_limit:
        return text
    marker = f"…[truncated, see {raw_pointer}]"
    trimmed = encoded[:byte_limit].decode("utf-8", errors="ignore")
    return f"{trimmed}{marker}"


def normalize_compute_resource(value: str | None) -> str | None:
    """Return the persisted compute resource name."""
    resource_names = {
        "small": "analyst-agents-small",
        "medium": "analyst-agents-medium",
        "large": "analyst-agents-large",
    }
    if value is None:
        return None
    return resource_names.get(value, value)


def string_or_none(value: Any) -> str | None:
    """Return a stripped non-empty string value or None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def json_dumps(value: Any) -> str:
    """Serialize a value using the repository JSON conventions."""
    return json.dumps(value, ensure_ascii=False)


__all__ = [
    "_METADATA_TEXT_TRUNCATE_BYTES",
    "first_message",
    "follow_up_questions",
    "json_dumps",
    "mapping_sequence",
    "normalize_compute_resource",
    "payload_mapping",
    "phytomni_state",
    "string_or_none",
    "truncate_text",
]
