# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Characterization tests for the DeepGenome graph assembly seams."""

from __future__ import annotations

from typing import Any

import pytest

from mcp_server_phytomni.agents.deep_genome import agent as deep_genome_module
from mcp_server_phytomni.agents.deep_genome.agent import DeepGenomeAgents

pytestmark = pytest.mark.agent


def test_deep_genome_build_graph_calls_mount_factory_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graph construction passes the app and persistence hook directly."""
    calls: list[tuple[Any, Any]] = []

    def factory(brief_gene_app: Any, persist_fn: Any) -> Any:
        calls.append((brief_gene_app, persist_fn))

        async def node(_state: Any) -> dict[str, Any]:
            return {}

        return node

    monkeypatch.setattr(
        deep_genome_module,
        "make_brief_gene_mount_node",
        factory,
    )
    agents = DeepGenomeAgents(knowledge_agent=None, analyst_agent=None)

    dependencies = getattr(agents, "_agents")
    persist_fn = getattr(agents, "_persist_brief_gene_result")
    assert calls == [(dependencies.brief_gene_app, persist_fn)]
