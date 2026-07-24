# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Autonomous in-process tool router for the HTTP Expert mode.

Public: ToolSelection, ToolSelectionError, select_agent_tool.

Re-implements the client ``PhytomniToolRouter.route_query`` selection
step in-process: one OpenAI tool-calling completion over the MCP agent
tool surface using the operator's main model. Dispatch stays with the
HTTP layer, which maps the tool to a slug and reuses _invoke_agent_run.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from openai import AsyncOpenAI

from ...config.settings import get_sensitive_config
from ...mcp.schemas import agent_openai_tool_specs

__all__ = ["ToolSelection", "ToolSelectionError", "select_agent_tool"]

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolSelection:
    """One agent tool the router chose plus its LLM-extracted arguments.

    Attributes:
        tool_name: Public MCP tool name the model selected (e.g.
            ``"KnowledgeAgent"``).
        arguments: JSON arguments the routing model produced from the
            tool's schema. Forwarded as-is to dispatch because four agents
            (analyst / deep_genome / design / network) have no
            ``user_query`` field, so a fixed argument shape is impossible.
    """

    tool_name: str
    arguments: dict[str, Any]


class ToolSelectionError(RuntimeError):
    """The routing model violated the constrained one-tool contract."""


async def select_agent_tool(
    user_query: str,
    history: Sequence[Mapping[str, Any]] = (),
    *,
    allowed_tools: Sequence[str] | None = None,
    forced_tool: str | None = None,
) -> ToolSelection | None:
    """Pick one MCP agent tool for a query with the main conversation model.

    Runs a single OpenAI tool-calling completion over the ten dispatchable
    agents (``agent_openai_tool_specs``; GetTaskStatus excluded) with
    ``tool_choice="auto"``. Mirrors the stdio client's
    ``PhytomniToolRouter.route_query`` selection step, but in-process so
    the HTTP Expert route needs no subprocess. The model fills each tool's
    arguments from its JSON schema; the caller forwards them unchanged.

    Args:
        user_query: The natural-language user turn to route.
        history: Prior ``{"role", "content"}`` turns for routing context
            only; never forwarded to the dispatched agent.

    Returns:
        A ``ToolSelection`` when the model picked a tool. Legacy callers
        without ``allowed_tools`` receive ``None`` when it answered without
        one (the caller falls back to the chat agent).

    Raises:
        ToolSelectionError: The strict routing contract is violated.
        Exception: OpenAI client / transport errors propagate so the HTTP
            layer can surface a 502; the router does not swallow them.
    """
    sensitive = get_sensitive_config()
    # Expert mode runs on the operator Bot, where relay mode is disabled,
    # so the routing call uses the operator's main-model credentials
    # directly (mirrors agents/chat/service.py's AsyncOpenAI construction).
    # A relay-child Expert deployment would need the _relay_llm_endpoint
    # rewrite from agents/chat/service.py.
    client = AsyncOpenAI(
        api_key=sensitive.API_KEY.get_secret_value(),
        base_url=sensitive.BASE_URL or None,
    )
    messages: list[dict[str, Any]] = [
        *(dict(turn) for turn in history),
        {"role": "user", "content": user_query},
    ]
    all_specs = agent_openai_tool_specs()
    strict = allowed_tools is not None
    if strict:
        assert allowed_tools is not None
        allowed_order = list(allowed_tools)
        specs_by_name = {
            str(spec["function"]["name"]): spec for spec in all_specs
        }
        if not allowed_order:
            raise ToolSelectionError("strict routing requires an allowed tool")
        try:
            tools = [specs_by_name[name] for name in allowed_order]
        except KeyError as exc:
            raise ToolSelectionError(
                "strict routing received an unknown allowed tool"
            ) from exc
        tool_choice: Any = (
            {
                "type": "function",
                "function": {"name": forced_tool},
            }
            if forced_tool is not None
            else "required"
        )
    else:
        allowed_order = []
        tools = all_specs
        tool_choice = "auto"
    completion = await client.chat.completions.create(
        model=sensitive.MODEL_ID,
        messages=cast(Any, messages),
        tools=cast(Any, tools),
        tool_choice=tool_choice,
    )
    if not completion.choices:
        if strict:
            raise ToolSelectionError("routing model returned no choice")
        return None
    message = completion.choices[0].message
    tool_calls = message.tool_calls or []
    if not tool_calls:
        if strict:
            raise ToolSelectionError("routing model returned no tool call")
        return None
    if strict and len(tool_calls) != 1:
        raise ToolSelectionError(
            "routing model must return exactly one tool call"
        )
    function = getattr(tool_calls[0], "function", None)
    if function is None:
        if strict:
            raise ToolSelectionError(
                "routing model returned a malformed tool call"
            )
        return None
    selected_name = str(function.name)
    if strict and selected_name not in allowed_order:
        raise ToolSelectionError(
            "routing model selected a tool outside the allowlist"
        )
    if forced_tool is not None and selected_name != forced_tool:
        raise ToolSelectionError("routing model did not honor the forced tool")
    return ToolSelection(
        tool_name=selected_name,
        arguments=_parse_tool_arguments(str(function.arguments)),
    )


def _parse_tool_arguments(raw: str) -> dict[str, Any]:
    """Parse the model's JSON tool arguments, defaulting to empty on junk.

    Args:
        raw: The ``tool_call.function.arguments`` JSON string.

    Returns:
        The parsed object, or ``{}`` when the model returned non-JSON or a
        non-object so dispatch surfaces a schema-validation error rather
        than crashing the router.
    """
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        _LOGGER.warning("Routing model returned non-JSON tool arguments")
        return {}
    return parsed if isinstance(parsed, dict) else {}
