# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Autonomous in-process tool router for the HTTP Expert mode.

Public surface is ``__all__``. Re-implements the stdio client's
``PhytomniToolRouter.route_query`` step in-process. Dispatch stays
in the HTTP layer. HTTP-only; stdio MCP does not call this package.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from random import uniform
from typing import Any, cast

import httpx
from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    BadRequestError,
)

from ...config.settings import get_sensitive_config
from ...mcp.schemas import agent_openai_tool_specs
from ...runtime.locale import (
    SupportedLocale,
    current_effective_locale,
    locale_instruction,
)
from ...runtime.outbound import OutboundPoolName, current_outbound_runtime
from ..network.resolve_query import resolve_network_route_hint
from .routing_observability import (
    ExpertProviderAttemptResult,
    elapsed_ms,
    record_expert_provider_attempt,
)

__all__ = [
    "ExpertCompletion",
    "ExpertProviderError",
    "ExpertProviderTimeoutError",
    "ExpertRoutingDeclinedError",
    "ExpertRoutingOptions",
    "ExpertRoutingContractError",
    "ToolSelection",
    "ToolSelectionError",
    "complete_expert_routing",
    "select_agent_tool",
    "select_expert_tool",
]

_LOGGER = logging.getLogger(__name__)

# Base URLs observed to reject a constrained ``tool_choice``
# (``"required"`` or a named function) with HTTP 400. HTTP Expert
# autonomous routing already sends ``"auto"``; this set only skips a
# wasted 400 when a caller still passes a constrained choice into
# ``complete_expert_routing``. Process-local and non-secret; never
# persisted.
_TOOL_CHOICE_REQUIRED_UNSUPPORTED: set[str] = set()

# Expert routing is a small control-plane request. Two retries are enough to
# absorb a transient gateway failure without turning a route decision into an
# unbounded wait. The jitter prevents concurrent requests from retrying in
# lockstep when the upstream is recovering.
_EXPERT_PROVIDER_MAX_RETRIES = 2
_EXPERT_PROVIDER_BACKOFF_BASE = 1.5

# Tools that accept ``user_query`` (Analyst converts it to
# ``goal_description`` at the HTTP boundary). Structured-input agents
# stay at ``{}`` when the routing model is skipped.
_DETERMINISTIC_USER_QUERY_TOOLS = frozenset(
    {
        "AnalystAgent",
        "BriefGeneAgent",
        "ChatAgent",
        "DataAgent",
        "InSilicoResearchAgent",
        "KnowledgeAgent",
        "ReviewAgent",
    }
)


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


class ExpertRoutingDeclinedError(ToolSelectionError):
    """The routing model answered directly instead of picking a tool.

    A distinct, non-fault outcome of the strict path: the model returned no
    tool call (a content-only or empty-choice completion), which on the real
    endpoint follows the ``required`` -> ``auto`` downgrade and simply means
    "this turn is plain chat". It subclasses ``ToolSelectionError`` so
    callers that do not opt into a chat fallback still treat a decline as a
    contract error. The HTTP Expert boundary degrades an unforced decline to
    ChatAgent when the caller allowed that tool. Genuine violations
    (multiple, malformed, or out-of-allowlist tool calls) keep raising
    ``ToolSelectionError``.
    """


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
    Routing takes one ``OutboundPoolName.LLM`` lease; the dispatched
    agent may take a second lease on the same pool.

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
    Stdio MCP never calls this path. A pinned ``forced_tool`` or a
    one-tool allowlist needs no routing model. Only an unpinned allowlist
    of two or more tools takes an LLM pool lease, and that call uses
    ``tool_choice="auto"``; a later agent turn may take another.
    """
    request = _build_routing_request(
        agent_openai_tool_specs(), options.allowed_tools, options.forced_tool
    )
    deterministic = _deterministic_selection(
        request,
        options.forced_tool,
        user_query,
    )
    if deterministic is not None:
        return deterministic
    if (
        options.forced_tool is None
        and "GeneNetworkAgent" in options.allowed_tools
    ):
        network_hint = resolve_network_route_hint(user_query)
        if network_hint is not None:
            return ToolSelection(
                tool_name="GeneNetworkAgent",
                arguments={
                    "species_code": network_hint.species_code,
                    "to_id": network_hint.to_id,
                },
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

    Holds one ``OutboundPoolName.LLM`` lease for the routing call.
    Provider exception details are intentionally discarded at this boundary;
    the HTTP layer maps the typed outcome to the public safe error envelope.

    HTTP Expert autonomous routing already sends ``tool_choice="auto"``.
    Some OpenAI-compatible endpoints (e.g. the Huawei pangu ``mastudio``
    deployment) still reject a constrained ``tool_choice`` -- both the
    bare ``"required"`` sentinel and a named
    ``{"type": "function", ...}`` choice -- with an HTTP 400 while
    honoring ``"auto"``. The two rejections carry different error bodies
    (a validation message vs. a generic ``PANGU.3342``), so detection
    keys off the constrained choice, not the error text: any 400 on a
    constrained choice records the endpoint and retries once with
    ``"auto"`` over the full tool list. Later constrained calls to a
    recorded endpoint skip straight to ``"auto"``. This path is
    defensive for callers that still pass a constrained choice.
    """
    runtime = current_outbound_runtime()
    sensitive = get_sensitive_config()
    base_url_key = str(getattr(runtime.openai, "base_url", ""))
    constrained = _is_constrained_choice(tool_choice)
    effective_choice = tool_choice
    if constrained and base_url_key in _TOOL_CHOICE_REQUIRED_UNSUPPORTED:
        effective_choice = "auto"

    async def _create(choice: Any) -> Any:
        async with runtime.pools.lease(OutboundPoolName.LLM):
            return await runtime.openai.chat.completions.create(
                model=sensitive.MODEL_ID,
                messages=cast(Any, messages),
                tools=cast(Any, tools),
                tool_choice=choice,
            )

    try:
        return await _create_with_retries(_create, effective_choice)
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
        return await _create_with_retries(create, "auto")
    except APIError as exc:
        raise ExpertProviderError() from exc


