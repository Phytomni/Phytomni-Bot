# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline smoke tests for the public wrappers exported by domain agents.

Covers multi_retrieve_generate, retrieve_generate, deep_research,
brief_gene_function, gene_function, and retrieve_plan_submit. Each test
replaces the agent class and the cached-agent helper with fakes so the
wrapper can be exercised without external services.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from mcp_server_phytomni.agents.analyst import agent as analyst_agent
from mcp_server_phytomni.agents.brief_gene import agent as brief_gene_agent
from mcp_server_phytomni.agents.deep_genome import agent as deep_genome_agent
from mcp_server_phytomni.agents.knowledge import agent as knowledge_agent
from mcp_server_phytomni.agents.review import agent as review_agent

pytestmark = pytest.mark.agent


def _no_cache(
    name: str,
    factory: Callable[[], Any],
    fingerprint_values: Any = None,
) -> Any:
    """Bypass the agent registry and invoke the factory directly.

    Args:
        name: Cache key supplied by the wrapper.
        factory: Zero-argument factory that constructs the agent.
        fingerprint_values: Cache fingerprint values (ignored in tests).

    Returns:
        The agent instance built by ``factory``.
    """
    assert name
    _ = fingerprint_values
    return factory()


class _FakeAgent:
    """Generic fake agent that records arun arguments and returns a stub.

    Attributes:
        captured: Dictionary populated with constructor and arun kwargs.
    """

    def __init__(self, captured: dict[str, Any], **kwargs: Any):
        """Record constructor kwargs into the shared captured dict.

        Args:
            captured: Dictionary shared with the calling test.
            **kwargs: Constructor keyword arguments forwarded by the
                wrapper's agent factory.
        """
        self.captured = captured
        captured.setdefault("init", []).append(kwargs)

    async def arun(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Record run arguments and return a deterministic payload.

        Args:
            *args: Positional arguments forwarded by the wrapper.
            **kwargs: Keyword arguments forwarded by the wrapper.

        Returns:
            Static payload that lets the wrapper return type be asserted.
        """
        self.captured["arun"] = {"args": args, "kwargs": kwargs}
        return {"ok": True}


def _bind_fake_agent(
    module: Any,
    attr_name: str,
    captured: dict[str, Any],
) -> None:
    """Replace ``attr_name`` on ``module`` with a _FakeAgent factory.

    Args:
        module: Agent module whose attribute should be replaced.
        attr_name: Name of the agent class attribute on the module.
        captured: Shared dict that receives constructor and arun records.
    """

    def factory(**kwargs: Any) -> _FakeAgent:
        """Construct a _FakeAgent that shares the captured dict.

        Args:
            **kwargs: Constructor keyword arguments forwarded by the
                wrapper's agent factory.

        Returns:
            A _FakeAgent instance writing into the shared captured dict.
        """
        return _FakeAgent(captured, **kwargs)

    module.__dict__[attr_name] = factory


async def test_multi_retrieve_generate_runs_wrapper(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify multi_retrieve_generate constructs and runs KnowledgeAgent.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap agent class.

    Returns:
        None after wrapper return and arun forwarding assertions pass.
    """
    captured: dict[str, Any] = {}
    monkeypatch.setattr(knowledge_agent, "get_cached_agent", _no_cache)
    _bind_fake_agent(knowledge_agent, "KnowledgeAgent", captured)

    result = await knowledge_agent.multi_retrieve_generate(
        user_query="What is photosynthesis?",
        repo_id_dict={"repo-1": 5},
        is_generate=True,
        is_follow_up=False,
    )

    assert result == {"ok": True}
    arun = captured["arun"]["kwargs"]
    assert arun["user_query"] == "What is photosynthesis?"
    assert arun["repo_id_dict"] == {"repo-1": 5}
    assert arun["is_generate"] is True
    assert arun["is_follow_up"] is False


async def test_retrieve_generate_delegates_to_multi_retrieve_generate(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify retrieve_generate forwards to multi_retrieve_generate.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap agent class.

    Returns:
        None after delegation assertion passes.
    """
    captured: dict[str, Any] = {}
    monkeypatch.setattr(knowledge_agent, "get_cached_agent", _no_cache)
    _bind_fake_agent(knowledge_agent, "KnowledgeAgent", captured)

    result = await knowledge_agent.retrieve_generate(
        user_query="What is C3 photosynthesis?",
        repo_id="rice-repo",
        page_size=3,
    )

    assert result == {"ok": True}
    arun = captured["arun"]["kwargs"]
    assert arun["user_query"] == "What is C3 photosynthesis?"
    assert arun["repo_id_dict"] == {"rice-repo": 3}


async def test_deep_research_runs_review_workflow(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify deep_research constructs DeepResearchAgent and runs it.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap agent class.

    Returns:
        None after wrapper return assertion passes.
    """
    captured: dict[str, Any] = {}
    monkeypatch.setattr(review_agent, "get_cached_agent", _no_cache)
    _bind_fake_agent(review_agent, "DeepResearchAgent", captured)

    result = await review_agent.deep_research(
        user_query="Drought tolerance review",
        obs_file_list=["obs://papers/p1.pdf"],
    )

    assert result == {"ok": True}
    arun = captured["arun"]["kwargs"]
    assert arun["user_query"] == "Drought tolerance review"
    assert arun["obs_file_list"] == ["obs://papers/p1.pdf"]


async def test_brief_gene_function_runs_brief_workflow(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify brief_gene_function constructs BriefGeneAgent and runs it.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap agent class.

    Returns:
        None after wrapper return assertion passes.
    """
    captured: dict[str, Any] = {}
    monkeypatch.setattr(brief_gene_agent, "get_cached_agent", _no_cache)
    _bind_fake_agent(brief_gene_agent, "BriefGeneAgent", captured)

    result = await brief_gene_agent.brief_gene_function(
        user_query="AtPHYB function",
    )

    assert result == {"ok": True}
    arun = captured["arun"]["kwargs"]
    assert arun["user_query"] == "AtPHYB function"


async def test_gene_function_runs_deep_genome_workflow(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify gene_function constructs DeepGenomeAgents and runs it.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap agent class.

    Returns:
        None after wrapper return assertion passes.
    """
    captured: dict[str, Any] = {}
    monkeypatch.setattr(deep_genome_agent, "get_cached_agent", _no_cache)
    _bind_fake_agent(deep_genome_agent, "DeepGenomeAgents", captured)

    result = await deep_genome_agent.gene_function(
        species_code="osa",
        gene_id="AtPHYB",
        user_id="test-user",
    )

    assert result == {"ok": True}
    arun = captured["arun"]["kwargs"]
    assert arun["species_code"] == "osa"
    assert arun["gene_id"] == "AtPHYB"


async def test_retrieve_plan_submit_runs_analyst_workflow(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify retrieve_plan_submit constructs AnalystAgent and runs it.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap agent class.

    Returns:
        None after wrapper return and meta_meta passthrough assertions.
    """
    captured: dict[str, Any] = {}
    monkeypatch.setattr(analyst_agent, "get_cached_agent", _no_cache)
    _bind_fake_agent(analyst_agent, "AnalystAgent", captured)

    result = await analyst_agent.retrieve_plan_submit(
        goal_description="Map QTLs in rice",
        data_list={"input": "obs://rice/data.csv"},
        obs_file_list=["obs://papers/rice.pdf"],
        meta_meta="extra-meta",
    )

    assert result["ok"] is True
    assert result["meta_meta"] == "extra-meta"
    arun = captured["arun"]["kwargs"]
    assert arun["goal_description"] == "Map QTLs in rice"
    assert arun["preset_data_list"] == {"input": "obs://rice/data.csv"}
    assert arun["obs_file_list"] == ["obs://papers/rice.pdf"]
