# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline smoke tests for the DataAgent wrapper."""

# pylint: disable=missing-function-docstring, too-few-public-methods

import pytest

from mcp_server_phytomni.config.defaults import DataConfig
from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.data_agents import DataAgent

pytestmark = pytest.mark.agent


class FakeCompiledGraph:
    """Minimal async graph stand-in used to inspect DataAgent invocation."""

    def __init__(self):
        self.state = None
        self.config = None

    async def ainvoke(self, state, config=None):
        self.state = state
        self.config = config
        return {
            "final_response": {
                "query": state["user_query"],
                "is_rewrite": state["is_rewrite"],
            }
        }


def test_data_agent_routes_start_by_rewrite_flag():
    agent = DataAgent(
        data_config=DataConfig(),
        sensitive_config=SensitiveConfig.load(),
    )

    assert agent.route_start({"is_rewrite": True}) == "retrieve_node"
    assert agent.route_start({"is_rewrite": False}) == "search_node"


async def test_data_agent_arun_invokes_compiled_graph_with_thread_id():
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