async def _create_with_retries(
    create: Callable[[Any], Awaitable[Any]], choice: Any
) -> Any:
    """Run one provider choice with bounded transient-error retries."""
    for attempt in range(_EXPERT_PROVIDER_MAX_RETRIES + 1):
        started_ns = time.monotonic_ns()
        try:
            result = await create(choice)
        except (APITimeoutError, httpx.TimeoutException, TimeoutError) as exc:
            if attempt >= _EXPERT_PROVIDER_MAX_RETRIES:
                _record_provider_attempt(
                    ExpertProviderAttemptResult.TIMEOUT,
                    started_ns,
                    attempt,
                )
                raise ExpertProviderTimeoutError() from exc
            _record_provider_attempt(
                ExpertProviderAttemptResult.TRANSIENT_RETRY,
                started_ns,
                attempt,
            )
        except BadRequestError:
            _record_provider_attempt(
                (
                    ExpertProviderAttemptResult.CONSTRAINED_DOWNGRADE
                    if _is_constrained_choice(choice)
                    else ExpertProviderAttemptResult.PROVIDER_ERROR
                ),
                started_ns,
                attempt,
            )
            raise
        except APIError as exc:
            if not _is_transient_provider_error(exc):
                _record_provider_attempt(
                    ExpertProviderAttemptResult.PROVIDER_ERROR,
                    started_ns,
                    attempt,
                )
                raise
            if attempt >= _EXPERT_PROVIDER_MAX_RETRIES:
                _record_provider_attempt(
                    ExpertProviderAttemptResult.PROVIDER_ERROR,
                    started_ns,
                    attempt,
                )
                raise ExpertProviderError() from exc
            _record_provider_attempt(
                ExpertProviderAttemptResult.TRANSIENT_RETRY,
                started_ns,
                attempt,
            )
        else:
            _record_provider_attempt(
                ExpertProviderAttemptResult.OK, started_ns, attempt
            )
            return result
        await asyncio.sleep(
            _EXPERT_PROVIDER_BACKOFF_BASE**attempt + uniform(0, 1)
        )
    raise AssertionError("expert provider retry loop exited unexpectedly")


def _record_provider_attempt(
    result: ExpertProviderAttemptResult,
    started_ns: int,
    attempt: int,
) -> None:
    """Record one timed Pangu hop without inspecting the exception."""
    record_expert_provider_attempt(
        result,
        duration_ms=elapsed_ms(started_ns),
        attempt=attempt,
    )


def _is_transient_provider_error(exc: APIError) -> bool:
    """Return whether an OpenAI SDK error is safe to retry."""
    if isinstance(exc, APIConnectionError):
        return True
    return isinstance(exc, APIStatusError) and 500 <= exc.status_code < 600


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
    """Prepare the model tool surface and strict-selection contract.

    Unpinned strict routing sends ``tool_choice="auto"``. Production
    Pangu rejects ``"required"``, and a model decline is already a
    Chat fallback rather than a forced pick. A pinned tool still
    records a named choice on the request object, but
    ``select_expert_tool`` never sends it.
    """
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
        else "auto"
    )
    return _RoutingRequest(allowed_order, tools, tool_choice, True)


def _deterministic_selection(
    request: _RoutingRequest,
    forced_tool: str | None,
    user_query: str,
) -> ToolSelection | None:
    """Return a selection that needs no routing model, or ``None``.

    A caller pin (``forced_tool``) or a one-tool allowlist is already
    decided. Only an unpinned allowlist of two or more tools needs the
    routing model.
    """
    if not request.strict:
        return None
    if forced_tool is not None:
        tool_name = forced_tool
    elif len(request.allowed_order) == 1:
        tool_name = request.allowed_order[0]
    else:
        return None
    arguments = (
        {"user_query": user_query}
        if tool_name in _DETERMINISTIC_USER_QUERY_TOOLS
        else {}
    )
    return ToolSelection(tool_name=tool_name, arguments=arguments)


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
            raise ExpertRoutingDeclinedError(
                "routing model returned no choice"
            )
        return None
    message = _field(choices[0], "message")
    tool_calls = _field(message, "tool_calls") or []
    if not tool_calls:
        if request.strict:
            raise ExpertRoutingDeclinedError(
                "routing model returned no tool call"
            )
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
