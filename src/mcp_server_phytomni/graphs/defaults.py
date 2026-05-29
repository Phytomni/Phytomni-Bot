# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Central registration of the project's built-in subgraphs.

``build_default_registry()`` is the single source of truth for the
agent workflows the project-level :class:`SubgraphRegistry` exposes
to the visualization script, future loaders, and tests. Each entry
is a tiny factory closing over the agent's constructor so the
registry caches the compiled app per ``(id, fingerprint)`` key.
"""

from __future__ import annotations

from typing import Any

from ..agents.analyst.agent import AnalystAgent
from ..agents.analyst.state import (
    AnalystInput,
    AnalystOutput,
    AnalystState,
)
from ..agents.brief_gene.core import BriefGeneAgent
from ..agents.brief_gene.state import (
    BriefGeneInput,
    BriefGeneOutput,
    BriefGeneState,
)
from ..agents.chat.builder import _build_chat_graph
from ..agents.data.agent import DataAgent
from ..agents.deep_genome.agent import DeepGenomeAgents
from ..agents.knowledge.agent import KnowledgeAgent
from ..agents.review.agent import DeepResearchAgent
from ..agents.review.state import (
    DeepResearchInput,
    DeepResearchOutput,
    DeepResearchState,
)
from . import SubgraphRegistry, SubgraphSpec


def _build_analyst_app() -> Any:
    """Return a compiled AnalystAgent workflow app."""
    return AnalystAgent().app


def _build_brief_gene_app() -> Any:
    """Return a compiled BriefGene workflow app."""
    return BriefGeneAgent().app


def _build_chat_app() -> Any:
    """Return a compiled chat workflow app."""
    return _build_chat_graph()


def _build_data_app() -> Any:
    """Return a compiled DataAgent workflow app."""
    return DataAgent().app


def _build_deep_genome_app() -> Any:
    """Return a compiled DeepGenome workflow app with default deps."""
    return DeepGenomeAgents(
        data_agent=DataAgent(),
        knowledge_agent=KnowledgeAgent(),
        analyst_agent=AnalystAgent(),
    ).app


def _build_knowledge_app() -> Any:
    """Return a compiled KnowledgeAgent workflow app."""
    return KnowledgeAgent().app


def _build_review_app() -> Any:
    """Return a compiled DeepResearchAgent (review) workflow app."""
    return DeepResearchAgent().app


def build_default_registry() -> SubgraphRegistry:
    """Return a SubgraphRegistry seeded with every built-in subgraph.

    The set grows as later phases land additional agents (design /
    network / research / environment / evolution). Currently covers
    analyst / brief_gene / chat / data / deep_genome / knowledge /
    review — every agent that constructs cleanly with the narrow
    subgraph IO contract its module ships.
    """
    registry = SubgraphRegistry()
    registry.register(
        SubgraphSpec(
            id="analyst",
            factory=_build_analyst_app,
            state_schema=AnalystState,
            input_schema=AnalystInput,
            output_schema=AnalystOutput,
        )
    )
    registry.register(
        SubgraphSpec(
            id="brief_gene",
            factory=_build_brief_gene_app,
            state_schema=BriefGeneState,
            input_schema=BriefGeneInput,
            output_schema=BriefGeneOutput,
        )
    )
    registry.register(SubgraphSpec(id="chat", factory=_build_chat_app))
    registry.register(SubgraphSpec(id="data", factory=_build_data_app))
    registry.register(
        SubgraphSpec(id="deep_genome", factory=_build_deep_genome_app)
    )
    registry.register(
        SubgraphSpec(id="knowledge", factory=_build_knowledge_app)
    )
    registry.register(
        SubgraphSpec(
            id="review",
            factory=_build_review_app,
            state_schema=DeepResearchState,
            input_schema=DeepResearchInput,
            output_schema=DeepResearchOutput,
        )
    )
    return registry
