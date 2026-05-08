# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline smoke tests for the DataAgent wrapper.

Covers graph routing, NL2SQL dialog id policy, DataAgent graph invocation, and
legacy rewrite_nl2sql wrapper thread-id compatibility.
"""

from typing import Any

import pytest

from mcp_server_phytomni.agents.data import agent as data_agent_module
from mcp_server_phytomni.agents.data.agent import DataAgent
from mcp_server_phytomni.agents.data.nl2sql import Nl2SqlRequest
from mcp_server_phytomni.config.defaults import DataConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent


class FakeCompiledGraph:
    """Minimal async graph stand-in used to inspect DataAgent invocation."""

    def __init__(self):
        """Verify init  ."""
        self.state = None
        self.config = None

    async def ainvoke(self, state, config=None):
        """Capture DataAgent graph invocation and return final response.

        Args:
            state: Initial DataAgent workflow state.
            config: Optional LangGraph runnable config.

        Returns:
            Final graph state containing the response payload.
        """
        self.state = state
        self.config = config
        return {
            "final_response": {
                "query": state["user_query"],
                "is_rewrite": state["is_rewrite"],
            }
        }

    def snapshot(self):
        """Return captured invocation details.

        Returns:
            Last state and config captured by ainvoke.
        """
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


async def test_rewrite_nl2sql_uses_dialog_id_as_thread_id(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify wrapper keeps dialog ID and graph thread ID aligned.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace the agent and
            cache.

    Returns:
        None after wrapper config and run assertions pass.
    """
    captured: dict[str, Any] = {}

    class FakeDataAgent:
        """Fake workflow that records constructor and run arguments.

        Attributes:
            Constructor and run inputs are stored in the outer captured dict.
        """

        def __init__(self, data_config, sensitive_config):
            """Capture resolved wrapper configuration."""
            captured["config"] = data_config
            captured["sensitive"] = sensitive_config

        async def arun(
            self,
            user_query: str,
            is_rewrite: bool = True,
            thread_id: str | None = None,
        ) -> dict[str, object]:
            """Capture the graph invocation.

            Args:
                user_query: Query forwarded by the wrapper.
                is_rewrite: Whether rewrite mode is enabled.
                thread_id: Thread id derived from dialog id.

            Returns:
                Minimal success payload.
            """
            captured["run"] = {
                "user_query": user_query,
                "is_rewrite": is_rewrite,
                "thread_id": thread_id,
            }
            return {"ok": True}

        def captured_config(self) -> Any:
            """Return captured config for lint-friendly fake shape.

            Returns:
                Captured DataConfig-like object.
            """
            return captured["config"]

    def no_cache(name, factory, fingerprint_values=None):
        """Return a fresh fake agent.

        Args:
            name: Ignored cache name.
            factory: Factory used to create the fake agent.
            fingerprint_values: Ignored cache fingerprint values.

        Returns:
            New fake agent instance from ``factory``.
        """
        del name, fingerprint_values
        return factory()

    monkeypatch.setattr(data_agent_module, "DataAgent", FakeDataAgent)
    monkeypatch.setattr(data_agent_module, "get_cached_agent", no_cache)

    result = await data_agent_module.rewrite_nl2sql(
        "plant height in rice",
        is_rewrite=False,
        dialog_id="dialog-1",
    )

    assert result == {"ok": True}
    assert captured["config"].DIALOG_ID == "dialog-1"
    assert captured["run"] == {
        "user_query": "plant height in rice",
        "is_rewrite": False,
        "thread_id": "dialog-1",
    }
