# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Canonical result-formatting dispatch and compatibility envelope."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ...common.reasoning_content import normalize_chat_completion_dict
from . import cited as _cited
from . import tasks as _tasks
from ._shared import (
    first_message,
    json_dumps,
    mapping_sequence,
    payload_mapping,
)
from .execution import (
    apply_compatibility_projection,
    build_execution_projection,
)
from .models import FormattedToolResult, ToolResultEnvelope
from .redaction import sanitize_raw

Formatter = Callable[..., FormattedToolResult]

_TOOL_ALIASES = {
    "AnalysisAgents": "AnalystAgent",
    "ChatAgents": "ChatAgent",
    "DatabaseAgents": "DataAgent",
    "KnowledgeAgents": "KnowledgeAgent",
    "ReviewAgents": "ReviewAgent",
}
_CITED_TOOLS = frozenset(
    {
        "KnowledgeAgent",
        "ReviewAgent",
        "BriefGeneAgent",
        "DeepGenomeAgent",
    }
)
_FORMATTERS: dict[str, Formatter] = {
    "ChatAgent": _cited.format_message_result,
    "KnowledgeAgent": _cited.format_cited_message_result,
    "ReviewAgent": _cited.format_cited_message_result,
    "BriefGeneAgent": _cited.format_cited_message_result,
    "DataAgent": _tasks.format_data_result,
    "AnalystAgent": _tasks.format_analyst_task_result,
    "DeepGenomeAgent": _tasks.format_deep_genome_result,
    "GeneNetworkAgent": _tasks.format_network_task_result,
    "InSilicoResearchAgent": _tasks.format_in_silico_result,
    "DigitalDesignAgent": _tasks.format_design_result,
    "GetTaskStatus": _tasks.format_task_status_result,
}


def normalize_tool_name(tool_name: str) -> str:
    """Return the canonical public MCP tool name."""
    return _TOOL_ALIASES.get(tool_name, tool_name)


def is_cited_tool(tool_name: str) -> bool:
    """Return True if the tool routes through the cited formatter."""
    return normalize_tool_name(tool_name) in _CITED_TOOLS


def _looks_like_cited_message(payload: Mapping[str, Any]) -> bool:
    """Return True when the payload carries a cited chat message."""
    message = first_message(payload)
    if mapping_sequence(message.get("doc_list")):
        return True
    content = message.get("content")
    return isinstance(content, str) and bool(content.strip())


def format_tool_result(
    tool_name: str,
    payload: Any,
    *,
    arguments: Mapping[str, Any] | None = None,
) -> FormattedToolResult:
    """Format one MCP tool payload using the registered tool name."""
    name = normalize_tool_name(tool_name)
    payload_map = payload_mapping(payload)
    if name == "DeepGenomeAgent" and _looks_like_cited_message(payload_map):
        return _cited.format_cited_message_result(
            payload_map, arguments=arguments
        )
    formatter = _FORMATTERS.get(name)
    if formatter is None:
        return FormattedToolResult(answer=json_dumps(payload))
    return formatter(payload_map, arguments=arguments)


def build_tool_result_envelope(
    tool_name: str,
    payload: Any,
    *,
    arguments: Mapping[str, Any] | None = None,
) -> ToolResultEnvelope:
    """Build a full result envelope for one tool response."""
    normalized_payload = normalize_chat_completion_dict(payload)
    execution = build_execution_projection(
        normalize_tool_name(tool_name), normalized_payload
    )
    formatted = apply_compatibility_projection(
        format_tool_result(
            tool_name,
            normalized_payload,
            arguments=arguments,
        ),
        execution,
    )
    return ToolResultEnvelope(
        formatted=formatted,
        raw=sanitize_raw(normalized_payload),
        execution=execution,
    )


__all__ = [
    "build_tool_result_envelope",
    "format_tool_result",
    "is_cited_tool",
    "normalize_tool_name",
]
