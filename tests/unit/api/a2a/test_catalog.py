# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the MCP-to-A2A skill catalog projection."""

from __future__ import annotations

import pytest
from google.protobuf import json_format

from mcp_server_phytomni.api.a2a.catalog import build_a2a_skills
from mcp_server_phytomni.mcp.schemas import AGENT_TOOL_DEFINITIONS

pytestmark = pytest.mark.unit


def test_a2a_catalog_has_exactly_the_dispatchable_mcp_tools() -> None:
    """The catalog contains all ten agents and excludes GetTaskStatus."""
    skills = build_a2a_skills()
    source_names = sorted(
        name.value for name, _description, _model in AGENT_TOOL_DEFINITIONS
    )

    assert len(skills) == 10
    assert [skill.id for skill in skills] == source_names
    assert all(skill.id != "GetTaskStatus" for skill in skills)


def test_a2a_catalog_is_deterministic_and_uses_mime_modes() -> None:
    """Repeated builds serialize identically and expose stable MIME modes."""
    first = build_a2a_skills()
    second = build_a2a_skills()

    assert [json_format.MessageToJson(skill) for skill in first] == [
        json_format.MessageToJson(skill) for skill in second
    ]
    for skill in first:
        assert skill.name
        assert skill.description
        assert list(skill.tags)
        assert list(skill.input_modes) == ["text/plain", "application/json"]
        assert list(skill.output_modes) == ["text/plain", "application/json"]


def test_a2a_catalog_does_not_mutate_mcp_source_definitions() -> None:
    """Skill construction must not alter the shared MCP definition tuple."""
    before = tuple(AGENT_TOOL_DEFINITIONS)

    build_a2a_skills()

    assert tuple(AGENT_TOOL_DEFINITIONS) == before
