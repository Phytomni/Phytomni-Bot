# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Central registration of the project's built-in subgraphs.

``build_default_registry()`` is the single source of truth for which
agent workflows are wired into the project-level
:class:`SubgraphRegistry`. The visualization script, future loaders,
and tests all read the same set from here so a new agent ships in
one place rather than three. Each entry is a tiny factory closing
over the agent's constructor — the registry calls the factory once
per ``(id, fingerprint)`` key and reuses the compiled app on every
subsequent lookup.
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


def _build_deep_genome_app() -> Any:
    """Return a compiled DeepGenome workflow app with default deps."""
    return DeepGenomeAgents(
        data_agent=DataAgent(),
        knowledge_agent=KnowledgeAgent(),
        analyst_agent=AnalystAgent(),
    ).app


def build_default_registry() -> SubgraphRegistry:
    """Return a SubgraphRegistry seeded with every built-in subgraph.

    The set grows as later phases land additional agents (knowledge /
    data / review / analyst / design / network / research /
    environment / evolution). For now the registry covers the agents
    that already construct cleanly without further subgraph
    composition work — chat ships here for the first time alongside
    the existing brief_gene and deep_genome entries.
    """
    registry = SubgraphRegistry()
    registry.register(
        SubgraphSpec(id="brief_gene", factory=_build_brief_gene_app)
    )
    registry.register(SubgraphSpec(id="chat", factory=_build_chat_app))
    registry.register(
        SubgraphSpec(id="deep_genome", factory=_build_deep_genome_app)
    )
    return registry
