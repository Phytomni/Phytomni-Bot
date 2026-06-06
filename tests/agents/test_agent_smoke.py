# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline fake-graph smoke tests for LangGraph agents.

Covers agent arun entrypoints by replacing compiled graphs with fakes and
asserting initial state and LangGraph thread config construction.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from mcp_server_phytomni.agents.analyst.agent import AnalystAgent
from mcp_server_phytomni.agents.brief_gene.agent import BriefGeneAgent
from mcp_server_phytomni.agents.deep_genome.agent import DeepGenomeAgents
from mcp_server_phytomni.agents.design.agent import DigitalDesignAgents
from mcp_server_phytomni.agents.knowledge.agent import KnowledgeAgent
from mcp_server_phytomni.agents.network.agent import GeneNetworkAgents
from mcp_server_phytomni.agents.research.agent import (
    InSilicoResearchAgents,
)
from mcp_server_phytomni.agents.review.agent import DeepResearchAgent
from mcp_server_phytomni.config.defaults import (
    AnalystConfig,
    BriefGeneConfig,
    DeepGenomeConfig,
    DigitalDesignConfig,
    GeneNetworkConfig,
    InSilicoResearchConfig,
    KnowledgeConfig,
    ReviewConfig,
)
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent


class FakeCompiledGraph:
    """Minimal async graph stand-in used to inspect agent invocation.

    Attributes:
        final_state: Values merged into the returned graph state.
        state: Last initial state passed to ainvoke.
        config: Last runnable config passed to ainvoke.
    """

    def __init__(self, final_state: dict[str, Any]):
        """Verify init  ."""
        self.final_state = final_state
        self.state: dict[str, Any] | None = None
        self.config: dict[str, Any] | None = None

    async def ainvoke(
        self,
        state: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Capture invocation state and return merged final state.

        Args:
            state: Initial graph state supplied by the agent.
            config: Optional LangGraph runnable config.

        Returns:
            State merged with the configured final_state.
        """
        self.state = state
        self.config = config
        return {**state, **self.final_state}

    def snapshot(self) -> dict[str, Any]:
        """Return captured invocation details.

        Returns:
            Last state and config captured by ainvoke.
        """
        return {"state": self.state, "config": self.config}


def _thread_config(thread_id: str) -> dict[str, dict[str, str]]:
    """Verify thread config."""
    return {"configurable": {"thread_id": thread_id}}


def _sensitive_config() -> SensitiveConfig:
    """Verify sensitive config."""
    return SensitiveConfig.load()


async def test_knowledge_agent_arun_invokes_graph_with_initial_state():
    """Verify knowledge agent arun invokes graph with initial state."""
    agent = KnowledgeAgent(
        knowledge_config=KnowledgeConfig(),
        sensitive_config=_sensitive_config(),
    )
    fake_graph = FakeCompiledGraph({"final_response": {"answer": "ok"}})
    object.__setattr__(agent, "app", fake_graph)

    result = await agent.arun(
        user_query="photosynthesis review",
        obs_file_list=["obs://paper.pdf"],
        repo_id_dict={"literature": 1},
        is_follow_up=False,
        thread_id="knowledge-thread",
    )

    assert result["answer"] == "ok"
    assert result["phytomni_state"]["user_query"] == "photosynthesis review"
    assert result["phytomni_state"]["obs_file_list"] == ["obs://paper.pdf"]
    assert result["phytomni_state"]["is_follow_up"] is False
    assert fake_graph.state is not None
    assert fake_graph.state["user_query"] == "photosynthesis review"
    assert fake_graph.state["obs_file_list"] == ["obs://paper.pdf"]
    assert fake_graph.state["repo_id_dict"] == {"literature": 1}
    assert fake_graph.state["is_generate"] is True
    assert fake_graph.state["is_follow_up"] is False
    assert fake_graph.config == _thread_config("knowledge-thread")


async def test_brief_gene_agent_arun_invokes_graph_with_initial_state():
    """Verify brief gene agent arun invokes graph with initial state."""
    agent = BriefGeneAgent(
        brief_config=BriefGeneConfig(),
        sensitive_config=_sensitive_config(),
        knowledge_agent=None,
    )
    fake_graph = FakeCompiledGraph(
        {"final_response": {"gene": "AT1G01010", "summary": "ok"}}
    )
    object.__setattr__(agent, "app", fake_graph)

    result = await agent.arun(
        user_query="AT1G01010 function",
        thread_id="brief-thread",
    )

    assert result["gene"] == "AT1G01010"
    assert result["summary"] == "ok"
    assert result["phytomni_state"]["user_query"] == "AT1G01010 function"
    assert result["phytomni_state"]["gene_found"] is False
    assert fake_graph.state is not None
    assert fake_graph.state["user_query"] == "AT1G01010 function"
    assert fake_graph.state["gene_found"] is False
    assert fake_graph.state["retrieved_docs"] == []
    assert fake_graph.state["follow_up_questions"] == []
    assert fake_graph.config == _thread_config("brief-thread")


async def test_review_agent_arun_invokes_graph_with_initial_state():
    """Verify review agent arun invokes graph with initial state."""
    agent = DeepResearchAgent(
        review_config=ReviewConfig(),
        sensitive_config=_sensitive_config(),
        knowledge_agent=None,
    )
    fake_graph = FakeCompiledGraph({"final_response": {"review": "ok"}})
    object.__setattr__(agent, "app", fake_graph)

    result = await agent.arun(
        user_query="write a rice drought review",
        obs_file_list=["obs://review-seed.md"],
        thread_id="review-thread",
    )

    assert result["review"] == "ok"
    assert result["phytomni_state"]["original_user_query"] == (
        "write a rice drought review"
    )
    assert result["phytomni_state"]["research_dimensions"] == []
    assert fake_graph.state is not None
    assert fake_graph.state["original_user_query"] == (
        "write a rice drought review"
    )
    assert fake_graph.state["obs_file_list"] == ["obs://review-seed.md"]
    assert fake_graph.state["research_dimensions"] == []
    assert fake_graph.state["final_response"] == {}
    assert fake_graph.config == _thread_config("review-thread")


async def test_in_silico_agent_arun_invokes_graph_with_initial_state():
    """Verify in silico agent arun invokes graph with initial state."""
    agent = InSilicoResearchAgents(
        analyst_agent=None,
        in_silico_config=InSilicoResearchConfig(),
        sensitive_config=_sensitive_config(),
    )
    fake_graph = FakeCompiledGraph(
        {
            "task_ids": {"goal-1": "task-1"},
            "goals": ["goal-1"],
            "error": None,
        }
    )
    object.__setattr__(agent, "app", fake_graph)

    result = await agent.arun(
        paper_text="A paper about root architecture.",
        data_list={"obs://data.tsv": "phenotype table"},
        user_id="user-1",
        obs_file_list=["obs://paper.pdf"],
        output_dir="/tmp/phytomni",
        thread_id="in-silico-thread",
    )

    assert result["task_ids"] == {"goal-1": "task-1"}
    assert result["goals"] == ["goal-1"]
    assert result["error"] is None
    assert "phytomni_state" in result
    assert fake_graph.state is not None
    assert fake_graph.state["paper_text"] == "A paper about root architecture."
    assert fake_graph.state["data_list"] == {
        "obs://data.tsv": "phenotype table"
    }
    assert fake_graph.state["user_id"] == "user-1"
    assert fake_graph.state["obs_file_list"] == ["obs://paper.pdf"]
    assert fake_graph.state["output_dir"] == "/tmp/phytomni"
    assert fake_graph.config == _thread_config("in-silico-thread")


async def test_gene_network_agent_arun_invokes_graph_with_initial_state():
    """Verify gene network agent arun invokes graph with initial state."""
    agent = GeneNetworkAgents(
        analyst_agent=None,
        gene_network_config=GeneNetworkConfig(),
        sensitive_config=_sensitive_config(),
    )
    fake_graph = FakeCompiledGraph(
        {"network_task": {"task_id": "network-1"}, "error": None}
    )
    object.__setattr__(agent, "app", fake_graph)

    result = await agent.arun(
        species="oryza sativa",
        to_id="TO:0000621",
        user_id="user-1",
        batch=True,
        output_dir="/tmp/network",
        thread_id="network-thread",
    )

    assert result["network_task"] == {"task_id": "network-1"}
    assert result["error"] is None
    assert "phytomni_state" in result
    assert fake_graph.state is not None
    assert fake_graph.state["species"] == "oryza sativa"
    assert fake_graph.state["to_id"] == "TO:0000621"
    assert fake_graph.state["user_id"] == "user-1"
    assert fake_graph.state["batch"] is True
    assert fake_graph.state["output_dir"] == "/tmp/network"
    assert fake_graph.config == _thread_config("network-thread")


async def test_digital_design_agent_arun_invokes_graph_with_initial_state():
    """Verify digital design agent arun invokes graph with initial state."""
    agent = DigitalDesignAgents(
        analyst_agent=None,
        digital_design_config=DigitalDesignConfig(),
        sensitive_config=_sensitive_config(),
    )
    fake_graph = FakeCompiledGraph(
        {"design_task_result": [{"task_id": "design-1"}], "error": None}
    )
    object.__setattr__(agent, "app", fake_graph)

    result = await agent.arun(
        species="arabidopsis thaliana",
        gene_id="AT1G01010",
        user_id="user-1",
        batch=False,
        output_dir="/tmp/design",
        thread_id="design-thread",
    )

    assert result["design_task_result"] == [{"task_id": "design-1"}]
    assert result["error"] is None
    assert "phytomni_state" in result
    assert fake_graph.state is not None
    assert fake_graph.state["species"] == "arabidopsis thaliana"
    assert fake_graph.state["gene_id"] == "AT1G01010"
    assert fake_graph.state["user_id"] == "user-1"
    assert fake_graph.state["batch"] is False
    assert fake_graph.state["output_dir"] == "/tmp/design"
    assert fake_graph.config == _thread_config("design-thread")


async def test_analyst_agent_arun_invokes_graph_with_initial_state():
    """Verify analyst agent arun invokes graph with initial state."""
    agent = AnalystAgent(
        analyst_config=AnalystConfig(),
        sensitive_config=_sensitive_config(),
    )
    fake_graph = FakeCompiledGraph(
        {
            "task_id": "task-1",
            "output_dir": "/tmp/analysis",
            "job_name": "pytest-job",
            "compute_resource": "small",
        }
    )
    object.__setattr__(agent, "app", fake_graph)

    result = await agent.arun(
        query="run differential expression",
        goal_description="Compare treated and control samples.",
        user_id="user-1",
        output_dir="/tmp/analysis",
        compute_resource="small",
        preset_data_list={"obs://counts.tsv": "count matrix"},
        obs_file_list=["obs://metadata.tsv"],
        preset_plan="Use DESeq2.",
        thread_id="analyst-thread",
        is_auto_select=False,
        is_polling=False,
    )

    assert result["task_id"] == "task-1"
    assert result["output_dir"] == "/tmp/analysis"
    assert result["job_name"] == "pytest-job"
    assert result["compute_resource"] == "small"
    assert "phytomni_state" in result
    assert fake_graph.state is not None
    assert fake_graph.state["query"] == "run differential expression"
    assert fake_graph.state["goal_description"] == (
        "Compare treated and control samples."
    )
    assert fake_graph.state["data_list"] == {
        "obs://counts.tsv": "count matrix"
    }
    assert fake_graph.state["obs_file_list"] == ["obs://metadata.tsv"]
    assert fake_graph.state["preset_plan"] == "Use DESeq2."
    assert fake_graph.state["plan"] is None
    assert fake_graph.state["is_auto_select"] is False
    assert fake_graph.state["is_polling"] is False
    assert fake_graph.state["is_preset_plan"] is False
    assert fake_graph.config == _thread_config("analyst-thread")


async def test_deep_genome_agent_arun_invokes_graph_with_initial_state():
    """Verify deep genome agent arun invokes graph with initial state."""
    agent = DeepGenomeAgents(
        knowledge_agent=None,
        analyst_agent=None,
        deep_genome_config=DeepGenomeConfig(),
        sensitive_config=_sensitive_config(),
    )
    fake_graph = FakeCompiledGraph(
        {
            "final_report": "complete report",
            "follow_up_questions": ["What next?"],
        }
    )
    object.__setattr__(agent, "app", fake_graph)

    result = await agent.arun(
        species_code="osa",
        gene_id="Os01g0177400",
        config_params={
            "use_data_agent": False,
            "use_analyst_agent": False,
        },
        thread_id="deep-genome-thread",
    )

    for _ in range(3):
        if fake_graph.state is not None:
            break
        await asyncio.sleep(0)

    assert result["task_id"]
    assert result["output_dir"].endswith(result["task_id"])
    assert result["compute_resource"] == "deep-genome"
    assert fake_graph.state is not None
    assert fake_graph.state["species_code"] == "osa"
    assert fake_graph.state["gene_id"] == "Os01g0177400"
    assert fake_graph.state["config_params"] == {
        "use_data_agent": False,
        "use_analyst_agent": False,
    }
    assert fake_graph.state["analysis_tasks"] == []
    assert fake_graph.state["follow_up_questions"] == []
    assert fake_graph.config == _thread_config("deep-genome-thread")
