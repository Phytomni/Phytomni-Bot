# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for MCP tool parameter schemas.

Covers generated JSON schema shape, ChatAgent field validation, stable public
agent enum values, constant-style enum names, omitted legacy names, and that
every demo_data payload validates under its declared MCP tool schema.
"""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

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


DEMO_PAYLOADS_DIR = (
    Path(__file__).resolve().parents[2] / "demo_data" / "payloads"
)

DEMO_PAYLOAD_TO_MODEL: dict[str, type[BaseModel]] = {
    "chat_agent.json": schemas.ChatAgent,
    "knowledge_agent.json": schemas.KnowledgeAgent,
    "data_agent.json": schemas.DataAgent,
    "analyst_agent.json": schemas.AnalystAgent,
    "review_agent.json": schemas.ReviewAgent,
    "brief_gene_agent.json": schemas.BriefGeneAgent,
    "deep_genome_agent.json": schemas.DeepGenomeAgent,
    "in_silico_research_agent.json": schemas.InSilicoResearchAgent,
    "digital_design_agent.json": schemas.DigitalDesignAgent,
    "gene_network_agent.json": schemas.GeneNetworkAgent,
}


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
        schemas.ChatAgent.model_validate({"user_query": "missing file list"})


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
    """Verify public agent enum values remain stable.

    Args:
        member_name: Enum member name under test.
        value: Expected public MCP tool name.
    """
    assert schemas.PhytomniAgents[member_name].value == value


@pytest.mark.parametrize(
    "member_name",
    ["CHAT_AGENT", "IN_SILICO_RESEARCH_AGENT"],
)
def test_public_agent_enum_members_use_constant_style_names(member_name):
    """Verify public agent enum members use constant style names.

    Args:
        member_name: Constant-style enum member expected to exist.
    """
    assert member_name in schemas.PhytomniAgents.__members__


@pytest.mark.parametrize(
    "member_name",
    ["CHATAGENT", "INSILICORESEARCHAGENT"],
)
def test_public_agent_enum_omits_legacy_member_names(member_name):
    """Verify public agent enum omits legacy member names.

    Args:
        member_name: Legacy enum member name expected to be absent.
    """
    assert member_name not in schemas.PhytomniAgents.__members__


@pytest.mark.parametrize(
    "payload_path",
    sorted(DEMO_PAYLOADS_DIR.glob("*.json")),
    ids=lambda path: path.name,
)
def test_demo_payload_matches_schema(payload_path):
    """Verify each demo_data payload validates under its MCP tool schema.

    Args:
        payload_path: Filesystem path to a demo_data payload JSON file.
    """
    model = DEMO_PAYLOAD_TO_MODEL[payload_path.name]
    payload = json.loads(payload_path.read_text(encoding="utf-8"))

    instance = model.model_validate(payload)

    assert isinstance(instance, model)


def test_demo_payload_map_covers_every_tool_model():
    """Verify every public tool model has a matching demo payload entry."""
    assert set(DEMO_PAYLOAD_TO_MODEL.values()) == set(TOOL_MODELS)
