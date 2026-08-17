# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Each graph node emits a phyto.progress tick under an active writer."""

from __future__ import annotations

from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.brief_gene.analytical_sections import (
    _run_section_application_node,
    _run_section_cloning_node,
    _run_section_discovery_node,
    _run_section_functional_node,
)
from mcp_server_phytomni.agents.brief_gene.core import BriefGeneAgent
from mcp_server_phytomni.agents.brief_gene.render import (
    _render_preamble_node,
)
from mcp_server_phytomni.agents.data.agent import DataAgent
from mcp_server_phytomni.agents.knowledge.agent import KnowledgeAgent
from mcp_server_phytomni.agents.knowledge.retrieval import _retrieval_result
from mcp_server_phytomni.agents.knowledge.retrieval_result import (
    RetrievalResult,
)
from mcp_server_phytomni.agents.review.agent import DeepResearchAgent

pytestmark = pytest.mark.agent


def _install_writer(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Patch ``get_stream_writer`` so ``emit_progress`` writes to a list."""
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "mcp_server_phytomni.mcp.progress_events.get_stream_writer",
        lambda: seen.append,
    )
    return seen


def _phases(seen: list[dict[str, Any]]) -> list[str]:
    """Extract phase labels from progress events."""
    return [e["phase"] for e in seen if e.get("kind") == "phyto.progress"]


async def test_knowledge_retrieve_node_emits_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KnowledgeAgent.retrieve_node emits a 'retrieving' tick."""
    seen = _install_writer(monkeypatch)

    async def _fake_multi_retrieve(**_k: Any) -> RetrievalResult:
        return _retrieval_result([], [])

    monkeypatch.setattr(
        "mcp_server_phytomni.agents.knowledge.agent.multi_retrieve",
        _fake_multi_retrieve,
    )

    agent = KnowledgeAgent()
    state = cast(
        Any,
        {
            "user_query": "q",
            "repo_id_dict": {},
            "upload_context": "",
        },
    )
    await agent.retrieve_node(state)

    assert "retrieving" in _phases(seen)


async def test_knowledge_generate_post_node_emits_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KnowledgeAgent.generate_post_node emits a 'generating' tick."""
    seen = _install_writer(monkeypatch)

    agent = KnowledgeAgent()
    state = cast(
        Any,
        {
            "retrieved_docs": [],
            "chat_response": {"choices": [{"message": {"content": "answer"}}]},
        },
    )
    await agent.generate_post_node(state)

    assert "generating" in _phases(seen)


async def test_review_draft_reduce_node_emits_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepResearchAgent.draft_reduce_node emits a 'drafting' tick."""
    seen = _install_writer(monkeypatch)

    agent = DeepResearchAgent()
    state = cast(
        Any,
        {"draft_indexed_results": [(0, "content")]},
    )
    await agent.draft_reduce_node(state)

    assert "drafting" in _phases(seen)


async def test_review_summary_post_node_emits_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ReviewSummaryMixin.summary_post_node emits 'generating'."""
    seen = _install_writer(monkeypatch)

    agent = DeepResearchAgent()
    state = cast(
        Any,
        {
            "chat_response": {
                "choices": [{"message": {"content": "summary"}}]
            },
        },
    )
    await agent.summary_post_node(state)

    assert "generating" in _phases(seen)


async def test_review_retrieve_reduce_node_emits_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ReviewPlanningMixin.retrieve_reduce_node emits 'retrieving'."""
    seen = _install_writer(monkeypatch)

    agent = DeepResearchAgent()
    state = cast(
        Any,
        {
            "research_dimensions": ["dim1"],
            "retrieve_indexed_results": [(0, [])],
            "retrieve_failed_indices": [],
            "total_length": 0,
        },
    )
    await agent.retrieve_reduce_node(state)

    assert "retrieving" in _phases(seen)


async def test_review_revised_reduce_node_emits_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepResearchAgent.revised_reduce_node emits 'revising'."""
    seen = _install_writer(monkeypatch)

    agent = DeepResearchAgent()
    state = cast(
        Any,
        {
            "revised_indexed_results": [(0, "revised")],
            "draft_contents": ["draft"],
            "research_dimensions": ["dim1"],
        },
    )
    await agent.revised_reduce_node(state)

    assert "revising" in _phases(seen)


async def test_data_retrieve_post_node_emits_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DataAgent.retrieve_post_node emits a 'retrieving' tick."""
    seen = _install_writer(monkeypatch)

    agent = DataAgent()
    state = cast(
        Any,
        {
            "user_query": "q",
            "knowledge_response": {
                "retrieved_docs": [],
                "retrieval_outcome": "no_match",
                "final_response": {},
            },
        },
    )
    await agent.retrieve_post_node(state)

    assert "retrieving" in _phases(seen)


async def test_data_rewrite_post_node_emits_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DataAgent.rewrite_post_node emits a 'rewriting' tick."""
    seen = _install_writer(monkeypatch)

    agent = DataAgent()
    state = cast(
        Any,
        {
            "chat_response": {
                "choices": [{"message": {"content": "rewritten"}}]
            },
        },
    )
    await agent.rewrite_post_node(state)

    assert "rewriting" in _phases(seen)


async def test_data_search_node_emits_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DataAgent.search_node emits a 'querying' tick."""
    seen = _install_writer(monkeypatch)

    async def _fake_nl2sql(_req: Any) -> dict[str, Any]:
        return {"rows": []}

    monkeypatch.setattr(
        "mcp_server_phytomni.agents.data.agent.execute_nl2sql_request",
        _fake_nl2sql,
    )

    agent = DataAgent()
    state = cast(Any, {"is_rewrite": False, "user_query": "q"})
    await agent.search_node(state)

    assert "querying" in _phases(seen)


async def test_brief_gene_section_discovery_emits_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """brief_gene _run_section_discovery_node emits 'analyzing'."""
    seen = _install_writer(monkeypatch)

    async def _fake_llm(_state: Any, _path: str) -> str:
        return "md"

    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene"
        ".analytical_sections._call_section_llm",
        _fake_llm,
    )

    await _run_section_discovery_node(cast(Any, {}))

    assert "analyzing" in _phases(seen)


