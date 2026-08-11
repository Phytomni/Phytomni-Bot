# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared helpers for Analyst-backed LangGraph task workflows.

Exports cache specs, routing helpers, output-dir builders, dispatch capture
helpers, config-copy utilities, and graph invocation wrappers used by
workflow agents that submit tasks through AnalystAgent.
"""

import hashlib
import logging
import traceback
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, NamedTuple

from langgraph.types import Send

from ...config.overrides import (
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from ...runtime.agent_registry import (
    agent_fingerprint_values,
    get_cached_agent,
)
from ...runtime.error_types import RemoteAnalysisSubmissionError
from ...runtime.langgraph_runner import (
    ainvoke_graph,
    capture_workflow_boundary,
)
from ...runtime.request_context import bind_accepted_task_ids
from ...runtime.result_run_layout import (
    result_child_output_dir,
    result_run_root_from_child,
)
from ...runtime.submission_outcome import SubmissionOutcome
from ...storage.path_policy import RunIdentity
from ..analyst.agent import (
    ANALYST_SECRET_FIELD_MAP,
    ANALYST_SENSITIVE_FIELD_MAP,
)
from .analysis_storage import create_output_dir
from .intermediate_state import merge_intermediate_state
from .parallel_dispatch import FailureRecord, redact_failure_message

logger = logging.getLogger(__name__)


def _compute_traceback_digest(exc: BaseException) -> str | None:
    """Compute a stable 16-char SHA256 digest of the exception traceback.

    Used to populate FailureRecord.traceback_digest. The digest stays in
    raw.phytomni_state only — never reaches formatted.metadata — so it
    cannot leak credentials or internal file paths to clients while
    still letting ops correlate identical failure stacks across runs.

    Returns None when traceback formatting itself raises (defensive — a
    digest is best-effort metadata, not load-bearing).
    """
    try:
        tb_str = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        return hashlib.sha256(tb_str.encode("utf-8")).hexdigest()[:16]
    except (TypeError, AttributeError, ValueError, RecursionError):
        return None


__all__ = [
    "AnalysisAgentCacheSpec",
    "AnalysisCaptureSpec",
    "AnalysisStateSpec",
    "base_analysis_state",
    "capture_analysis_result",
    "capture_dispatched_analysis",
    "copy_analyst_sensitive_config",
    "copy_user_analysis_config",
    "ensure_analysis_output_dir",
    "get_cached_analysis_agent",
    "get_configured_analysis_agent",
    "invoke_analysis_agent",
    "route_analysis_tasks",
    "run_analysis_graph",
    "AnalystDispatchContext",
    "prepare_analyst_dispatch_context",
    "submit_analyst_analysis",
    "finalize_analysis_submission",
]


@dataclass(frozen=True)
class AnalysisCaptureSpec:
    """State-output and exception policy for one captured dispatch."""

    result_key: str | None = None
    result_list_key: str | None = None
    captured_exceptions: tuple[type[Exception], ...] | None = None


def finalize_analysis_submission(
    result: Mapping[str, Any],
    outcome: SubmissionOutcome,
    *,
    pending: bool = False,
) -> dict[str, Any]:
    """Project a typed Analyst outcome onto the public agent result."""
    if outcome.kind == "rejected" or pending:
        raise RemoteAnalysisSubmissionError("no remote task was accepted")
    bind_accepted_task_ids(outcome.task_ids)
    return {
        **result,
        "task_ids": list(outcome.task_ids),
        "submission_warnings": list(outcome.warnings),
    }


class AnalystDispatchContext(NamedTuple):
    """Resolved dispatch parameters shared by every analyst-bound caller.

    ``submit_analyst_analysis`` (the legacy direct-``arun`` path) and
    ``submit_analyst_via_subgraph`` (the opt-in subgraph entry point)
    both need the same ``output_dir`` and ``thread_id`` derived from
    the dispatch ``request``. Bundling them in a NamedTuple keeps the
    prep logic in one place and prevents the four-line preamble from
    drifting between the two dispatch helpers.

    Attributes:
        analysis_type: ``request["analysis_type"]`` cast to ``str`` for
            downstream logging and label use.
        target_id: The dispatch target identifier from
            ``request["target_id"]``, cast to ``str``.
        output_dir: The resolved OBS / local output directory.
        thread_id: The per-task LangGraph thread id derived from the
            run identity and the target / analysis-type pair.
    """

    analysis_type: str
    target_id: str
    output_dir: str
    thread_id: str


async def prepare_analyst_dispatch_context(
    config: Any,
    request: Mapping[str, Any],
    fingerprint: str | None = None,
) -> AnalystDispatchContext:
    """Resolve the dispatch context shared by both analyst entry points.

    Mints a ``RunIdentity`` from the caller's ``USER_ID``, ensures the
    output directory exists (creating an OBS prefix when configured),
    and derives a LangGraph ``thread_id`` scoped to the dispatched
    target. The same context flows into ``submit_analyst_analysis``
    (legacy ``arun``) and ``submit_analyst_via_subgraph`` (compiled
    subgraph entry) so both paths share identical OBS layout and
    checkpoint behaviour.

    Args:
        config: Public config object with at least ``USER_ID``.
        request: Prepared request mapping with ``analysis_type`` /
            ``target_id`` / optional ``output_dir``.
        fingerprint: Optional input-identity digest forwarded to
            ``ensure_analysis_output_dir`` so the output directory
            routes to the tenant-neutral shared key rather than the
            per-run user-scoped path.

    Returns:
        An ``AnalystDispatchContext`` capturing the resolved labels,
        output directory, and thread id.
    """
    analysis_type = str(request["analysis_type"])
    target_id = str(request["target_id"])
    run_identity = RunIdentity.create(
        user_id=config.USER_ID,
        scope=analysis_type,
    )
    if request.get("output_dir_is_result_child") is True:
        output_dir = str(request["output_dir"])
        result_run_root_from_child(output_dir)
    else:
        output_dir = result_child_output_dir(
            await ensure_analysis_output_dir(
                config,
                analysis_type,
                request.get("output_dir"),
                run_identity,
                fingerprint=fingerprint,
            ),
            0,
        )
    thread_id = run_identity.scoped_id("thread", target_id, analysis_type)
    return AnalystDispatchContext(
        analysis_type=analysis_type,
        target_id=target_id,
        output_dir=output_dir,
        thread_id=thread_id,
    )


@dataclass(frozen=True)
class AnalysisStateSpec:
    """State initialization spec for ``run_analysis_graph``.

    Attributes:
        tasks_key: State key that will hold prepared task payloads.
        result_inits: Optional mapping of additional state keys to
            their initial values (e.g. ``{"design_task_result": []}``).
    """

    tasks_key: str
    result_inits: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class AnalysisAgentCacheSpec:
    """Inputs needed to resolve a cached Analyst-backed agent.

    Attributes:
        agent_name: Registry key used for the cached agent instance.
        config_name: Fingerprint label for the copied public config.
        base_config: Source config object used before wrapper overrides.
        field_map: Mapping from wrapper keyword names to config fields.
        user_id: Optional user id stored in the copied config.
    """

    agent_name: str
    config_name: str
    base_config: Any
    field_map: Mapping[str, str]
    user_id: str | None


def route_analysis_tasks(
    node_name: str,
    target_key: str,
    tasks_key: str,
    state: Mapping[str, Any],
) -> list[Send]:
    """Dispatch analysis tasks in parallel using LangGraph Send.

    Args:
        node_name: Graph node name that receives each task payload.
        target_key: State key holding the target identifier to copy.
        tasks_key: State key containing prepared task mappings.
        state: Current workflow state with species_code, target, and tasks.

    Returns:
        LangGraph Send commands that fan tasks out to ``node_name``.
    """
    return [
        Send(
            node_name,
            {
                "task_index": index,
                "species_code": state["species_code"],
                target_key: state[target_key],
                "output_dir": state.get("output_dir"),
                "locale": state.get("locale"),
                **task,
            },
        )
        for index, task in enumerate(state.get(tasks_key, []))
    ]


async def ensure_analysis_output_dir(
    config: Any,
    analysis_type: str,
    output_dir: str | None,
    run_identity: RunIdentity | None = None,
    **kwargs: Any,
) -> str:
    """Return an existing or newly created analysis output directory.

    A content-addressed ``fingerprint`` always wins over a preset
    ``output_dir``: the shared key is the dedup identity, so honouring a
    caller's user-scoped preset here would silently bypass cross-tenant
    dedup (and, for the deep_genome evolution mount, scope results to the
    module-default ``anonymous`` user). A preset ``output_dir``
    short-circuits creation only when no fingerprint is supplied.

    Args:
        config: Public config object with user, OBS server, and bucket fields.
        analysis_type: Analysis workflow name used in generated paths.
        output_dir: Existing output directory to reuse when no fingerprint
            is supplied; ignored when a fingerprint routes to the shared key.
        run_identity: Optional run identity for deterministic path building.
        **kwargs: Optional ``fingerprint``; when set it overrides
            ``output_dir`` and routes the created dir to the tenant-neutral
            shared content-addressed key.

    Returns:
        The preset ``output_dir`` (no fingerprint), else the newly created
        OBS output directory (the tenant-neutral shared key when a
        fingerprint is supplied, otherwise a per-run user-scoped path).
    """
    fingerprint = kwargs.get("fingerprint")
    if output_dir and not fingerprint:
        return output_dir
    identity = run_identity or RunIdentity.create(
        user_id=config.USER_ID,
        scope=analysis_type,
    )
    return await create_output_dir(
        user_id=identity.user_id,
        task=f"{analysis_type}_task",
        bucket_name=config.BUCKET_NAME,
        run_identity=identity,
        fingerprint=fingerprint,
    )


async def submit_analyst_analysis(
    analyst_agent: Any,
    config: Any,
    request: Mapping[str, Any],
    *,
    is_polling: bool = False,
) -> dict[str, Any]:
    """Submit one prepared analysis task through AnalystAgent.

    Args:
        analyst_agent: Configured AnalystAgent-compatible instance.
        config: Public config object used for user and OBS settings.
        request: Prepared request mapping containing analysis metadata,
            prompt parts, compute resource, target id, and optional output dir.
        is_polling: Whether the analyst should block until the submitted task
            reaches a terminal state. Defaults to ``False`` to preserve the
            fire-and-poll-elsewhere semantics of the design / network /
            research / environment / evolution consumers; deep_genome's
            dispatch passes ``True`` to mirror its historical polling
            behavior at ``dispatch.py:_submit_analysis_task``.

    Returns:
        AnalystAgent result payload, including task id and output directory
        when task submission succeeds.
    """
    context = await prepare_analyst_dispatch_context(config, request)
    goal_description, meta, data_list = request["prompt_parts"]
    logger.info("Submitting %s task via AnalystAgent", context.analysis_type)
    result = await analyst_agent.arun(
        query=None,
        goal_description=goal_description,
        preset_data_list=data_list,
        preset_plan=meta,
        output_dir=context.output_dir,
        compute_resource=request["compute_resource"],
        is_auto_select=False,
        is_polling=is_polling,
        is_preset_plan=True,
        thread_id=context.thread_id,
    )
    logger.info(
        "%s task completed (task_id: %s)",
        context.analysis_type,
        result.get("task_id"),
    )
    return result


async def capture_analysis_result(
    state: Mapping[str, Any],
    analysis_type: str,
    submit_call: Callable[[], Awaitable[dict[str, Any]]],
    spec: AnalysisCaptureSpec | None = None,
) -> dict[str, Any]:
    """Capture one dispatched analysis result as LangGraph state updates.

    Args:
        state: Current LangGraph state used to preserve partial progress.
        analysis_type: Analysis type label used for task id storage. The
            ``_analysis`` suffix (when present) is stripped before keying
            into ``task_ids``, so callers can pass either a strict
            analysis type or an opaque task name.
        submit_call: Awaitable callback that submits or dispatches the task.
        spec: Optional output and exception policy. ``result_key`` stores
            one result, ``result_list_key`` accumulates results, and
            ``captured_exceptions`` controls the failure boundary. ``None``
            retains the legacy broad workflow boundary; an empty tuple makes
            the dispatch fail loudly.

    Returns:
        State updates containing task ids, completion count, result payloads,
        or an error message when dispatch fails.
    """

    spec = spec or AnalysisCaptureSpec()

    async def run_task() -> dict[str, Any]:
        """Run the task and merge task id/result updates.

        Returns:
            State updates containing merged task ids and result payloads.
        """
        task_result = await submit_call()
        existing_task_ids = dict(state.get("task_ids", {}))
        task_id = task_result.get("task_id")
        if task_id is not None:
            existing_task_ids[analysis_type.replace("_analysis", "")] = str(
                task_id
            )
        updates: dict[str, Any] = {
            "task_ids": existing_task_ids,
            "completed_count": 1,
        }
        if spec.result_list_key is not None:
            task_results = list(state.get(spec.result_list_key, []))
            task_results.append(task_result)
            updates[spec.result_list_key] = task_results
        elif spec.result_key is not None:
            updates[spec.result_key] = task_result
        return updates

    def failure_state(exc: Exception) -> dict[str, Any]:
        """Preserve partial task progress when dispatch fails.

        Args:
            exc: Exception raised by the submit callback.

        Returns:
            State updates that record the error and completed dispatch count.
            Writes both the legacy ``error`` field and the new ``failures``
            list so existing readers and new FailureRecord readers both see
            consistent information. The message is redacted at creation so
            no backend URL / token / credential rides the ``error`` field
            or the FailureRecord into ``raw.phytomni_state`` under debug.
        """
        msg = redact_failure_message(str(exc))
        task_label = analysis_type or f"task:{state.get('task_index', '?')}"
        return {
            "task_ids": state.get("task_ids", {}),
            "completed_count": 1,
            "error": msg,
            "failures": [
                FailureRecord(
                    task_label=task_label,
                    message=msg,
                    kind="execute",
                    traceback_digest=_compute_traceback_digest(exc),
                )
            ],
        }

    if spec.captured_exceptions is None:
        return await capture_workflow_boundary(run_task, failure_state)
    if not spec.captured_exceptions:
        return await run_task()
    try:
        return await run_task()
    except spec.captured_exceptions as exc:
        return failure_state(exc)


async def capture_dispatched_analysis(
    state: Mapping[str, Any],
    analysis_type: str,
    target_key: str,
    dispatch_call: Callable[
        [str, str, str, str | None], Awaitable[dict[str, Any]]
    ],
    spec: AnalysisCaptureSpec | None = None,
) -> dict[str, Any]:
    """Capture an analysis dispatched by target-key based state.

    Args:
        state: Current LangGraph state containing species_code, target, and
            output.
        analysis_type: Analysis type label passed to the dispatch callback.
        target_key: State key holding the analysis target identifier.
        dispatch_call: Callback that dispatches one target-specific analysis.
        spec: Optional output and exception policy passed to
            ``capture_analysis_result``.

    Returns:
        State updates produced by ``capture_analysis_result``.
    """
    return await capture_analysis_result(
        state,
        analysis_type,
        lambda: dispatch_call(
            analysis_type,
            state["species_code"],
            state[target_key],
            state.get("output_dir"),
        ),
        spec,
    )


def base_analysis_state(
    base_state: Mapping[str, Any],
    tasks_key: str,
    kwargs: Mapping[str, Any],
    result_inits: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the common initial state for Analyst-backed workflows.

    Args:
        base_state: Workflow-specific initial state values.
        tasks_key: State key that will hold prepared task payloads.
        kwargs: Public wrapper keyword arguments.
        result_inits: Optional mapping of additional state keys to their
            initial values (e.g. ``{"design_task_result": []}`` for
            list-valued accumulators or ``{"network_task": {}}`` for
            dict-valued result slots). Pass ``None`` when the workflow
            does not pre-initialize result storage.

    Returns:
        Initial graph state with shared task bookkeeping fields.
    """
    state: dict[str, Any] = {
        **base_state,
        "user_id": kwargs.get("user_id"),
        "batch": kwargs.get("batch", False),
        "output_dir": kwargs.get("output_dir"),
        tasks_key: [],
        "task_ids": {},
        "completed_count": 0,
        "error": None,
    }
    if result_inits:
        state.update(result_inits)
    return state


