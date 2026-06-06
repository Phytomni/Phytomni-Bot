# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""IO mapping + dispatch helpers bridging payloads to the analyst subgraph.

``map_send_payload_to_analyst_input`` projects the dispatch request
into ``AnalystInput`` shape; ``map_analyst_output_to_dispatch_state``
projects the analyst final state into the dict
``capture_analysis_result`` consumes; ``submit_analyst_via_subgraph``
composes both around ``analyst_agent.app.ainvoke`` so dispatchers
can opt into subgraph composition without bypassing OBS layout.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from langchain_core.runnables import RunnableConfig

from ..agents.analyst.state import AnalystInput
from ..agents.shared.analysis import prepare_analyst_dispatch_context

logger = logging.getLogger(__name__)

__all__ = [
    "map_send_payload_to_analyst_input",
    "map_analyst_output_to_dispatch_state",
    "submit_analyst_via_subgraph",
]


def map_send_payload_to_analyst_input(
    payload: Mapping[str, Any],
    *,
    is_polling: bool = True,
) -> AnalystInput:
    """Project a dispatch request payload into ``AnalystInput`` shape.

    Mirrors the kwargs ``submit_analyst_analysis`` forwards to
    ``AnalystAgent.arun``: pulls ``goal_description`` / ``preset_plan``
    / ``data_list`` from the request's ``prompt_parts`` tuple, threads
    ``compute_resource`` and the optional ``output_dir`` through, and
    pins ``is_auto_select=False`` / ``is_preset_plan=True`` (the
    constants ``submit_analyst_analysis`` hard-codes). ``query`` is
    set to the empty string because the preset-plan path inside the
    analyst graph never reads ``query`` once ``is_preset_plan`` is
    True. ``is_polling`` is parameterised: design / network / research
    / environment / evolution (the five current dispatch consumers)
    all submit fire-and-poll-elsewhere style so they pass
    ``is_polling=False``; the default ``True`` is forward-looking for
    deep_genome which awaits a polling-terminal task state inline.

    Args:
        payload: Request mapping with ``analysis_type`` / ``target_id``
            / ``prompt_parts`` (a 3-tuple of goal description, preset
            plan meta string, and data list dict) / ``compute_resource``
            / optional ``output_dir``.
        is_polling: Whether the analyst graph should block until the
            submitted task reaches a terminal state. Defaults to
            ``True`` (deep_genome semantics); existing dispatch
            consumers pass ``False`` to match their current
            ``submit_analyst_analysis`` / ``analyst.submit`` paths.

    Returns:
        An ``AnalystInput`` dict suitable for ``ainvoke`` on the
        compiled analyst subgraph.

    Raises:
        KeyError: When the payload is missing ``prompt_parts`` or
            ``compute_resource``; the surrounding dispatch flow treats
            either omission as a programming error.
    """
    goal_description, preset_plan_meta, data_list = payload["prompt_parts"]
    return AnalystInput(
        query="",
        goal_description=goal_description,
        preset_plan=preset_plan_meta,
        data_list=data_list,
        compute_resource=payload["compute_resource"],
        output_dir=payload.get("output_dir") or "",
        is_polling=is_polling,
        is_auto_select=False,
        is_preset_plan=True,
    )


def map_analyst_output_to_dispatch_state(
    final_state: Mapping[str, Any],
) -> dict[str, Any]:
    """Project ``AnalystOutput`` into the dispatch state-update shape.

    Mirrors how ``submit_analyst_analysis``'s return is read by
    ``capture_analysis_result``: callers pull ``task_id`` for the
    per-analysis task-id register and ``output_dir`` / ``plan`` /
    ``tool_usages`` / ``task_status`` for downstream consumers and
    the HTTP/MCP ``raw.phytomni_state`` envelope. Returns a plain
    dict so downstream code can spread it as ``**adapter_output``
    without re-validating against the ``AnalystOutput`` TypedDict at
    runtime.

    Args:
        final_state: ``AnalystOutput``-shaped dict produced by the
            compiled analyst subgraph.

    Returns:
        Dict containing ``task_id`` / ``output_dir`` / ``plan`` /
        ``tool_usages`` / ``task_status`` with missing keys surfacing
        as ``None`` so failure paths flow through without raising.
    """
    return {
        "task_id": final_state.get("task_id"),
        "output_dir": final_state.get("output_dir"),
        "plan": final_state.get("plan"),
        "tool_usages": final_state.get("tool_usages"),
        "task_status": final_state.get("task_status"),
    }


async def submit_analyst_via_subgraph(
    analyst_agent: Any,
    config: Any,
    sensitive_config: Any,
    request: Mapping[str, Any],
    *,
    is_polling: bool = True,
) -> dict[str, Any]:
    """Dispatch one analysis through the analyst's compiled subgraph.

    Mirrors ``submit_analyst_analysis`` (same ``request`` shape, same
    ``RunIdentity`` / output-dir / thread-id preparation via the
    shared ``prepare_analyst_dispatch_context`` helper) but invokes
    the analyst through ``analyst_agent.app.ainvoke(AnalystInput, ...)``
    rather than ``analyst_agent.arun(...)``. The dispatcher still
    owns the ``analyst_agent`` instance built with its own per-call
    config, so USER_ID, APP_ID, retry, and storage behavior remain
    consistent with the legacy path. The two helpers therefore form
    the two routes the ``USE_ANALYST_SUBGRAPH`` flag selects between
    on every dispatcher subclass of ``AnalystConfig``.

    Args:
        analyst_agent: ``AnalystAgent``-compatible instance owning a
            compiled ``app`` exposing the three-schema ``ainvoke``
            entry point.
        config: Public dispatcher config object with at least
            ``USER_ID`` and the OBS output-dir attributes that
            ``ensure_analysis_output_dir`` requires.
        sensitive_config: Sensitive config object used by
            ``ensure_analysis_output_dir`` for OBS credentials.
        request: Prepared request mapping with ``analysis_type`` /
            ``target_id`` / ``prompt_parts`` (a 3-tuple of goal
            description, preset plan meta string, and data list
            dict) / ``compute_resource`` / optional ``output_dir``.
        is_polling: Whether the analyst graph should block until the
            submitted task reaches a terminal state. Defaults to
            ``True`` (deep_genome semantics); design / network /
            research / environment / evolution all pass ``False`` to
            match their current ``submit_analyst_analysis`` /
            ``analyst.submit`` fire-and-poll-elsewhere paths.

    Returns:
        Dict containing ``task_id`` / ``output_dir`` / ``plan`` /
        ``tool_usages`` / ``task_status`` projected by
        ``map_analyst_output_to_dispatch_state`` from the analyst
        subgraph's final state. Missing fields surface as ``None`` so
        failure paths flow through to ``capture_analysis_result``
        without raising.
    """
    context = prepare_analyst_dispatch_context(
        config, sensitive_config, request
    )
    enriched_request = {**request, "output_dir": context.output_dir}
    analyst_input = map_send_payload_to_analyst_input(
        enriched_request, is_polling=is_polling
    )
    runnable_config: RunnableConfig = {
        "configurable": {"thread_id": context.thread_id}
    }
    logger.info(
        "Submitting %s task via analyst subgraph", context.analysis_type
    )
    final_state = await analyst_agent.app.ainvoke(
        analyst_input, config=runnable_config
    )
    result = map_analyst_output_to_dispatch_state(final_state)
    logger.info(
        "%s task completed via subgraph (task_id: %s)",
        context.analysis_type,
        result.get("task_id"),
    )
    return result
