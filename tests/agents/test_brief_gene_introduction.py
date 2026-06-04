# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for brief_gene introduction LLM node.

``_run_introduction_node`` produces ``introduction_report`` by
calling the ``brief_gene_introduction`` prompt with section1-4
markdown context (happy path, ``gene_found=True``) or with
``retrieve_context`` only (``gene_found=False`` D5.a degraded
variant).
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from mcp_server_phytomni.agents.brief_gene.introduction import (
    _run_introduction_node,
)

pytestmark = pytest.mark.agent


def _happy_state() -> dict[str, Any]:
    return {
        "gene_found": True,
        "gene_id": "Os01g0177400",
        "species_code": "osa",
        "species_english_name": "rice",
        "gene_name_symbol_list": ["OsCAB1"],
        "section1_markdown": "### 1. Discovery\n\nSection 1 content.",
        "section2_markdown": "### 2. Cloning\n\nSection 2 content.",
        "section3_markdown": "### 3. Functional\n\nSection 3 content.",
        "section4_markdown": "### 4. Application\n\nSection 4 content.",
        "retrieve_context": "[document:1] reference text",
        "retrieved_docs": [{"id": "doc1"}],
    }


def _degraded_state() -> dict[str, Any]:
    return {
        "gene_found": False,
        "user_query": "Unknown plant gene",
        "retrieve_context": "[document:1] some reference",
        "retrieved_docs": [{"id": "doc1"}],
    }


def _mock_chat(text: str) -> AsyncMock:
    return AsyncMock(
        return_value={"choices": [{"message": {"content": text}}]}
    )


@pytest.mark.asyncio
async def test_introduction_node_happy_path_uses_section_context() -> None:
    """gene_found=True: introduction LLM receives section1-4 markdowns.

    The introduction is post-hoc — it summarizes the four sections
    just produced. The rendered user_query must include the section
    markdowns so the LLM can write a 3-5 paragraph intro grounded
    in the analytical body.
    """
    mock_chat = _mock_chat("Intro paragraphs based on sections.")
    with patch(
        "mcp_server_phytomni.agents.brief_gene.introduction.phyto_chat",
        new=mock_chat,
    ):
        delta = await _run_introduction_node(cast(Any, _happy_state()))

    assert delta["introduction_report"].startswith("Intro paragraphs")
    rendered_query = mock_chat.call_args.kwargs.get("user_query", "")
    assert "Section 1 content" in rendered_query
    assert "Section 4 content" in rendered_query


@pytest.mark.asyncio
async def test_introduction_node_degraded_path_uses_lit_only() -> None:
    """gene_found=False: introduction LLM receives only lit context.

    D5.a variant — when gene_id resolution failed, the intro is
    based on literature retrieved against the free-form query.
    The rendered user_query excludes section markdowns since they
    do not exist on this path.
    """
    mock_chat = _mock_chat("Lit-only narrative.")
    with patch(
        "mcp_server_phytomni.agents.brief_gene.introduction.phyto_chat",
        new=mock_chat,
    ):
        delta = await _run_introduction_node(cast(Any, _degraded_state()))

    assert delta["introduction_report"] == "Lit-only narrative."
    rendered_query = mock_chat.call_args.kwargs.get("user_query", "")
    assert "[document:1] some reference" in rendered_query


@pytest.mark.asyncio
async def test_introduction_node_empty_sections_fallback() -> None:
    """gene_found=True with empty sections degrades gracefully.

    brief_gene may run with empty section content if any of the 4
    section LLM calls produced an empty string. The introduction
    node must not crash on empty section_markdown values.
    """
    state = _happy_state()
    state["section1_markdown"] = ""
    state["section2_markdown"] = ""
    state["section3_markdown"] = ""
    state["section4_markdown"] = ""

    mock_chat = _mock_chat("Intro despite empty sections.")
    with patch(
        "mcp_server_phytomni.agents.brief_gene.introduction.phyto_chat",
        new=mock_chat,
    ):
        delta = await _run_introduction_node(cast(Any, state))

    assert delta["introduction_report"] == "Intro despite empty sections."
