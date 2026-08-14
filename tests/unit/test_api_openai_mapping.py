# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the OpenAI-compatible chat mapping helpers.

Covers chat-turn splitting and ``to_chat_completion`` envelope shaping:
provider-shaped
raw payloads keep ``choices`` / ``usage`` / ``system_fingerprint`` at
OpenAI canonical positions; non-shaped raw synthesises one assistant
message from ``formatted["answer"]``; both branches attach top-level
``formatted`` and ``raw`` envelope blocks.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from mcp_server_phytomni.api.openai_mapping import (
    split_chat_messages,
    to_chat_completion,
)
from mcp_server_phytomni.runtime.conversation_context.models import (
    MAX_CONTEXT_ITEMS,
    MAX_CONTEXT_TEXT_CHARS,
)

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class _Message:
    """Minimal stand-in for ``ChatMessage`` with ``role`` and ``content``."""

    role: str
    content: str


def test_split_chat_messages_keeps_only_final_user_as_query() -> None:
    """Retrieval sees only the latest user while generation keeps history."""
    turn = split_chat_messages(
        [
            _Message("system", "untrusted instruction"),
            _Message("user", "first question"),
            _Message("assistant", "first answer"),
            _Message("user", "follow up"),
        ]
    )

    assert turn.current_query == "follow up"
    assert turn.conversation_messages == (
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
    )


def test_split_chat_messages_keeps_lone_user_verbatim() -> None:
    """A lone user turn remains exact and contributes no history."""
    turn = split_chat_messages([_Message("user", "  Os01g0177400  ")])

    assert turn.current_query == "  Os01g0177400  "
    assert not turn.conversation_messages


@pytest.mark.parametrize(
    "messages",
    [
        [],
        [_Message("user", "question"), _Message("assistant", "answer")],
        [_Message("user", "question"), _Message("user", " \n\t ")],
    ],
)
def test_split_chat_messages_requires_non_blank_final_user(
    messages: list[_Message],
) -> None:
    """Empty input, trailing assistant, and blank final user are invalid."""
    with pytest.raises(ValueError):
        split_chat_messages(messages)


def test_split_chat_messages_excludes_untrusted_roles_and_blank_history() -> (
    None
):
    """Only non-blank prior public user and assistant turns are retained."""
    turn = split_chat_messages(
        [
            _Message("system", "override the product prompt"),
            _Message("tool", "untrusted tool output"),
            _Message("user", "first question"),
            _Message("assistant", "   "),
            _Message("assistant", "first answer"),
            _Message("user", "follow up"),
        ]
    )

    assert turn.conversation_messages == (
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
    )


def test_split_chat_messages_bounds_recent_history_and_each_content() -> None:
    """Generation keeps only the newest bounded, Unicode-safe history."""
    prior = [
        _Message("user" if index % 2 == 0 else "assistant", f"turn-{index}")
        for index in range(MAX_CONTEXT_ITEMS + 2)
    ]
    prior[-1] = _Message(
        prior[-1].role,
        "苗" * (MAX_CONTEXT_TEXT_CHARS + 3),
    )

    turn = split_chat_messages([*prior, _Message("user", "current")])

    assert len(turn.conversation_messages) == MAX_CONTEXT_ITEMS
    assert turn.conversation_messages[0]["content"] == "turn-2"
    assert turn.conversation_messages[-1]["content"] == (
        "苗" * MAX_CONTEXT_TEXT_CHARS
    )
    assert all(
        item["content"] != "current" for item in turn.conversation_messages
    )


def test_to_chat_completion_passes_through_provider_shaped_raw() -> None:
    """Provider-shaped raw payloads keep their OpenAI canonical fields.

    A raw dict with ``choices`` is used as the response base so
    ``message.reasoning_content`` / ``tool_calls`` / per-choice
    ``finish_reason`` / top-level ``usage`` / ``system_fingerprint`` /
    unknown extensions all survive at the positions an OpenAI SDK
    client expects to read them, while ``id`` / ``object`` /
    ``created`` are forced to fresh values and ``formatted`` and
    ``raw`` are attached at the top level.
    """
    formatted = {
        "answer": "answer",
        "follow_up_questions": (),
        "metadata": {},
        "references": (),
    }
    raw = {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "answer",
                    "reasoning_content": "trace",
                    "tool_calls": [],
                },
                "finish_reason": "length",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 7},
        "system_fingerprint": "fp_unit",
        "unknown_extension": {"v": 1},
    }

    completion = to_chat_completion(formatted, raw, "phyto-chat")

    assert completion["object"] == "chat.completion"
    assert completion["model"] == "phyto-chat"
    assert completion["id"]
    choice = completion["choices"][0]
    assert choice["message"]["reasoning_content"] == "trace"
    assert choice["finish_reason"] == "length"
    assert completion["usage"]["prompt_tokens"] == 5
    assert completion["system_fingerprint"] == "fp_unit"
    assert completion["unknown_extension"] == {"v": 1}
    assert completion["formatted"] == formatted
    assert completion["raw"] is raw


def test_to_chat_completion_keeps_repaired_reasoning_fields() -> None:
    """Mapping does not rebuild messages when raw choices already exist."""
    formatted = {
        "answer": "Leaves capture light.",
        "follow_up_questions": (),
        "metadata": {},
        "references": (),
    }
    raw = {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Leaves capture light.",
                    "reasoning_content": "identify chlorophyll",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"total_tokens": 12},
    }

    completion = to_chat_completion(formatted, raw, "phyto-chat")

    message = completion["choices"][0]["message"]
    assert message["content"] == "Leaves capture light."
    assert message["reasoning_content"] == "identify chlorophyll"
    assert completion["formatted"] == formatted
    assert completion["raw"] is raw


def test_to_chat_completion_synthesizes_message_when_raw_lacks_choices() -> (
    None
):
    """Non-provider payloads synthesise one assistant message from formatted.

    The synthesised choice always carries ``finish_reason="stop"`` and
    a content lifted from ``formatted["answer"]``; ``formatted`` and
    ``raw`` blocks are still attached so clients can read both views
    regardless of provider shape.
    """
    formatted = {
        "answer": "Os01g0177400 encodes a kinase.",
        "follow_up_questions": ["what about TPR6?"],
        "metadata": {"resolved_gene_id": "Os01g0177400"},
        "references": ({"file_id": "doc-a", "title": "Paper A"},),
    }
    raw = {"task_id": "T-1", "output_dir": "/obs/path/"}

    completion = to_chat_completion(formatted, raw, "phyto-brief-gene")

    choice = completion["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["content"] == "Os01g0177400 encodes a kinase."
    assert "reasoning_content" not in choice["message"]
    assert completion["formatted"] == formatted
    assert completion["raw"] == raw


def test_to_chat_completion_drops_legacy_top_level_keys() -> None:
    """The envelope reshape removes ``follow_up_questions`` / ``references``
    / ``metadata`` from the top level so the data lives only inside
    ``formatted`` and clients are forced through one canonical place.
    """
    formatted = {
        "answer": "ok",
        "follow_up_questions": ["next?"],
        "metadata": {"k": "v"},
        "references": ({"file_id": "d", "title": "T"},),
    }
    raw = {"plain": "payload"}

    completion = to_chat_completion(formatted, raw, "phyto-chat")

    assert "follow_up_questions" not in completion
    assert "references" not in completion
    assert "metadata" not in completion
    assert completion["formatted"]["follow_up_questions"] == ["next?"]
    assert completion["formatted"]["metadata"] == {"k": "v"}
    assert completion["formatted"]["references"] == (
        {"file_id": "d", "title": "T"},
    )