async def invoke_analysis_agent(
    app: Any,
    initial_state: Mapping[str, Any],
    thread_id: str | None,
    result_keys: tuple[str, ...],
) -> dict[str, Any]:
    """Invoke an analysis graph and return surfaced + intermediate state.

    Args:
        app: Compiled LangGraph application.
        initial_state: Initial state passed to the graph.
        thread_id: Optional checkpoint thread id.
        result_keys: Result fields surfaced at the top level (mirrors
            the historic trim allow-list). Everything else in the
            final state is nested under ``phytomni_state`` so the
            HTTP/MCP raw envelope can expose LangGraph intermediates
            like plan, tool_usages, and method_context.

    Returns:
        Mapping with the requested ``result_keys`` at the top level
        plus a ``phytomni_state`` entry carrying the remaining final
        state fields.
    """
    result = await ainvoke_graph(
        app,
        initial_state,
        thread_id=thread_id,
    )
    return merge_intermediate_state(result, surface_keys=result_keys)


def copy_analyst_sensitive_config(
    base_config: Any,
    kwargs: Mapping[str, Any],
) -> Any:
    """Return sensitive config overrides shared by Analyst-backed agents.

    Args:
        base_config: Base SensitiveConfig-like object.
        kwargs: Wrapper keyword overrides.

    Returns:
        Copied sensitive config with Analyst field overrides applied.
    """
    return copy_sensitive_config_with_overrides(
        base_config,
        kwargs,
        field_map=ANALYST_SENSITIVE_FIELD_MAP,
        secret_field_map=ANALYST_SECRET_FIELD_MAP,
    )


