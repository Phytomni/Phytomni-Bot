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
    assert server.PhytomniAgents.CHATAGENT.value == "ChatAgent"
    assert server.PhytomniAgents.KNOWLEDGEAGENT.value == "KnowledgeAgent"
    assert server.PhytomniAgents.DATAAGENT.value == "DataAgent"
    assert server.PhytomniAgents.ANALYSTAGENT.value == "AnalystAgent"
    assert server.PhytomniAgents.REVIEWAGENT.value == "ReviewAgent"
    assert server.PhytomniAgents.BRIEFGENEAGENT.value == "BriefGeneAgent"
    assert server.PhytomniAgents.DEEPGENOMEAGENT.value == "DeepGenomeAgent"
    assert (
        server.PhytomniAgents.INSILICORESEARCHAGENT.value
        == "InSilicoResearchAgent"
    )
    assert server.PhytomniAgents.DIGITALDESIGNAGENT.value == (
        "DigitalDesignAgent"
    )
    assert server.PhytomniAgents.GENENETWORKAGENT.value == "GeneNetworkAgent"
