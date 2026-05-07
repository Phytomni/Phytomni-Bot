# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline smoke tests for the DataAgent wrapper."""

import pytest

from mcp_server_phytomni.config.defaults import DataConfig
from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.data_agents import DataAgent, Nl2SqlRequest

pytestmark = pytest.mark.agent


class FakeCompiledGraph:
    """Minimal async graph stand-in used to inspect DataAgent invocation."""

    def __init__(self):
        """Verify init  ."""
        self.state = None
        self.config = None

    async def ainvoke(self, state, config=None):
        """Verify ainvoke."""
        self.state = state
        self.config = config
        return {
            "final_response": {
                "query": state["user_query"],
                "is_rewrite": state["is_rewrite"],
            }
        }

    def snapshot(self):
        """Return captured invocation details."""
        return {"state": self.state, "config": self.config}


def test_data_agent_routes_start_by_rewrite_flag():
    """Verify data agent routes start by rewrite flag."""
    agent = DataAgent(
        data_config=DataConfig(),
        sensitive_config=SensitiveConfig.load(),
    )

    assert agent.route_start({"is_rewrite": True}) == "retrieve_node"
    assert agent.route_start({"is_rewrite": False}) == "search_node"


def test_nl2sql_request_keeps_explicit_dialog_id():
    """Verify caller-provided dialog IDs remain unchanged."""
    request = Nl2SqlRequest.from_kwargs(
        "plant height in rice",
        {"dialog_id": "dialog-1"},
    )

    assert request.payload()["dialog_id"] == "dialog-1"


def test_nl2sql_request_generates_policy_dialog_id_when_missing():
    """Verify missing dialog IDs use the shared generated ID style."""
    request = Nl2SqlRequest.from_kwargs("plant height in rice", {})

    assert "-dialog-" in request.payload()["dialog_id"]


async def test_data_agent_arun_invokes_compiled_graph_with_thread_id():
    """Verify data agent arun invokes compiled graph with thread id."""
    agent = DataAgent(
        data_config=DataConfig(),
        sensitive_config=SensitiveConfig.load(),
    )
    fake_graph = FakeCompiledGraph()
    agent.app = fake_graph

    result = await agent.arun(
        user_query="plant height in rice",
        is_rewrite=False,
        thread_id="pytest-thread",
    )

    assert result == {
        "query": "plant height in rice",
        "is_rewrite": False,
    }
    assert fake_graph.state == {
        "user_query": "plant height in rice",
        "is_rewrite": False,
        "retrieve_prompt": None,
        "rewrite_query": None,
        "final_response": None,
    }
    assert fake_graph.config == {
        "configurable": {"thread_id": "pytest-thread"}
    }