def copy_user_analysis_config(
    base_config: Any,
    kwargs: Mapping[str, Any],
    field_map: Mapping[str, str],
    user_id: str | None,
) -> Any:
    """Return analysis config overrides plus the optional user id.

    Args:
        base_config: Base public config object.
        kwargs: Wrapper keyword overrides.
        field_map: Mapping from wrapper keyword names to config fields.
        user_id: Optional user id fixed into the copied config.

    Returns:
        Copied public config with wrapper overrides applied.
    """
    return copy_config_with_overrides(
        base_config,
        kwargs,
        field_map,
        fixed_updates={"USER_ID": user_id},
    )


async def run_analysis_graph(
    app: Any,
    base_state: Mapping[str, Any],
    kwargs: Mapping[str, Any],
    result_keys: tuple[str, ...],
    state_spec: AnalysisStateSpec,
) -> dict[str, Any]:
    """Build initial state, invoke the graph, and return selected fields.

    Args:
        app: Compiled LangGraph application.
        base_state: Workflow-specific initial state values.
        kwargs: Public wrapper keyword arguments.
        result_keys: Final-state keys to return to the caller.
        state_spec: Tasks-key plus optional ``result_inits`` mapping
            forwarded to ``base_analysis_state``.

    Returns:
        Mapping of selected final-state values.
    """
    initial_state = base_analysis_state(
        base_state,
        state_spec.tasks_key,
        kwargs,
        result_inits=state_spec.result_inits,
    )
    return await invoke_analysis_agent(
        app,
        initial_state,
        kwargs.get("thread_id"),
        result_keys,
    )


