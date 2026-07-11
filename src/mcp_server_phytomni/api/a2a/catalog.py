# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Deterministic A2A skill projection from the MCP agent catalog."""

from __future__ import annotations

from a2a.types import AgentSkill

from ...mcp.schemas import AGENT_TOOL_DEFINITIONS

__all__ = ["build_a2a_skills"]

_INPUT_MODES = ["text/plain", "application/json"]
_OUTPUT_MODES = ["text/plain", "application/json"]


def _skill_tags(tool_name: str) -> list[str]:
    """Derive stable, non-empty discovery tags from an MCP tool name."""
    stem = tool_name.removesuffix("Agent").strip()
    return [stem.lower() or "plant-science"]


def build_a2a_skills() -> list[AgentSkill]:
    """Build the sorted A2A skill list from the MCP source of truth.

    Returns:
        Fresh protobuf skill messages sorted by their MCP tool id. The
        dispatch-only ``GetTaskStatus`` tool is absent because it is not part
        of ``AGENT_TOOL_DEFINITIONS``.
    """
    skills = [
        AgentSkill(
            id=tool_name.value,
            name=tool_name.value.removesuffix("Agent"),
            description=description.value,
            tags=_skill_tags(tool_name.value),
            input_modes=list(_INPUT_MODES),
            output_modes=list(_OUTPUT_MODES),
        )
        for tool_name, description, _model in AGENT_TOOL_DEFINITIONS
    ]
    return sorted(skills, key=lambda skill: skill.id)
