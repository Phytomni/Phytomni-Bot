# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Contracts for the globally optimized routing descriptions."""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest
from scripts.agent_routing_eval.dataset import WorkbookSource, load_dataset

from mcp_server_phytomni.mcp.schemas import (
    AGENT_TOOL_DEFINITIONS,
    AnalystAgent,
    BriefGeneAgent,
    ChatAgent,
    DataAgent,
    DeepGenomeAgent,
    DigitalDesignAgent,
    GeneNetworkAgent,
    InSilicoResearchAgent,
    KnowledgeAgent,
    PhytomniAgents,
    ReviewAgent,
)

pytestmark = pytest.mark.unit

_ROOT: Final = Path(__file__).resolve().parents[3]
_DATASET_PATHS: Final = (
    _ROOT / "evaluation/agent_routing/datasets/dev_v1.jsonl",
    _ROOT / "evaluation/agent_routing/datasets/test_v1.jsonl",
)
_EXPECTED_TOOLS: Final = (
    (PhytomniAgents.CHAT_AGENT, ChatAgent),
    (PhytomniAgents.KNOWLEDGE_AGENT, KnowledgeAgent),
    (PhytomniAgents.DATA_AGENT, DataAgent),
    (PhytomniAgents.ANALYST_AGENT, AnalystAgent),
    (PhytomniAgents.REVIEW_AGENT, ReviewAgent),
    (PhytomniAgents.BRIEF_GENE_AGENT, BriefGeneAgent),
    (PhytomniAgents.DEEP_GENOME_AGENT, DeepGenomeAgent),
    (PhytomniAgents.IN_SILICO_RESEARCH_AGENT, InSilicoResearchAgent),
    (PhytomniAgents.DIGITAL_DESIGN_AGENT, DigitalDesignAgent),
    (PhytomniAgents.GENE_NETWORK_AGENT, GeneNetworkAgent),
)
_FORBIDDEN_MARKERS: Final = ("xxx", "to:xxx", "tbd", "todo")


def _descriptions() -> tuple[str, ...]:
    """Return the ordered public description text."""
    return tuple(
        str(description)
        for _name, description, _model in AGENT_TOOL_DEFINITIONS
    )


def _evaluation_literals() -> tuple[str, ...]:
    """Return exact benchmark literals forbidden in descriptions."""
    literals: set[str] = set()
    for path in _DATASET_PATHS:
        for case in load_dataset(path):
            literals.add(case.question.strip())
            if isinstance(case.source, WorkbookSource):
                literals.add(case.source.source_id.strip())
            if (
                case.expected_agent == "InSilicoResearchAgent"
                and case.transformation.source_text
            ):
                literals.add(case.transformation.source_text.strip())
            for key in ("gene_id", "to_id"):
                value = case.expected_core_args.get(key)
                if isinstance(value, str):
                    literals.add(value.strip())
            if case.expected_agent == "BriefGeneAgent":
                value = case.expected_core_args.get("user_query")
                if isinstance(value, str):
                    literals.add(value.strip())
    return tuple(sorted(value.casefold() for value in literals if value))


def test_description_set_preserves_tool_order_and_models() -> None:
    """Descriptions must not alter the canonical router universe."""
    actual = tuple(
        (name, model) for name, _description, model in AGENT_TOOL_DEFINITIONS
    )
    assert actual == _EXPECTED_TOOLS


def test_descriptions_are_unique_and_balanced() -> None:
    """Keep every description distinct and within the approved budget."""
    descriptions = _descriptions()
    assert len(descriptions) == len(set(descriptions)) == 10
    out_of_bounds = {
        str(name): len(str(description).split())
        for name, description, _model in AGENT_TOOL_DEFINITIONS
        if not 35 <= len(str(description).split()) <= 75
    }
    assert out_of_bounds == {}


def test_descriptions_have_no_unresolved_markers() -> None:
    """Reject unfinished candidate wording."""
    marker_hits = {
        marker
        for marker in _FORBIDDEN_MARKERS
        if any(marker in value.casefold() for value in _descriptions())
    }
    assert marker_hits == set()


def test_descriptions_do_not_leak_evaluation_literals() -> None:
    """Keep benchmark questions and identifiers out of routing copy."""
    description_text = "\n".join(_descriptions()).casefold()
    leaks = [
        literal
        for literal in _evaluation_literals()
        if literal in description_text
    ]
    assert leaks == []
