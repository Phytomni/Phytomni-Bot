# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Flag-branch tests for the deep_genome knowledge-dispatch chokepoint.

Pins the knowledge-dispatch chokepoint that ``_experiment_protocols``
uses to retrieve protocol sections: flag-off routes through the
legacy ``knowledge_agent.arun`` helper; flag-on routes through the
compiled knowledge subgraph ``ainvoke`` returning the same
chat-completion envelope shape.
"""

# pylint: disable=protected-access
# Test file exercises ``DeepGenomeReportMixin._dispatch_knowledge_retrieve``
# (an internal helper that owns the knowledge-call seam shared by the
# experiment protocols worker) directly to assert flag routing.

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.deep_genome import report as report_module
from mcp_server_phytomni.config.defaults import DeepGenomeConfig

pytestmark = pytest.mark.agent


def _build_mixin_instance(
    use_knowledge_subgraph: bool,
    knowledge_app: Any = None,
) -> Any:
    """Construct a minimal stand-in for ``DeepGenomeReportMixin``.

    The dispatch helper only reads ``self.deep_genome_config``,
    ``self._agents.knowledge_agent``, and ``self._knowledge_app``,
    so a ``SimpleNamespace`` with those fields suffices to exercise
    the routing branch without constructing the full
    ``DeepGenomeAgents`` (which would compile a graph and bind a
    ``BriefGeneAgent`` subgraph).
    """
    config = DeepGenomeConfig().model_copy(
        update={"USE_KNOWLEDGE_SUBGRAPH": use_knowledge_subgraph}
    )
    legacy_arun = AsyncMock(
        return_value={"choices": [{"message": {"content": "legacy"}}]}
    )
    return (
        SimpleNamespace(
            deep_genome_config=config,
            _agents=SimpleNamespace(
                knowledge_agent=SimpleNamespace(arun=legacy_arun),
                knowledge_app=knowledge_app,
            ),
        ),
        legacy_arun,
    )


async def test_dispatch_knowledge_uses_legacy_when_flag_off() -> None:
    """Default flag-off path awaits the legacy ``knowledge_agent.arun``."""
    mixin, legacy_arun = _build_mixin_instance(use_knowledge_subgraph=False)

    result = (
        await report_module.DeepGenomeReportMixin._dispatch_knowledge_retrieve(
            mixin,
            user_query="experiment-stub",
            repo_id_dict={"repo": 1},
        )
    )

    assert result == {"choices": [{"message": {"content": "legacy"}}]}
    legacy_arun.assert_awaited_once()


async def test_dispatch_knowledge_uses_subgraph_when_flag_on() -> None:
    """Flag-on path delegates to the compiled knowledge subgraph.

    The compiled app's ``ainvoke`` returns the ``KnowledgeOutput`` shape
    (``retrieved_docs`` + ``final_response``); the helper unwraps
    ``final_response`` so callers see the same chat-completion envelope
    the legacy ``knowledge_agent.arun`` produces.
    """
    subgraph_app_mock = AsyncMock(
        return_value={
            "retrieved_docs": [],
            "final_response": {
                "choices": [{"message": {"content": "subgraph"}}]
            },
        }
    )
    knowledge_app = SimpleNamespace(ainvoke=subgraph_app_mock)
    mixin, legacy_arun = _build_mixin_instance(
        use_knowledge_subgraph=True, knowledge_app=knowledge_app
    )

    result = (
        await report_module.DeepGenomeReportMixin._dispatch_knowledge_retrieve(
            mixin,
            user_query="experiment-stub",
            repo_id_dict={"repo": 1},
        )
    )

    assert result == {"choices": [{"message": {"content": "subgraph"}}]}
    subgraph_app_mock.assert_awaited_once()
    legacy_arun.assert_not_awaited()
