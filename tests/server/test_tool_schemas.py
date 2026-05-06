# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for MCP tool parameter schemas."""

import pytest
from pydantic import ValidationError

from mcp_server_phytomni import server

pytestmark = pytest.mark.server


TOOL_MODELS = [
    server.ChatAgent,
    server.KnowledgeAgent,
    server.DataAgent,
    server.AnalystAgent,
    server.DeepGenomeAgent,
    server.ReviewAgent,
    server.BriefGeneAgent,
    server.InSilicoResearchAgent,
    server.DigitalDesignAgent,
    server.GeneNetworkAgent,
]


def test_tool_models_generate_object_json_schemas():
    for model in TOOL_MODELS:
        schema = model.model_json_schema()

        assert schema["type"] == "object"
        assert schema["title"] == model.__name__
        assert schema["properties"]
        assert schema["required"]


def test_chat_agent_validates_required_fields():
    arguments = server.ChatAgent(
        user_query="What is photosynthesis?",
        obs_file_list=[],
    )

    assert arguments.user_query == "What is photosynthesis?"
    assert arguments.obs_file_list == []

    with pytest.raises(ValidationError):
        server.ChatAgent(user_query="missing file list")


def test_public_agent_enum_values_remain_stable():
    assert server.PhytomniAgents.CHAT_AGENT.value == "ChatAgent"
    assert server.PhytomniAgents.KNOWLEDGE_AGENT.value == "KnowledgeAgent"
    assert server.PhytomniAgents.DATA_AGENT.value == "DataAgent"
    assert server.PhytomniAgents.ANALYST_AGENT.value == "AnalystAgent"
    assert server.PhytomniAgents.REVIEW_AGENT.value == "ReviewAgent"
    assert server.PhytomniAgents.BRIEF_GENE_AGENT.value == "BriefGeneAgent"
    assert server.PhytomniAgents.DEEP_GENOME_AGENT.value == "DeepGenomeAgent"
    assert (
        server.PhytomniAgents.IN_SILICO_RESEARCH_AGENT.value
        == "InSilicoResearchAgent"
    )
    assert server.PhytomniAgents.DIGITAL_DESIGN_AGENT.value == (
        "DigitalDesignAgent"
    )
    assert server.PhytomniAgents.GENE_NETWORK_AGENT.value == "GeneNetworkAgent"


def test_public_agent_enum_members_use_constant_style_names():
    assert "CHATAGENT" not in server.PhytomniAgents.__members__
    assert "INSILICORESEARCHAGENT" not in server.PhytomniAgents.__members__
    assert "CHAT_AGENT" in server.PhytomniAgents.__members__
    assert "IN_SILICO_RESEARCH_AGENT" in server.PhytomniAgents.__members__
