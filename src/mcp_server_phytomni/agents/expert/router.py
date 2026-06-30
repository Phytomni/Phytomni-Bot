# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Autonomous in-process tool router for the HTTP Expert mode.

Public: ToolSelection, select_agent_tool.

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

__all__ = ["ToolSelection", "select_agent_tool"]

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


async def select_agent_tool(
    user_query: str,
    history: Sequence[Mapping[str, Any]] = (),
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
        A ``ToolSelection`` when the model picked a tool, or ``None`` when
        it answered without one (the caller falls back to the chat agent).

    Raises:
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
    completion = await client.chat.completions.create(
        model=sensitive.MODEL_ID,
        messages=cast(Any, messages),
        tools=cast(Any, agent_openai_tool_specs()),
        tool_choice="auto",
    )
    if not completion.choices:
        return None
    message = completion.choices[0].message
    tool_calls = message.tool_calls or []
    if not tool_calls:
        return None
    function = getattr(tool_calls[0], "function", None)
    if function is None:
        return None
    return ToolSelection(
        tool_name=str(function.name),
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
