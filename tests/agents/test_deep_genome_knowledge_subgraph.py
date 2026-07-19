# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the deep_genome knowledge-dispatch chokepoint.

Pins the knowledge-dispatch chokepoint that ``_experiment_protocols``
uses to retrieve protocol sections: it routes through the compiled
knowledge subgraph ``ainvoke`` and unwraps ``final_response`` so
callers see the same chat-completion envelope shape.
"""

# The direct dispatch probe below targets the report's internal knowledge
# seam; its protected-access directive is scoped to that test symbol.

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.agents.deep_genome import report as report_module
from mcp_server_phytomni.config.defaults import DeepGenomeConfig
from tests.support.subgraph_fakes import (
    RecordingKnowledgeApp,
    knowledge_output,
)

pytestmark = pytest.mark.agent


def _build_mixin_instance(knowledge_app: Any) -> Any:
    """Construct a minimal stand-in for ``DeepGenomeReportMixin``.

    The dispatch helper only reads ``self.deep_genome_config`` and
    ``self._agents.knowledge_app``, so a ``SimpleNamespace`` with
    those fields suffices to exercise the helper without constructing
    the full ``DeepGenomeAgents`` (which would compile a graph and
    bind a ``BriefGeneAgent`` subgraph).
    """
    config = DeepGenomeConfig()
    return SimpleNamespace(
        deep_genome_config=config,
        _agents=SimpleNamespace(knowledge_app=knowledge_app),
    )


async def test_dispatch_knowledge_uses_subgraph() -> None:
    """The helper delegates to the compiled knowledge subgraph.

    The compiled app's ``ainvoke`` returns the ``KnowledgeOutput`` shape
    (``retrieved_docs`` + ``final_response``); the helper unwraps
    ``final_response`` so callers see the same chat-completion envelope
    the legacy ``knowledge_agent.arun`` produced.
    """
    # pylint: disable=protected-access
    fake = RecordingKnowledgeApp(output=knowledge_output("subgraph"))
    mixin = _build_mixin_instance(knowledge_app=fake.compiled)

    result = (
        await report_module.DeepGenomeReportMixin._dispatch_knowledge_retrieve(
            mixin,
            user_query="experiment-stub",
            repo_id_dict={"repo": 1},
        )
    )

    assert result == {"choices": [{"message": {"content": "subgraph"}}]}
    assert len(fake.calls) == 1
