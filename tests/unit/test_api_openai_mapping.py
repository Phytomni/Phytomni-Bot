# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the OpenAI-compatible chat mapping helpers.

Covers ``flatten_messages`` for single / multi message normalisation
and ``to_chat_completion`` for the envelope shape: provider-shaped
raw payloads keep ``choices`` / ``usage`` / ``system_fingerprint`` at
OpenAI canonical positions; non-shaped raw synthesises one assistant
message from ``formatted["answer"]``; both branches attach top-level
``formatted`` and ``raw`` envelope blocks.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from mcp_server_phytomni.api.openai_mapping import (
    flatten_messages,
    to_chat_completion,
)

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class _Message:
    """Minimal stand-in for ``ChatMessage`` with ``role`` and ``content``."""

    role: str
    content: str


def test_single_user_message_is_verbatim() -> None:
    """A lone user message must be returned unchanged.

    Identifier-driven tools (BriefGene takes a gene/transcript id) need
    the user_query verbatim; prefixing it with ``user: `` corrupts the
    lookup and forces the LLM to hallucinate an unrelated gene.
    """
    result = flatten_messages([_Message("user", "Os01g0177400")])

    assert result == "Os01g0177400"


def test_multi_message_keeps_role_prefix() -> None:
    """Multi-turn conversations keep ``role:`` prefixes for turn context."""
    result = flatten_messages(
        [
            _Message("system", "be brief"),
            _Message("user", "what is photosynthesis?"),
        ]
    )

    assert result == "system: be brief\n\nuser: what is photosynthesis?"


def test_missing_user_message_raises() -> None:
    """A message list without any user turn is rejected."""
    with pytest.raises(ValueError):
        flatten_messages([_Message("system", "be brief")])


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
