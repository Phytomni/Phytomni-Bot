# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the OpenAI-compatible chat mapping helpers.

Covers ``flatten_messages``: a lone user message is returned verbatim
so identifier-driven tools (BriefGene takes a gene/transcript id) see
the raw content, while multi-message conversations keep ``role: ``
prefixes to preserve turn context.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from mcp_server_phytomni.api.openai_mapping import flatten_messages

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
