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
from ..agents.brief_gene.core import BriefGeneAgent
from ..agents.chat.builder import _build_chat_graph
from ..agents.data.agent import DataAgent
from ..agents.deep_genome.agent import DeepGenomeAgents
from ..agents.knowledge.agent import KnowledgeAgent
from . import SubgraphRegistry, SubgraphSpec


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


def build_default_registry() -> SubgraphRegistry:
    """Return a SubgraphRegistry seeded with every built-in subgraph.

    The set grows as later phases land additional agents (review /
    analyst / design / network / research / environment /
    evolution). Currently covers brief_gene / chat / data /
    deep_genome / knowledge — every agent that constructs cleanly
    with the narrow subgraph IO contract its module ships.
    """
    registry = SubgraphRegistry()
    registry.register(
        SubgraphSpec(id="brief_gene", factory=_build_brief_gene_app)
    )
    registry.register(SubgraphSpec(id="chat", factory=_build_chat_app))
    registry.register(SubgraphSpec(id="data", factory=_build_data_app))
    registry.register(
        SubgraphSpec(id="deep_genome", factory=_build_deep_genome_app)
    )
    registry.register(
        SubgraphSpec(id="knowledge", factory=_build_knowledge_app)
    )
    return registry
