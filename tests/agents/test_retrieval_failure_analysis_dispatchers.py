# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Cross-consumer admission tests for required Knowledge failures.

The Research, Network, and Design wrappers all enter the same compiled
Analyst boundary. A mounted Knowledge failure must stop before any accepted
submission or task projection, while reliable partial evidence must reach the
normal dispatch result exactly once.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.analyst.agent import AnalystAgent
from mcp_server_phytomni.agents.design import agent as design_agent
from mcp_server_phytomni.agents.design.agent import (
    DigitalDesignAgents,
    _DispatchOptions,
)
from mcp_server_phytomni.agents.knowledge.retrieval_result import (
    retrieval_unavailable_error,
)
from mcp_server_phytomni.agents.network import agent as network_agent
from mcp_server_phytomni.agents.network.agent import GeneNetworkAgents
from mcp_server_phytomni.agents.research import agent as research_agent
from mcp_server_phytomni.agents.research.agent import (
    InSilicoResearchAgents,
    ResearchTaskContext,
)
from mcp_server_phytomni.agents.shared import remote_analysis
from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.runtime.task_manager import TaskManager

pytestmark = pytest.mark.agent

_RETRIEVAL_ERROR = "Knowledge retrieval temporarily unavailable"
_CONSUMERS = ("research", "network", "design")


def _build_analyst_stub() -> tuple[Any, AsyncMock, AsyncMock]:
    """Build a mounted Analyst app plus a legacy submission spy."""
    knowledge_mount = AsyncMock()
    remote_submit = AsyncMock()
    analyst_stub = SimpleNamespace(
        app=SimpleNamespace(ainvoke=knowledge_mount),
        arun=remote_submit,
        identifier=lambda: "analyst-stub",
    )
    return analyst_stub, knowledge_mount, remote_submit


def _patch_acceptance(
    monkeypatch: pytest.MonkeyPatch,
    acceptance_spy: Mock,
) -> None:
    """Patch every acceptance projection used by the three wrappers."""
    for module in (
        design_agent,
        network_agent,
        research_agent,
        remote_analysis,
    ):
        monkeypatch.setattr(module, "accepted_submission", acceptance_spy)


def _build_consumer(
    consumer: str,
    analyst_stub: Any,
    output_dir: str,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, Callable[[], Awaitable[dict[str, Any]]]]:
    """Build one real indirect wrapper and its smallest invocation."""
    sensitive = SensitiveConfig.load()
    agent: Any
    if consumer == "research":
        agent = InSilicoResearchAgents(
            sensitive_config=sensitive,
            analyst_agent=cast(AnalystAgent, analyst_stub),
        )
        monkeypatch.setattr(
            research_agent,
            "collect_research_evidence",
            AsyncMock(return_value=None),
        )
        task = ResearchTaskContext(
            goal_description="Investigate a bounded synthetic target.",
            context="synthetic research context",
            data_list={"obs://synthetic/data.tsv": "synthetic data"},
            output_dir=output_dir,
            task_name="research_goal_synthetic",
            thread_id="thread-research-synthetic",
        )

        async def invoke_research() -> dict[str, Any]:
            """Invoke the real Research submission wrapper."""
            return await getattr(agent, "_submit_research_task")(task)

        return agent, invoke_research

    if consumer == "network":
        agent = GeneNetworkAgents(
            sensitive_config=sensitive,
            analyst_agent=cast(AnalystAgent, analyst_stub),
        )
        monkeypatch.setattr(
            agent,
            "_analysis_prompt_parts",
            lambda *_args, **_kwargs: (
                "synthetic network goal",
                "synthetic network metadata",
                {"obs://synthetic/network.tsv": "synthetic data"},
            ),
        )

        async def invoke_network() -> dict[str, Any]:
            """Invoke the real Network submission wrapper."""
            return await getattr(agent, "_dispatch_and_wait_analysis")(
                analysis_type="gene_network_analysis",
                species_code="ath",
                to_id="TO:synthetic-network",
                output_dir=output_dir,
            )

        return agent, invoke_network

    if consumer == "design":
        agent = DigitalDesignAgents(
            sensitive_config=sensitive,
            analyst_agent=cast(AnalystAgent, analyst_stub),
        )
        monkeypatch.setattr(
            agent,
            "_analysis_prompt_parts",
            lambda *_args, **_kwargs: (
                "synthetic design goal",
                "synthetic design metadata",
                {"obs://synthetic/design.tsv": "synthetic data"},
            ),
        )

        async def invoke_design() -> dict[str, Any]:
            """Invoke the real Design submission wrapper."""
            return await getattr(agent, "_dispatch_and_wait_analysis")(
                analysis_type="protein_design_analysis",
                species_code="ath",
                gene_id="AT:synthetic-design",
                options=_DispatchOptions(output_dir=output_dir),
            )

        return agent, invoke_design

    raise AssertionError(f"unknown consumer: {consumer}")


def _assert_no_task_side_effects(db_path: Path, output_dir: str) -> None:
    """Assert the isolated task store contains no submitted or running row."""
    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    assert count == 0
    assert not Path(output_dir).exists()


@pytest.fixture(autouse=True)
def _isolate_dispatch_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep output and dedup state inside the test's temporary directory."""
    db_path = tmp_path / "tasks.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))
    TaskManager(str(db_path))
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.shared.analysis_storage."
        "relay_mode_enabled",
        lambda: True,
    )


@pytest.mark.parametrize("consumer", _CONSUMERS)
async def test_required_retrieval_failure_has_no_indirect_submission(
    consumer: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Research, Network, and Design stop before remote task acceptance."""
    analyst_stub, knowledge_mount, remote_submit = _build_analyst_stub()
    knowledge_mount.side_effect = retrieval_unavailable_error()
    acceptance_spy = Mock()
    _patch_acceptance(monkeypatch, acceptance_spy)
    output_dir = str(tmp_path / consumer / "run" / "children" / "part-001")
    _agent, invoke = _build_consumer(
        consumer, analyst_stub, output_dir, monkeypatch
    )

    with pytest.raises(McpError, match=_RETRIEVAL_ERROR) as exc_info:
        await invoke()

    assert str(exc_info.value) == _RETRIEVAL_ERROR
    knowledge_mount.assert_awaited_once()
    remote_submit.assert_not_awaited()
    acceptance_spy.assert_not_called()
    _assert_no_task_side_effects(tmp_path / "tasks.sqlite", output_dir)


@pytest.mark.parametrize("consumer", _CONSUMERS)
async def test_partial_retrieval_reaches_each_normal_dispatch_seam(
    consumer: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reliable partial evidence still reaches one normal submission call."""
    analyst_stub, knowledge_mount, remote_submit = _build_analyst_stub()
    knowledge_mount.return_value = {
        "task_id": f"accepted-{consumer}",
        "output_dir": str(tmp_path / consumer / "run"),
        "task_status": "SUCCEEDED",
    }
    acceptance_spy = Mock()
    _patch_acceptance(monkeypatch, acceptance_spy)
    output_dir = str(tmp_path / consumer / "run" / "children" / "part-001")
    _agent, invoke = _build_consumer(
        consumer, analyst_stub, output_dir, monkeypatch
    )

    result = await invoke()

    assert result["task_id"] == f"accepted-{consumer}"
    knowledge_mount.assert_awaited_once()
    remote_submit.assert_not_awaited()
    assert acceptance_spy.call_count >= 1