async def test_brief_gene_section_cloning_emits_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """brief_gene _run_section_cloning_node emits 'analyzing'."""
    seen = _install_writer(monkeypatch)

    async def _fake_llm(_state: Any, _path: str) -> str:
        return "md"

    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene"
        ".analytical_sections._call_section_llm",
        _fake_llm,
    )

    await _run_section_cloning_node(cast(Any, {}))

    assert "analyzing" in _phases(seen)


async def test_brief_gene_section_functional_emits_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """brief_gene _run_section_functional_node emits 'analyzing'."""
    seen = _install_writer(monkeypatch)

    async def _fake_llm(_state: Any, _path: str) -> str:
        return "md"

    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene"
        ".analytical_sections._call_section_llm",
        _fake_llm,
    )

    await _run_section_functional_node(cast(Any, {}))

    assert "analyzing" in _phases(seen)


async def test_brief_gene_section_application_emits_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """brief_gene _run_section_application_node emits 'analyzing'."""
    seen = _install_writer(monkeypatch)

    async def _fake_llm(_state: Any, _path: str) -> str:
        return "md"

    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene"
        ".analytical_sections._call_section_llm",
        _fake_llm,
    )

    await _run_section_application_node(cast(Any, {}))

    assert "analyzing" in _phases(seen)


async def test_brief_gene_fetch_annotation_emits_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BriefGeneAgent.fetch_annotation_node emits 'annotating'."""
    seen = _install_writer(monkeypatch)

    async def _fake_bi(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"rows": []}

    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene.core.run_bi_api",
        _fake_bi,
    )

    agent = BriefGeneAgent()
    state = cast(
        Any,
        {"gene_id": "g1", "species_code": "sc", "user_query": "q"},
    )
    await agent.fetch_annotation_node(state)

    assert "annotating" in _phases(seen)


async def test_brief_gene_retrieve_reduce_emits_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BriefGene retrieve_reduce_node emits 'retrieving'."""
    seen = _install_writer(monkeypatch)

    agent = BriefGeneAgent()
    state = cast(
        Any,
        {
            "retrieve_tasks": [],
            "retrieve_indexed_results": [],
            "retrieve_failed_indices": [],
        },
    )
    await agent.retrieve_reduce_node(state)

    assert "retrieving" in _phases(seen)


async def test_brief_gene_render_node_emits_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """brief_gene _render_preamble_node emits 'generating'."""
    seen = _install_writer(monkeypatch)

    state = cast(
        Any,
        {
            "gene_id": "g1",
            "user_query": "q",
            "introduction_report": "",
            "section1_markdown": "",
            "section2_markdown": "",
            "section3_markdown": "",
            "section4_markdown": "",
            "literature_degraded": [],
        },
    )
    _render_preamble_node(state)

    assert "generating" in _phases(seen)
