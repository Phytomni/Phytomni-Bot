# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for MCP tool parameter schemas."""

import pytest
from pydantic import ValidationError

from mcp_server_phytomni.mcp import schemas

pytestmark = pytest.mark.server


TOOL_MODELS = [
    schemas.ChatAgent,
    schemas.KnowledgeAgent,
    schemas.DataAgent,
    schemas.AnalystAgent,
    schemas.DeepGenomeAgent,
    schemas.ReviewAgent,
    schemas.BriefGeneAgent,
    schemas.InSilicoResearchAgent,
    schemas.DigitalDesignAgent,
    schemas.GeneNetworkAgent,
]


def test_tool_models_generate_object_json_schemas():
    """Verify tool models generate object json schemas."""
    for model in TOOL_MODELS:
        schema = model.model_json_schema()

        assert schema["type"] == "object"
        assert schema["title"] == model.__name__
        assert schema["properties"]
        assert schema["required"]


def test_chat_agent_validates_required_fields():
    """Verify chat agent validates required fields."""
    arguments = schemas.ChatAgent(
        user_query="What is photosynthesis?",
        obs_file_list=[],
    )

    assert arguments.user_query == "What is photosynthesis?"
    assert arguments.obs_file_list == []

    with pytest.raises(ValidationError):
        schemas.ChatAgent(user_query="missing file list")


@pytest.mark.parametrize(
    ("member_name", "value"),
    [
        ("CHAT_AGENT", "ChatAgent"),
        ("KNOWLEDGE_AGENT", "KnowledgeAgent"),
        ("DATA_AGENT", "DataAgent"),
        ("ANALYST_AGENT", "AnalystAgent"),
        ("REVIEW_AGENT", "ReviewAgent"),
        ("BRIEF_GENE_AGENT", "BriefGeneAgent"),
        ("DEEP_GENOME_AGENT", "DeepGenomeAgent"),
        ("IN_SILICO_RESEARCH_AGENT", "InSilicoResearchAgent"),
        ("DIGITAL_DESIGN_AGENT", "DigitalDesignAgent"),
        ("GENE_NETWORK_AGENT", "GeneNetworkAgent"),
    ],
)
def test_public_agent_enum_values_remain_stable(member_name, value):
    """Verify public agent enum values remain stable."""
    assert schemas.PhytomniAgents[member_name].value == value


@pytest.mark.parametrize(
    "member_name",
    ["CHAT_AGENT", "IN_SILICO_RESEARCH_AGENT"],
)
def test_public_agent_enum_members_use_constant_style_names(member_name):
    """Verify public agent enum members use constant style names."""
    assert member_name in schemas.PhytomniAgents.__members__


@pytest.mark.parametrize(
    "member_name",
    ["CHATAGENT", "INSILICORESEARCHAGENT"],
)
def test_public_agent_enum_omits_legacy_member_names(member_name):
    """Verify public agent enum omits legacy member names."""
    assert member_name not in schemas.PhytomniAgents.__members__
