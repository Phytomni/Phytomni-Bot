# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""IO mapping helpers bridging dispatch payloads to the analyst subgraph.

``map_send_payload_to_analyst_input`` projects the prepared request
mapping ``submit_analyst_analysis`` consumes into ``AnalystInput``
shape, and ``map_analyst_output_to_dispatch_state`` projects the
analyst final state back into the dict ``capture_analysis_result``
reads through ``task_result.get(...)``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..agents.analyst.state import AnalystInput

__all__ = [
    "map_send_payload_to_analyst_input",
    "map_analyst_output_to_dispatch_state",
]


def map_send_payload_to_analyst_input(
    payload: Mapping[str, Any],
) -> AnalystInput:
    """Project a dispatch request payload into ``AnalystInput`` shape.

    Mirrors the kwargs ``submit_analyst_analysis`` forwards to
    ``AnalystAgent.arun``: pulls ``goal_description`` / ``preset_plan``
    / ``data_list`` from the request's ``prompt_parts`` tuple, threads
    ``compute_resource`` and the optional ``output_dir`` through, and
    pins the three dispatch flags to the constants
    ``submit_analyst_analysis`` hard-codes
    (``is_auto_select=False`` / ``is_polling=False`` /
    ``is_preset_plan=True``). ``query`` is set to the empty string
    because the preset-plan path inside the analyst graph never reads
    ``query`` once ``is_preset_plan`` is True.

    Args:
        payload: Request mapping with ``analysis_type`` / ``target_id``
            / ``prompt_parts`` (a 3-tuple of goal description, preset
            plan meta string, and data list dict) / ``compute_resource``
            / optional ``output_dir``.

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
        is_polling=False,
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
