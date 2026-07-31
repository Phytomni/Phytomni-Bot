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
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

import httpx
from openai import APIError, APITimeoutError, AsyncOpenAI, BadRequestError

from ...config.settings import get_sensitive_config
from ...mcp.schemas import agent_openai_tool_specs
from ...runtime.locale import (
    SupportedLocale,
    current_effective_locale,
    locale_instruction,
)

__all__ = [
    "ExpertCompletion",
    "ExpertProviderError",
    "ExpertProviderTimeoutError",
    "ExpertRoutingOptions",
    "ExpertRoutingContractError",
    "ToolSelection",
    "ToolSelectionError",
    "complete_expert_routing",
    "select_agent_tool",
    "select_expert_tool",
]

_LOGGER = logging.getLogger(__name__)

# Base URLs (empty string when unset) observed to reject
# ``tool_choice="required"`` with an HTTP 400. Once an endpoint is recorded
# here, strict routing sends ``"auto"`` up front instead of paying a wasted
# 400 round-trip. Process-local and non-secret, mirroring how the router
# already reads ``sensitive.BASE_URL``; never persisted.
_TOOL_CHOICE_REQUIRED_UNSUPPORTED: set[str] = set()


@dataclass(frozen=True, slots=True)
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


class ExpertRoutingContractError(RuntimeError):
    """The routing model violated the constrained one-tool contract."""


class ToolSelectionError(ExpertRoutingContractError):
    """Backward-compatible name for selector contract violations."""


class ExpertProviderError(RuntimeError):
    """A non-timeout failure occurred while calling the routing provider."""


class ExpertProviderTimeoutError(ExpertProviderError):
    """The routing provider did not answer within its configured timeout."""


