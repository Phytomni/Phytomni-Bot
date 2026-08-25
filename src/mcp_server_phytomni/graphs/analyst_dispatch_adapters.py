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

import importlib
import logging
from collections.abc import Mapping
from typing import Any

from langchain_core.runnables import RunnableConfig

from ..agents.analyst.state import AnalystInput
from ..agents.analyst.task_ops import (
    discard_duplicate_job,
    task_delete,
    verified_reuse_task_ids,
)
from ..agents.shared.analysis import prepare_analyst_dispatch_context
from ..agents.shared.analysis_requests import (
    build_analyst_analysis_request,
    build_analyst_prompt_parts,
)
from ..agents.shared.options import resolve_agent_locale
from ..runtime.fingerprint_jobs import try_attach_reuse_claim
from ..runtime.langgraph_runner import invoke_graph
from ..runtime.request_context import current_request_user, current_run_id
from ..runtime.result_run_layout import result_run_root_from_child
from ..runtime.task_dedup import (
    analyst_task_fingerprint,
    persist_submitted_fingerprint,
    record_dispatch_submission,
    should_reuse_prior_task,
)
from ..runtime.task_manager import TaskManager, resolve_tasks_db_path

logger = logging.getLogger(__name__)

__all__ = [
    "build_analyst_dispatch_request",
    "map_send_payload_to_analyst_input",
    "map_analyst_output_to_dispatch_state",
    "submit_analyst_via_subgraph",
]


def build_analyst_dispatch_request(
    analysis_type: str,
    target_id: str,
    inputs: Mapping[str, Any],
    compute_resource: str,
) -> dict[str, Any]:
    """Build one preset-plan request shared by domain dispatch graphs."""
    prompt_parts = build_analyst_prompt_parts(
        inputs["goal_description"], inputs["meta"], inputs["data_list"]
    )
    return build_analyst_analysis_request(
        analysis_type,
        target_id,
        inputs["output_dir"],
        prompt_parts,
        compute_resource,
    ).to_payload()


