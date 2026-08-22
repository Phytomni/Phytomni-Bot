# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Result-delivery agents must not reuse the shared config dump as a run root.

HTTP/MCP handlers seed ``*_CONFIG.OUTPUT_DIR`` (the test placeholder).
Network, Design, and Research historically treated that dump as an
allocated root and placed ``children/part-001`` underneath it, so EI
harvest listed leftover objects from every prior job.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.analyst.agent import AnalystAgent
from mcp_server_phytomni.agents.design.agent import (
    DigitalDesignAgents,
    DigitalDesignConfig,
    DigitalDesignState,
)
from mcp_server_phytomni.agents.network.agent import (
    GeneNetworkAgents,
    GeneNetworkConfig,
    GeneNetworkState,
)
from mcp_server_phytomni.agents.research.agent import (
    InSilicoResearchAgents,
    InSilicoResearchConfig,
    InSilicoResearchState,
)
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent

_DEFAULT = "/obs/phytomni/agent_data/test/output"
_ALLOCATED = "/obs/phytomni/agent_data/users/alice/run-new"


class _StubAnalyst:
    """Stand-in passed as ``analyst_agent`` to skip real construction."""

    def identifier(self) -> str:
        """Return a stable label for debugging."""
        return "stub-analyst"


async def _fake_create(*_args: Any, **_kwargs: Any) -> str:
    """Return a unique run root without touching OBS."""
    return _ALLOCATED


async def test_network_prepare_tasks_ignores_shared_dump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gene Network allocates a unique root when MCP seeds the dump."""
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.network.agent.create_output_dir",
        _fake_create,
    )
    agent = GeneNetworkAgents(
        gene_network_config=GeneNetworkConfig(),
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, _StubAnalyst()),
    )

    result = await agent.prepare_tasks(
        cast(
            GeneNetworkState,
            {
                "user_id": "alice",
                "output_dir": _DEFAULT,
            },
        )
    )

    assert result["output_dir"] == _ALLOCATED
    assert result["network_tasks"][0]["output_dir"] == (
        f"{_ALLOCATED}/children/part-001"
    )


async def test_design_prepare_tasks_ignores_shared_dump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Digital Design allocates a unique root when MCP seeds the dump."""
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.design.agent.create_output_dir",
        _fake_create,
    )
    agent = DigitalDesignAgents(
        digital_design_config=DigitalDesignConfig(),
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, _StubAnalyst()),
    )

    result = await agent.prepare_tasks(
        cast(
            DigitalDesignState,
            {
                "user_id": "alice",
                "output_dir": _DEFAULT,
            },
        )
    )

    assert result["output_dir"] == _ALLOCATED
    assert result["design_tasks"][0]["output_dir"] == (
        f"{_ALLOCATED}/children/part-001"
    )
    assert result["design_tasks"][1]["output_dir"] == (
        f"{_ALLOCATED}/children/part-002"
    )


async def test_research_prepare_tasks_ignores_shared_dump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-silico research allocates a unique root when MCP seeds the dump."""
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.research.agent.create_output_dir",
        _fake_create,
    )
    agent = InSilicoResearchAgents(
        in_silico_config=InSilicoResearchConfig(),
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, _StubAnalyst()),
    )

    result = await agent.prepare_tasks(
        cast(
            InSilicoResearchState,
            {
                "user_id": "alice",
                "output_dir": _DEFAULT,
                "goals": [{"goal": "map hormone genes", "context": "osa"}],
            },
        )
    )

    assert result["output_dir"] == _ALLOCATED
    assert result["research_tasks"][0]["output_dir"] == (
        f"{_ALLOCATED}/children/part-001"
    )