def get_cached_analysis_agent(
    agent_name: str,
    factory: Callable[[], Any],
    config_name: str,
    config: Any,
    sensitive_config: Any,
) -> Any:
    """Return a cached Analyst-backed agent with a stable fingerprint.

    Args:
        agent_name: Registry key used for caching.
        factory: Callable that creates the agent when cache misses.
        config_name: Fingerprint label for the public config object.
        config: Public config object used in the cache fingerprint.
        sensitive_config: Sensitive config object used in the fingerprint.

    Returns:
        Cached or newly created Analyst-backed agent instance.
    """
    return get_cached_agent(
        agent_name,
        factory,
        agent_fingerprint_values(
            **{config_name: config, "sensitive_config": sensitive_config}
        ),
    )


def get_configured_analysis_agent(
    spec: AnalysisAgentCacheSpec,
    kwargs: Mapping[str, Any],
    base_sensitive_config: Any,
    factory_builder: Callable[[Any, Any], Any],
) -> Any:
    """Resolve overrides and return a cached Analyst-backed agent.

    Args:
        spec: Static cache and config-copy settings.
        kwargs: Public wrapper keyword overrides.
        base_sensitive_config: Base SensitiveConfig-like object.
        factory_builder: Callable that builds an agent from copied configs.

    Returns:
        Cached or newly created configured agent instance.
    """
    config = copy_user_analysis_config(
        spec.base_config,
        kwargs,
        spec.field_map,
        spec.user_id,
    )
    sensitive_config = copy_analyst_sensitive_config(
        base_sensitive_config,
        kwargs,
    )
    return get_cached_analysis_agent(
        spec.agent_name,
        lambda: factory_builder(config, sensitive_config),
        spec.config_name,
        config,
        sensitive_config,
    )