def map_send_payload_to_analyst_input(
    payload: Mapping[str, Any],
    *,
    is_polling: bool = False,
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
    True. ``is_polling`` is parameterised and defaults to submit-return so
    the shared execution runtime owns remote waiting. Legacy callers that
    still require in-graph polling opt in explicitly.

    Args:
        payload: Request mapping with ``analysis_type`` / ``target_id``
            / ``prompt_parts`` (a 3-tuple of goal description, preset
            plan meta string, and data list dict) / ``compute_resource``
            / optional ``output_dir``.
        is_polling: Whether the analyst graph should block until the
            submitted task reaches a terminal state. Defaults to ``False``
            so the caller-owned execution runtime performs remote polling.

    Returns:
        An ``AnalystInput`` dict suitable for ``ainvoke`` on the
        compiled analyst subgraph.

    Raises:
        KeyError: When the payload is missing ``prompt_parts`` or
            ``compute_resource``; the surrounding dispatch flow treats
            either omission as a programming error.
    """
    goal_description, preset_plan_meta, data_list = payload["prompt_parts"]
    analyst_input = AnalystInput(
        query="",
        goal_description=goal_description,
        preset_plan=preset_plan_meta,
        data_list=data_list,
        compute_resource=payload["compute_resource"],
        output_dir=payload.get("output_dir") or "",
        output_dir_is_result_child=(
            payload.get("output_dir_is_result_child") is True
        ),
        locale=resolve_agent_locale(payload.get("locale")),
        is_polling=is_polling,
        is_auto_select=False,
        is_preset_plan=True,
    )
    raw_documents = payload.get("obs_file_list")
    if raw_documents is not None:
        if not isinstance(raw_documents, (list, tuple)) or any(
            not isinstance(item, str) or not item.strip()
            for item in raw_documents
        ):
            raise ValueError("obs_file_list must contain non-empty strings")
        analyst_input["obs_file_list"] = list(raw_documents)
    explicit_fingerprint = payload.get("dispatch_fingerprint")
    if explicit_fingerprint is not None:
        analyst_input["dispatch_fingerprint"] = explicit_fingerprint
        analyst_input["input_fingerprint"] = explicit_fingerprint
    sidecar = payload.get("research_grant_sidecar")
    if sidecar is not None:
        analyst_input["research_grant_sidecar"] = sidecar
    return analyst_input


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


def _stamp_analysis_type(
    result: dict[str, Any], analysis_type: str
) -> dict[str, Any]:
    """Copy the producer analysis_type onto one Analyst submit dict."""
    if not analysis_type or result.get("analysis_type"):
        return result
    return {**result, "analysis_type": analysis_type}


async def submit_analyst_via_subgraph(
    analyst_agent: Any,
    config: Any,
    sensitive_config: Any,
    request: Mapping[str, Any],
    *,
    is_polling: bool = False,
) -> dict[str, Any]:
    """Dispatch one analysis through the analyst's compiled subgraph.

    Mirrors ``submit_analyst_analysis`` (same ``request`` shape, same
    ``RunIdentity`` / output-dir / thread-id preparation via the
    shared ``prepare_analyst_dispatch_context`` helper) but invokes
    the analyst through ``analyst_agent.app.ainvoke(AnalystInput, ...)``
    rather than ``analyst_agent.arun(...)``. The dispatcher still
    owns the ``analyst_agent`` instance built with its own per-call
    config, so USER_ID, APP_ID, retry, and storage behavior remain
    consistent with the historical direct-``arun`` path. Every
    dispatcher subclass of ``AnalystConfig`` submits analyst work
    through this helper.

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
            submitted task reaches a terminal state. Defaults to ``False``
            so the caller-owned execution runtime performs remote polling.

    Returns:
        Dict containing ``task_id`` / ``output_dir`` / ``plan`` /
        ``tool_usages`` / ``task_status`` projected by
        ``map_analyst_output_to_dispatch_state`` from the analyst
        subgraph's final state. Missing fields surface as ``None`` so
        failure paths flow through to ``capture_analysis_result``
        without raising. On an input-fingerprint dedup hit the same
        shape is returned (``plan`` / ``tool_usages`` ``None``) but the
        ``task_id`` is a fresh caller-owned id and ``source_task_id``
        carries the prior tenant's remote id; the seam persists a
        caller-owned task row so the reuse caller polls a row they own.
    """
    output_dir_is_result_child, fingerprint = _dispatch_identity(request)
    del sensitive_config
    context = await prepare_analyst_dispatch_context(
        config, request, fingerprint
    )
    rejected_source_id = None
    if fingerprint is not None:
        reused, rejected_source_id = await _reuse_prior_dispatch(
            fingerprint, require_terminal_success=is_polling
        )
        if reused is not None:
            reused = _normalize_reused_submission(reused)
            logger.info(
                "Reusing prior %s task via fingerprint dedup "
                "(caller task_id: %s, source_task_id: %s)",
                context.analysis_type,
                reused["task_id"],
                reused["source_task_id"],
            )
            record_dispatch_submission(
                reused["task_id"],
                reused["output_dir"],
                fingerprint,
                source_task_id=reused["source_task_id"],
            )
            return _stamp_analysis_type(reused, context.analysis_type)
    enriched_request = {
        **request,
        "output_dir": context.output_dir,
        "output_dir_is_result_child": output_dir_is_result_child,
    }
    analyst_input = map_send_payload_to_analyst_input(
        enriched_request, is_polling=is_polling
    )
    runnable_config: RunnableConfig = {
        "configurable": {"thread_id": context.thread_id}
    }
    logger.info(
        "Submitting %s task via analyst subgraph", context.analysis_type
    )
    final_state = await invoke_graph(
        analyst_agent.app, analyst_input, config=runnable_config
    )
    result = _stamp_analysis_type(
        map_analyst_output_to_dispatch_state(final_state),
        context.analysis_type,
    )
    await _persist_subgraph_fingerprint(
        result, fingerprint, is_polling, rejected_source_id
    )
    logger.info(
        "%s task completed via subgraph (task_id: %s)",
        context.analysis_type,
        result.get("task_id"),
    )
    return result


def _dispatch_fingerprint(request: Mapping[str, Any]) -> str:
    """Return the dedup fingerprint for a dispatch request.

    The digest includes the semantic goal and data channels. Document
    context is carried by the explicit Research dispatch fingerprint, so
    the same formula and namespace lets a gene analysis reuse the analyst
    top-level dedup without dropping document identity.

    Args:
        request: Dispatch request with a ``prompt_parts`` 3-tuple of
            goal description, preset plan meta string, and data list.

    Returns:
        The ``analyst_task_fingerprint`` digest for the request.
    """
    goal_description, _meta, data_list = request["prompt_parts"]
    return analyst_task_fingerprint(
        goal_description=goal_description,
        data_list=data_list,
        obs_file_list=None,
    )


def _dispatch_identity(
    request: Mapping[str, Any],
) -> tuple[bool, str | None]:
    """Return the child-layout flag and any explicit dispatch identity."""
    output_dir_is_result_child = (
        request.get("output_dir_is_result_child") is True
    )
    if output_dir_is_result_child:
        result_run_root_from_child(str(request.get("output_dir") or ""))
    explicit_fingerprint = request.get("dispatch_fingerprint")
    if explicit_fingerprint is not None and (
        not isinstance(explicit_fingerprint, str)
        or not explicit_fingerprint.strip()
    ):
        raise ValueError("dispatch_fingerprint must be a non-blank string")
    if explicit_fingerprint is not None:
        return output_dir_is_result_child, explicit_fingerprint
    if output_dir_is_result_child:
        return output_dir_is_result_child, None
    return output_dir_is_result_child, _dispatch_fingerprint(request)


def _normalize_reused_submission(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a reuse result while retaining its adapter mapping shape."""
    # ``deep_genome`` exports this adapter and imports it during package
    # initialization, so importing the coordinator at module load time would
    # create a cycle.  The helper runs only after initialization is complete.
    coordinator = importlib.import_module(
        "mcp_server_phytomni.agents.deep_genome.coordinator"
    )
    normalized = coordinator.normalize_submission(payload)
    return {
        **payload,
        "task_id": normalized.submitted_task_id,
        "source_task_id": normalized.poll_task_id,
        "output_dir": normalized.output_dir,
    }


async def _reuse_prior_dispatch(
    fingerprint: str,
    *,
    require_terminal_success: bool,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return a reuse result and the source excluded if fresh work is needed.

    Reads the fingerprint row, applies the cheap status gate, then the
    live verification (probe + is_polling-aware decision). The reuse
    dict mirrors ``map_analyst_output_to_dispatch_state``'s 5-key shape
    (``plan`` / ``tool_usages`` are ``None`` for a reused task) plus a
    ``source_task_id`` pointing at the prior tenant's remote id. The
    surfaced ``task_id`` is a fresh caller-owned id (never the prior
    tenant's), so a reuse caller polls a row they own while the prior
    remote task remains the live-status source.

    Args:
        fingerprint: Identity digest from ``analyst_task_fingerprint``.
        require_terminal_success: True for a polling caller
            (deep_genome), which may only reuse a terminal-success task.

    Returns:
        The optional reuse dict and the exact remote source considered.
    """
    db_path = resolve_tasks_db_path()
    prior = TaskManager(db_path).get_task_by_fingerprint(fingerprint)
    if prior is None:
        return None, None
    source_id = str(prior.get("source_task_id") or prior["task_id"])
    if not should_reuse_prior_task(prior["status"] or ""):
        return None, source_id
    reuse_ids = await verified_reuse_task_ids(
        prior,
        require_terminal_success=require_terminal_success,
    )
    claim = try_attach_reuse_claim(
        db_path,
        fingerprint=fingerprint,
        prior=prior,
        reuse_ids=reuse_ids,
        identity=(
            _claim_run_id(reuse_ids[0] if reuse_ids else ""),
            _claim_user_id(),
        ),
    )
    if claim is None:
        return None, source_id
    caller_task_id, source_task_id = claim
    return {
        "task_id": caller_task_id,
        "output_dir": prior["output_dir"],
        "plan": None,
        "tool_usages": None,
        "task_status": prior["status"],
        "source_task_id": source_task_id,
    }, source_id


def _claim_run_id(claimant_task_id: str) -> str:
    """Bind a claim to the request run, or the caller-owned task id."""
    return current_run_id() or claimant_task_id


def _claim_user_id() -> str:
    """Bind a claim to the request user, or the anonymous MCP owner."""
    return current_request_user() or "anonymous"


async def _persist_subgraph_fingerprint(
    result: dict[str, Any],
    fingerprint: str | None,
    force_new: bool,
    rejected_source_id: str | None,
) -> None:
    """Record a new fingerprint job when the subgraph minted a task id."""
    orphan = persist_submitted_fingerprint(
        result,
        fingerprint,
        force_new=force_new,
        rejected_ei_task_id=rejected_source_id,
    )
    await discard_duplicate_job(orphan, task_delete)
