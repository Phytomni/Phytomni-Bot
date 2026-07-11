# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Map A2A v1 messages to the existing MCP tool request contract."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from a2a.types import (
    ContentTypeNotSupportedError,
    InvalidParamsError,
    Message,
    Part,
)
from google.protobuf import json_format

from ...agents.expert.router import ToolSelection, select_agent_tool
from ...mcp.schemas import AGENT_TOOL_DEFINITIONS

__all__ = ["A2AToolRequest", "map_a2a_message"]

_TEXT_ARGUMENT_KEYS = ("user_query", "goal_description")
_ToolSelector = Callable[[str], Awaitable[ToolSelection | None]]


@dataclass(frozen=True)
class A2AToolRequest:
    """One MCP tool name and validated transport arguments."""

    tool_name: str
    arguments: dict[str, Any]


def _tool_fields(tool_name: str) -> frozenset[str]:
    """Return the request fields for one catalogued MCP tool."""
    for name, _description, model in AGENT_TOOL_DEFINITIONS:
        if name.value == tool_name:
            return frozenset(model.model_fields)
    raise InvalidParamsError(message=f"unknown A2A skill: {tool_name}")


def _request_skill_id(metadata: Mapping[str, Any]) -> str | None:
    """Read only the request-level ``metadata.skill_id`` value."""
    raw_skill_id = metadata.get("skill_id")
    if raw_skill_id is None or raw_skill_id == "":
        return None
    if not isinstance(raw_skill_id, str):
        raise InvalidParamsError(message="metadata.skill_id must be a string")
    return raw_skill_id


def _text_parts(parts: list[Part]) -> str:
    """Join all text parts while retaining their message order."""
    values: list[str] = []
    for part in parts:
        if part.WhichOneof("content") == "text":
            values.append(part.text)
    return "\n\n".join(value for value in values if value)


def _data_parts(parts: list[Part]) -> dict[str, Any]:
    """Decode object-valued data parts and merge them without overwrites."""
    merged: dict[str, Any] = {}
    for part in parts:
        if part.WhichOneof("content") != "data":
            continue
        value = part.data
        if value.WhichOneof("kind") != "struct_value":
            raise ContentTypeNotSupportedError(
                message="A2A data parts must contain a JSON object"
            )
        decoded = json_format.MessageToDict(
            value,
            preserving_proto_field_name=True,
        )
        if not isinstance(decoded, dict):
            raise ContentTypeNotSupportedError(
                message="A2A data parts must contain a JSON object"
            )
        for key, item in decoded.items():
            if key in merged and merged[key] != item:
                raise InvalidParamsError(
                    message=f"conflicting A2A data value for '{key}'"
                )
            merged[key] = item
    return merged


def _merge_text(
    arguments: dict[str, Any],
    text: str,
    tool_name: str,
) -> dict[str, Any]:
    """Put text into the tool's textual field, rejecting ambiguity."""
    if not text:
        return arguments
    fields = _tool_fields(tool_name)
    text_keys = [key for key in _TEXT_ARGUMENT_KEYS if key in fields]
    if not text_keys:
        raise InvalidParamsError(
            message=f"A2A text content is not accepted by {tool_name}"
        )
    target = text_keys[0]
    existing = arguments.get(target)
    if existing is not None and existing != text:
        raise InvalidParamsError(
            message=f"conflicting A2A text value for '{target}'"
        )
    arguments[target] = text
    return arguments


def _parts(message: Message) -> list[Part]:
    """Validate and copy message parts before any content conversion."""
    if not message.parts:
        raise InvalidParamsError(message="A2A message must contain a part")
    parts = list(message.parts)
    for part in parts:
        content = part.WhichOneof("content")
        if content is None:
            raise InvalidParamsError(message="A2A part has no content")
        if content in {"raw", "url"}:
            raise ContentTypeNotSupportedError(
                message=f"A2A part content '{content}' is not supported"
            )
    return parts


async def map_a2a_message(
    message: Message,
    *,
    request_metadata: Mapping[str, Any] | None = None,
    select_agent: _ToolSelector = select_agent_tool,
) -> A2AToolRequest:
    """Map one A2A user message to an existing MCP tool request.

    ``request_metadata`` is deliberately separate from ``message.metadata``:
    only the JSON-RPC request-level ``metadata.skill_id`` controls routing.
    When no skill is supplied, the injected expert selector chooses the MCP
    tool from the textual content; a selector that returns no tool falls back
    to the existing ChatAgent behavior.
    """
    parts = _parts(message)
    text = _text_parts(parts)
    arguments = _data_parts(parts)
    skill_id = _request_skill_id(request_metadata or {})
    text_already_routed = False

    if skill_id is None:
        if not text:
            for key in _TEXT_ARGUMENT_KEYS:
                value = arguments.get(key)
                if isinstance(value, str) and value:
                    text = value
                    break
        if not text:
            raise InvalidParamsError(
                message="A2A expert routing requires text content"
            )
        selection = await select_agent(text)
        if selection is None:
            skill_id = "ChatAgent"
            arguments.setdefault("obs_file_list", [])
        else:
            skill_id = selection.tool_name
            selected_arguments = dict(selection.arguments)
            for key, value in arguments.items():
                if (
                    key in selected_arguments
                    and selected_arguments[key] != value
                ):
                    raise InvalidParamsError(
                        message=f"conflicting A2A value for '{key}'"
                    )
                selected_arguments[key] = value
            arguments = selected_arguments
            text_already_routed = any(
                key in arguments for key in _TEXT_ARGUMENT_KEYS
            )

    _tool_fields(skill_id)
    if not text_already_routed:
        arguments = _merge_text(arguments, text, skill_id)
    return A2AToolRequest(tool_name=skill_id, arguments=arguments)