ExpertCompletion = Callable[..., Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ExpertRoutingOptions:
    """Strict selector constraints and its injectable completion seam."""

    allowed_tools: tuple[str, ...]
    forced_tool: str | None
    locale: SupportedLocale
    completion: ExpertCompletion | None = None


@dataclass(frozen=True, slots=True)
class _RoutingRequest:
    """Prepared tool surface and selection contract for one routing call."""

    allowed_order: tuple[str, ...]
    tools: list[dict[str, Any]]
    tool_choice: Any
    strict: bool


async def select_agent_tool(
    user_query: str,
    history: Sequence[Mapping[str, Any]] = (),
    *,
    allowed_tools: Sequence[str] | None = None,
    forced_tool: str | None = None,
    completion: ExpertCompletion | None = None,
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
        ExpertProviderTimeoutError: The routing provider timed out.
        ExpertProviderError: The routing provider failed without timing out.
    """
    if allowed_tools is not None:
        return await select_expert_tool(
            user_query=user_query,
            history=history,
            options=ExpertRoutingOptions(
                allowed_tools=tuple(allowed_tools),
                forced_tool=forced_tool,
                locale=current_effective_locale(),
                completion=completion,
            ),
        )

    request = _build_routing_request(
        agent_openai_tool_specs(), allowed_tools, forced_tool
    )
    messages = [
        *(dict(turn) for turn in history),
        {"role": "user", "content": user_query},
    ]
    result = await _run_completion(
        messages=messages,
        request=request,
        completion=completion,
    )
    return _selection_from_completion(result, request, forced_tool)


async def select_expert_tool(
    *,
    user_query: str,
    history: Sequence[Mapping[str, Any]],
    options: ExpertRoutingOptions,
) -> ToolSelection:
    """Select exactly one caller-authorized canonical agent tool.

    This is the strict HTTP Expert seam. The legacy ``select_agent_tool``
    wrapper deliberately keeps its optional-selection behavior only when no
    allowlist is supplied, which is the compatibility path used by A2A.
    """
    request = _build_routing_request(
        agent_openai_tool_specs(), options.allowed_tools, options.forced_tool
    )
    messages = [
        {"role": "system", "content": locale_instruction(options.locale)},
        *(dict(turn) for turn in history),
        {"role": "user", "content": user_query},
    ]
    result = await _run_completion(
        messages=messages,
        request=request,
        completion=options.completion,
    )
    selection = _selection_from_completion(
        result, request, options.forced_tool
    )
    if selection is None:
        raise ToolSelectionError("strict routing returned no selection")
    return selection


async def complete_expert_routing(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_choice: Any,
) -> Any:
    """Run one provider completion for Expert selection.

    Provider exception details are intentionally discarded at this boundary;
    the HTTP layer maps the typed outcome to the public safe error envelope.

    Some OpenAI-compatible endpoints (e.g. the Huawei pangu ``mastudio``
    deployment) reject a constrained ``tool_choice`` -- both the bare
    ``"required"`` sentinel and a named ``{"type": "function", ...}`` choice --
    with an HTTP 400 while honoring ``"auto"``. The two rejections carry
    different error bodies (a validation message vs. a generic ``PANGU.3342``),
    so detection keys off the constrained choice, not the error text: any 400
    on a constrained choice records the endpoint and retries once with
    ``"auto"`` over the full tool list. Later constrained calls to a recorded
    endpoint skip straight to ``"auto"``. The forced-tool guarantee is then
    enforced by ``_selection_from_completion``, which coerces the final
    selection to ``forced_tool`` regardless of what the auto retry returned.
    """
    sensitive = get_sensitive_config()
    base_url = sensitive.BASE_URL or None
    client = AsyncOpenAI(
        api_key=sensitive.API_KEY.get_secret_value(),
        base_url=base_url,
    )
    base_url_key = base_url or ""
    constrained = _is_constrained_choice(tool_choice)
    effective_choice = tool_choice
    if constrained and base_url_key in _TOOL_CHOICE_REQUIRED_UNSUPPORTED:
        effective_choice = "auto"

    async def _create(choice: Any) -> Any:
        return await client.chat.completions.create(
            model=sensitive.MODEL_ID,
            messages=cast(Any, messages),
            tools=cast(Any, tools),
            tool_choice=choice,
        )

    try:
        return await _create(effective_choice)
    except (APITimeoutError, httpx.TimeoutException, TimeoutError) as exc:
        raise ExpertProviderTimeoutError() from exc
    except BadRequestError as exc:
        if effective_choice != "auto" and constrained:
            _TOOL_CHOICE_REQUIRED_UNSUPPORTED.add(base_url_key)
            _LOGGER.warning(
                "Endpoint rejected constrained tool_choice; "
                "retrying once with 'auto'."
            )
            return await _complete_with_auto_fallback(_create)
        raise ExpertProviderError() from exc
    except APIError as exc:
        raise ExpertProviderError() from exc


def _is_constrained_choice(tool_choice: Any) -> bool:
    """True when ``tool_choice`` pins a selection (``required`` or named)."""
    if tool_choice == "required":
        return True
    if isinstance(tool_choice, Mapping):
        function = tool_choice.get("function")
        return isinstance(function, Mapping) and bool(function.get("name"))
    return False


async def _complete_with_auto_fallback(
    create: Callable[[Any], Awaitable[Any]],
) -> Any:
    """Retry a routing completion with ``tool_choice="auto"``."""
    try:
        return await create("auto")
    except (APITimeoutError, httpx.TimeoutException, TimeoutError) as exc:
        raise ExpertProviderTimeoutError() from exc
    except APIError as exc:
        raise ExpertProviderError() from exc


async def _run_completion(
    *,
    messages: list[dict[str, Any]],
    request: _RoutingRequest,
    completion: ExpertCompletion | None,
) -> Any:
    """Invoke the injectable completion seam with no provider data leakage."""
    provider = completion or complete_expert_routing
    return await provider(
        messages=messages,
        tools=request.tools,
        tool_choice=request.tool_choice,
    )


def _build_routing_request(
    all_specs: list[dict[str, Any]],
    allowed_tools: Sequence[str] | None,
    forced_tool: str | None,
) -> _RoutingRequest:
    """Prepare the model tool surface and strict-selection contract."""
    if allowed_tools is None:
        return _RoutingRequest((), all_specs, "auto", False)
    allowed_order = tuple(allowed_tools)
    specs_by_name = {str(spec["function"]["name"]): spec for spec in all_specs}
    if (
        not allowed_order
        or len(allowed_order) > 10
        or len(set(allowed_order)) != len(allowed_order)
    ):
        raise ToolSelectionError("strict routing requires an allowed tool")
    if any(name not in specs_by_name for name in allowed_order):
        raise ToolSelectionError("strict routing received an unknown tool")
    if forced_tool is not None and forced_tool not in allowed_order:
        raise ToolSelectionError("strict routing forced tool is not allowed")
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
    return _RoutingRequest(allowed_order, tools, tool_choice, True)


def _selection_from_completion(
    completion: Any,
    request: _RoutingRequest,
    forced_tool: str | None,
) -> ToolSelection | None:
    """Validate one model completion against the prepared routing contract.

    When ``forced_tool`` is set the caller pinned an ``@agent``, so the final
    selection is coerced to it unconditionally: the auto fallback used on
    endpoints that reject a named ``tool_choice`` may return a different tool
    or none at all, and the user's explicit choice must still win.
    """
    if forced_tool is not None:
        return ToolSelection(
            tool_name=forced_tool,
            arguments=_forced_arguments(completion),
        )
    choices = getattr(completion, "choices", None)
    if not choices:
        if request.strict:
            raise ToolSelectionError("routing model returned no choice")
        return None
    message = _field(choices[0], "message")
    tool_calls = _field(message, "tool_calls") or []
    if not tool_calls:
        if request.strict:
            raise ToolSelectionError("routing model returned no tool call")
        return None
    if request.strict and len(tool_calls) != 1:
        raise ToolSelectionError(
            "routing model must return exactly one tool call"
        )
    function = _field(tool_calls[0], "function")
    selected_name = _field(function, "name")
    raw_arguments = _field(function, "arguments")
    if (
        function is None
        or not isinstance(selected_name, str)
        or not selected_name
        or not isinstance(raw_arguments, str)
    ):
        if request.strict:
            raise ToolSelectionError(
                "routing model returned a malformed tool call"
            )
        return None
    if request.strict and selected_name not in request.allowed_order:
        raise ToolSelectionError(
            "routing model selected a tool outside the allowlist"
        )
    return ToolSelection(
        tool_name=selected_name,
        arguments=_parse_tool_arguments(raw_arguments, strict=request.strict),
    )


def _field(value: Any, name: str) -> Any:
    """Read one SDK object or mapping field without exposing its contents."""
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _parse_tool_arguments(raw: str, *, strict: bool = False) -> dict[str, Any]:
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
    except (json.JSONDecodeError, TypeError):
        if strict:
            raise ToolSelectionError(
                "routing model returned invalid tool arguments"
            ) from None
        _LOGGER.warning("Routing model returned non-JSON tool arguments")
        return {}
    if not isinstance(parsed, dict):
        if strict:
            raise ToolSelectionError(
                "routing model returned non-object tool arguments"
            )
        return {}
    return parsed


def _forced_arguments(completion: Any) -> dict[str, Any]:
    """Best-effort arguments for a coerced forced-tool selection.

    Reuses whatever the model produced (even for a different tool under the
    auto fallback) and never raises, so a forced route always resolves to a
    ``ToolSelection``; dispatch re-validates the arguments against the forced
    tool's own schema. Falls back to ``{}`` on a missing or malformed call.
    """
    choices = getattr(completion, "choices", None)
    if not choices:
        return {}
    tool_calls = _field(_field(choices[0], "message"), "tool_calls") or []
    if not tool_calls:
        return {}
    raw_arguments = _field(_field(tool_calls[0], "function"), "arguments")
    if not isinstance(raw_arguments, str):
        return {}
    return _parse_tool_arguments(raw_arguments, strict=False)
