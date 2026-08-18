# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pin locale propagation from MCP handlers into every stable wrapper.

These tests stop at the handler/wrapper boundary: the wrapper spies return a
small success payload, so no provider or remote analysis service is needed.
The graph-side assertions cover the shared chat option bag and analyst Send
payload, which are the two places where a locale can otherwise disappear.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_server_phytomni.agents.shared.analysis import route_analysis_tasks
from mcp_server_phytomni.graphs.chat_adapters import build_chat_kwargs_for
from mcp_server_phytomni.mcp import handlers
from mcp_server_phytomni.mcp.app import invoke_tool_raw
from tests.support.config_fakes import fake_chat_config, fake_sensitive_config

pytestmark = pytest.mark.agent


_CASES = (
    ("ChatAgent", "phyto_chat_with_follow"),
    ("KnowledgeAgent", "multi_retrieve_generate"),
    ("DataAgent", "rewrite_nl2sql"),
    ("AnalystAgent", "retrieve_plan_submit"),
    ("ReviewAgent", "review_agent_function"),
    ("InSilicoResearchAgent", "in_silico_research"),
    ("GeneNetworkAgent", "network_analysis"),
    ("BriefGeneAgent", "brief_gene_function"),
    ("DeepGenomeAgent", "gene_function"),
    ("DigitalDesignAgent", "design_module"),
)


def _minimal_arguments(tool_name: str) -> dict[str, Any]:
    """Return schema-valid arguments for one stable tool."""
    common = {"locale": "zh-CN"}
    payloads: dict[str, dict[str, Any]] = {
        "ChatAgent": {"user_query": "hello", "obs_file_list": []},
        "KnowledgeAgent": {"user_query": "hello", "obs_file_list": []},
        "DataAgent": {"user_query": "count genes"},
        "AnalystAgent": {
            "goal_description": "run a test analysis",
            "data_list": {},
            "obs_file_list": [],
        },
        "ReviewAgent": {
            "user_query": "review photosynthesis",
            "obs_file_list": [],
        },
        "InSilicoResearchAgent": {
            "user_query": "decompose a study",
            "data_list": {},
            "obs_file_list": [],
        },
        "GeneNetworkAgent": {
            "species_code": "osa",
            "to_id": "TO:0000207",
        },
        "BriefGeneAgent": {"user_query": "Os01g0177400"},
        "DeepGenomeAgent": {
            "species_code": "osa",
            "gene_id": "Os01g0177400",
        },
        "DigitalDesignAgent": {
            "species_code": "osa",
            "gene_id": "Os01g0177400",
        },
    }
    return {**payloads[tool_name], **common}


@pytest.mark.parametrize("tool_name,wrapper_name", _CASES)
async def test_handler_forwards_effective_locale(
    tool_name: str,
    wrapper_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every stable handler forwards the validated locale explicitly."""
    captured: dict[str, Any] = {}

    async def wrapper_spy(**kwargs: Any) -> dict[str, Any]:
        """Capture wrapper kwargs and short-circuit external work."""
        captured.update(kwargs)
        return {}

    monkeypatch.setattr(handlers, wrapper_name, wrapper_spy)
    await invoke_tool_raw(tool_name, _minimal_arguments(tool_name))

    assert captured["locale"] == "zh-CN"


def test_chat_option_builder_keeps_locale_and_instruction_together() -> None:
    """Chat option construction emits one locale-bearing provider bag."""
    kwargs = build_chat_kwargs_for(
        fake_chat_config(),
        fake_sensitive_config(),
        locale="zh-CN",
    )

    assert kwargs["locale"] == "zh-CN"
    assert "Simplified Chinese" in kwargs["locale_instruction"]


def test_analysis_send_payload_carries_locale() -> None:
    """Parallel analyst dispatch copies the parent locale to each Send."""
    sends = route_analysis_tasks(
        "analyst_node",
        "target_gene",
        "analysis_tasks",
        {
            "species_code": "osa",
            "target_gene": "Os01g0177400",
            "locale": "zh-CN",
            "analysis_tasks": [{"goal": "profile gene"}],
        },
    )

    assert len(sends) == 1
    assert sends[0].arg["locale"] == "zh-CN"
