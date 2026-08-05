# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""LangGraph workflow for digital design task submission.

This module exposes `DigitalDesignState`, `DigitalDesignAgents`, and
`design_module`. It prepares protein and promoter design tasks, dispatches
them through AnalystAgent, and returns submitted task metadata.
"""

import logging
import operator
from collections.abc import Mapping
from dataclasses import asdict
from time import perf_counter
from typing import (
    Annotated,
    Any,
    Literal,
    NamedTuple,
    cast,
)

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command, interrupt

from ...common.prompts import get_prompt
from ...config.defaults import DigitalDesignConfig
from ...config.settings import SensitiveConfig, get_sensitive_config
from ...interop.planner import InteropMode
from ...runtime.langgraph_runner import ensure_checkpointer
from ...runtime.locale import SupportedLocale
from ...runtime.result_run_layout import result_child_output_dir
from ...runtime.submission_outcome import (
    AcceptedSubmission,
    SubmissionOutcome,
    classify_submissions,
    has_pending_a2a,
    rejected_submissions_from_state,
)
from ...storage.path_policy import RunIdentity
from ..analyst.agent import (
    ANALYST_CONFIG_FIELD_MAP,
    AnalystAgent,
)
from ..shared.analysis import (
    AnalysisAgentCacheSpec,
    AnalysisCaptureSpec,
    AnalysisStateSpec,
    capture_analysis_result,
    capture_dispatched_analysis,
    finalize_analysis_submission,
    get_configured_analysis_agent,
    route_analysis_tasks,
    run_analysis_graph,
)
from ..shared.analysis_storage import (
    create_output_dir,
    get_data_list,
    resolve_data_list_key,
)
from ..shared.interop import (
    InteropAttempt,
    a2a_pending_state_update,
    build_a2a_resume_draft,
    completed_interop_evidence_update,
    has_interop_target_kind,
    initial_interop_state,
    interop_attempt_update,
    merge_a2a_pending_fields,
    project_a2a_evidence,
    require_a2a_result,
    require_a2a_task_id,
    resolve_interop_dependencies,
    update_a2a_pending_from_result,
)
from ..shared.options import resolve_agent_locale
from ..shared.parallel_dispatch import (
    ParallelDispatchSpec,
    ParallelDispatchState,
    build_parallel_dispatch_graph,
)
from ..shared.remote_analysis import (
    REMOTE_FANOUT_ERRORS,
    RemoteAnalysisPrompt,
    RemoteAnalysisRequest,
    accepted_submission,
    rejected_submission,
    submit_remote_analysis,
)
from .entrypoints import (
    DesignAnalysisRequest,
    DesignEntrypointDependencies,
    _DesignAnalysisSpec,
    submit_design_analysis,
)
from .interop import (
    DESIGN_A2A_CAPABILITY,
    DESIGN_INTEROP_FAILURES,
    DESIGN_MCP_CAPABILITY,
    DesignA2APending,
    DesignEvidence,
    DesignInteropDependencies,
    collect_design_a2a,
    collect_design_evidence,
    format_design_evidence,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DigitalDesignAgents",
    "DigitalDesignState",
    "design_module",
    "promoter_design_for_gene",
    "protein_structure_for_gene",
]

DIGITAL_DESIGN_CONFIG = DigitalDesignConfig()
DIGITAL_DESIGN_CONFIG_FIELD_MAP = {
    **ANALYST_CONFIG_FIELD_MAP,
    "deepgenome_data": "DEEPGENOME_DATA",
}
DIGITAL_DESIGN_TEMPLATE_PATHS = {
    "protein_design_analysis": (
        "user/protein_design_analysis",
        "user/protein_design_analysis_meta",
    ),
    "promoter_design_analysis": (
        "user/promoter_design_analysis",
        "user/promoter_design_analysis_meta",
    ),
}


class _DispatchOptions(NamedTuple):
    """Per-call dispatch options for one design analysis submission.

    Bundles the optional output directory and the polling flag so
    ``_dispatch_and_wait_analysis`` stays within the pylint
    ``too-many-arguments`` budget while still threading the
    consumer-supplied ``is_polling`` (deep_genome mounts pass ``True``;
    the external submit-only flow keeps the ``False`` default).

    Attributes:
        output_dir: Optional pre-allocated output directory path.
        is_polling: Whether the analyst graph blocks until the submitted
            task reaches a terminal state.
        external_evidence: Optional bounded planning evidence from an
            operator-approved external capability.
    """

    output_dir: str | None = None
    is_polling: bool = False
    external_evidence: DesignEvidence | None = None


class DigitalDesignState(ParallelDispatchState):
    """State schema for the digital design workflow.

    Inherits the shared parallel-dispatch bookkeeping fields
    (``analysis_type``, ``task_index``, ``task_ids``,
    ``completed_count``, ``error``, ``failures``) from
    ``ParallelDispatchState`` and adds the protein/promoter
    design-specific fields below.

    Attributes:
        species_code: Three-letter species code (e.g., "ath", "osa").
        gene_id: Gene identifier for target protein or promoter.
        user_id: User identifier.
        batch: Whether this is batch processing.
        output_dir: Output directory path for results.
        is_polling: Whether each design submission blocks until the task
            reaches a terminal state. ``False`` (default) keeps the
            external submit-only flow; a deep_genome mount passes ``True``.
        design_tasks: List of design tasks to be executed.
        task_index: Current task index in parallel execution via Send API.
        task_ids: Mapping of task names to their corresponding task IDs.
        completed_count: Counter tracking the number of completed tasks.
        error: Error message if any task failed during execution.
        interop_mode: External delegation policy.
        interop_targets: Operator-registered target ids eligible for
            planning evidence.
    """

    species_code: str
    gene_id: str
    locale: SupportedLocale
    user_id: str
    batch: bool
    output_dir: str | None
    is_polling: bool
    design_task_result: Annotated[list[dict[str, Any]], operator.add]
    design_tasks: list[dict[str, Any]]  # List of design tasks
    interop_mode: InteropMode
    interop_targets: list[str]
    a2a_pending: Annotated[list[DesignA2APending], operator.add]
    a2a_task_ids: Annotated[dict[str, str], operator.or_]
    submission_rejections: Annotated[list[dict[str, str]], operator.add]


def _project_design_submission_updates(
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Remove rejection sentinels and retain safe rejection records."""
    rejections: list[dict[str, str]] = []
    accepted_results: list[dict[str, Any]] = []
    for item in updates.get("design_task_result", []):
        if not isinstance(item, Mapping):
            continue
        rejected = item.get("_submission_rejected")
        if isinstance(rejected, Mapping):
            goal = rejected.get("goal")
            code = rejected.get("code")
            if isinstance(goal, str) and isinstance(code, str):
                rejections.append({"goal": goal, "code": code})
            continue
        accepted_results.append(dict(item))
    updates["design_task_result"] = accepted_results
    if rejections:
        updates["submission_rejections"] = rejections
    return updates


def _design_submission_outcome(
    result: Mapping[str, Any],
) -> SubmissionOutcome:
    """Classify accepted Design children and safe rejection records."""
    accepted: list[AcceptedSubmission] = []
    task_results = result.get("design_task_result")
    if isinstance(task_results, list):
        for item in task_results:
            if not isinstance(item, Mapping):
                continue
            task_id = item.get("task_id")
            if isinstance(task_id, str) and task_id.strip():
                accepted.append(
                    AcceptedSubmission(
                        task_id=task_id,
                        output_dir=str(item.get("output_dir") or ""),
                    )
                )
    rejected = rejected_submissions_from_state(
        result,
        pending_keys=("goal_description", "analysis_type", "task_id"),
    )
    return classify_submissions(accepted=accepted, rejected=rejected)


class DigitalDesignAgents:
    """LangGraph-based agent for protein and promoter digital design.

    This agent provides a workflow for computational protein design and
    promoter analysis using LangGraph's parallel execution capabilities. It
    leverages the AnalystAgent to submit and manage design tasks.

    Attributes:
        checkpointer: LangGraph checkpointer for state persistence.
        analyst_agent: AnalystAgent instance for task execution.
        digital_design_config: Digital design configuration.
        sensitive_config: Sensitive configuration settings.
        app: Compiled LangGraph application.

    Example:
        >>> agents = DigitalDesignAgents()
        >>> result = await agents.arun(
        ...     species_code="osa",
        ...     gene_id="Os01g0177400"
        ... )
    """

    def __init__(
        self,
        checkpointer: BaseCheckpointSaver | None = None,
        analyst_agent: AnalystAgent | None = None,
        digital_design_config=DIGITAL_DESIGN_CONFIG,
        sensitive_config: SensitiveConfig | None = None,
        **options: Any,
    ):
        """Initialize the DigitalDesignAgents.

        Args:
            checkpointer: LangGraph MemorySaver for state persistence.
            analyst_agent: Optional AnalystAgent instance. Creates one if
                omitted.
            digital_design_config: Digital design configuration object.
            sensitive_config: Sensitive configuration for credentials.
            interop_dependencies: Optional injected discovery/transport seams
                used by offline interoperability tests.
        """
        interop_dependencies = options.pop("interop_dependencies", None)
        if options:
            raise TypeError(
                "unexpected digital-design options: "
                + ", ".join(sorted(options))
            )
        self.checkpointer = ensure_checkpointer(checkpointer)
        self.digital_design_config = digital_design_config
        self.sensitive_config = sensitive_config or get_sensitive_config()
        self.analyst_agent = analyst_agent or AnalystAgent(
            analyst_config=digital_design_config,
            sensitive_config=self.sensitive_config,
        )
        self._interop_dependencies = (
            interop_dependencies or DesignInteropDependencies()
        )
        self.app = self._build_graph()

    def _build_graph(self):
        """Build the LangGraph workflow for digital design tasks."""
        return build_parallel_dispatch_graph(
            ParallelDispatchSpec(
                state_class=DigitalDesignState,
                prepare_node=self.prepare_tasks,
                work_node=self.run_design_node,
                route_fn=self.route_design_tasks,
                work_node_name="design_node",
            ),
            checkpointer=self.checkpointer,
            post_work_nodes=(
                ("design_a2a_resume_node", self.resume_design_a2a),
            ),
        )

    def route_design_tasks(self, state: DigitalDesignState):
        """Dispatch design tasks in parallel using Send API.

        Args:
            state: Current digital design workflow state.

        Returns:
            LangGraph Send commands for each configured design task.
        """
        return route_analysis_tasks(
            "design_node",
            "gene_id",
            "design_tasks",
            state,
        )

    async def _dispatch_and_wait_analysis(
        self,
        analysis_type: str,
        species_code: str,
        gene_id: str,
        options: _DispatchOptions = _DispatchOptions(),
    ) -> dict:
        """Submit task using AnalystAgent and wait for completion.

        Args:
            analysis_type: Type of design analysis.
            species_code: Three-letter species code (e.g., "ath").
            gene_id: Target gene identifier.
            options: Output directory + polling flag for this submission.

        Returns:
            Dict containing task_id and output_dir.
        """
        goal_description, meta, data_list = self._analysis_prompt_parts(
            analysis_type,
            species_code,
            gene_id,
        )
        if options.external_evidence is not None:
            meta = (
                f"{meta}\n\n"
                f"{format_design_evidence(options.external_evidence)}"
            )
        request = RemoteAnalysisRequest(
            analysis_type=analysis_type,
            target_id=gene_id,
            output_dir=options.output_dir,
            prompt=RemoteAnalysisPrompt(
                goal_description=goal_description,
                meta=meta,
                data_list=data_list,
            ),
            compute_resource=self._get_compute_resource(analysis_type),
            output_dir_is_result_child=True,
        )
        result = await submit_remote_analysis(
            self.analyst_agent,
            self.digital_design_config,
            self.sensitive_config,
            request,
            is_polling=options.is_polling,
        )
        accepted_submission(result)
        return result

    def _analysis_prompt_parts(
        self,
        analysis_type: str,
        species_code: str,
        gene_id: str,
    ) -> tuple[str, str, dict[str, Any]]:
        """Return goal, meta, and data list for one design analysis."""
        paths = DIGITAL_DESIGN_TEMPLATE_PATHS.get(analysis_type)
        if paths is None:
            raise ValueError(f"Unknown analysis type: {analysis_type}")
        goal_path, meta_path = paths
        goal_description = get_prompt(
            self.digital_design_config.PROMPT_FILE,
            goal_path,
            {"gene_id": gene_id},
        )
        meta = get_prompt(self.digital_design_config.PROMPT_FILE, meta_path)
        data_list = get_data_list(
            self.digital_design_config.DEEPGENOME_DATA,
            analysis_type,
            species_code,
        )
        return goal_description, meta, data_list

    def _get_compute_resource(
        self, analysis_type: str
    ) -> Literal["small", "medium", "large"]:
        """Determine compute resource level based on analysis type."""
        medium_compute_types = {"protein_design_analysis"}
        if analysis_type in medium_compute_types:
            return "medium"
        return "small"

    def _design_interop_task(
        self,
        state: DigitalDesignState,
        analysis_type: str,
        gene_id: str,
    ) -> dict[str, Any]:
        """Build the bounded external planning payload for one task."""
        goal, context, data_list = self._analysis_prompt_parts(
            analysis_type,
            state["species_code"],
            gene_id,
        )
        return {
            "analysis_type": analysis_type,
            "species_code": state["species_code"],
            "gene_id": gene_id,
            "goal_description": goal,
            "context": context,
            "data_list": data_list,
            "output_dir": state.get("output_dir"),
            "thread_id": state.get("thread_id", analysis_type),
            "is_polling": bool(state.get("is_polling", False)),
            "interop_mode": state.get("interop_mode", "off"),
            "interop_targets": state.get("interop_targets", []),
        }

    async def _collect_design_external(
        self,
        task: Mapping[str, Any],
    ) -> DesignEvidence | DesignA2APending | None:
        """Collect optional planning evidence before local Analyst dispatch."""
        mode = cast(InteropMode, task.get("interop_mode", "off"))
        target_ids = tuple(task.get("interop_targets", []))
        dependencies = resolve_interop_dependencies(
            self._interop_dependencies,
            mode=mode,
            sensitive_config=self.sensitive_config,
        )
        if dependencies is None:
            return None
        self._interop_dependencies = dependencies
        if has_interop_target_kind(
            dependencies,
            target_ids,
            "a2a",
        ):
            result = await collect_design_a2a(
                task,
                mode=mode,
                target_ids=target_ids,
                dependencies=dependencies,
            )
            if result is not None:
                result_status = result["status"]
                if result_status == "input_required":
                    task_id = require_a2a_task_id(result)
                    pending = merge_a2a_pending_fields(
                        {
                            "analysis_type": str(task["analysis_type"]),
                            "species_code": str(task["species_code"]),
                            "gene_id": str(task["gene_id"]),
                            "goal_description": str(task["goal_description"]),
                            "context": str(task["context"]),
                            "data_list": dict(task.get("data_list") or {}),
                            "output_dir": str(task["output_dir"]),
                            "thread_id": str(task.get("thread_id", "design")),
                            "is_polling": bool(task.get("is_polling", False)),
                        },
                        {**result, "task_id": task_id},
                    )
                    return cast(DesignA2APending, pending)
                return cast(DesignEvidence, project_a2a_evidence(result))
        evidence = await collect_design_evidence(
            task,
            mode=mode,
            target_ids=target_ids,
            dependencies=dependencies,
        )
        if evidence is None and mode == "required":
            raise RuntimeError(
                "required Design interop produced no external evidence"
            )
        return evidence

    async def prepare_tasks(self, state: DigitalDesignState) -> dict:
        """Prepare the list of design tasks.

        Args:
            state: Current digital design workflow state.

        Returns:
            State update with design tasks and counters initialized.
        """
        run_identity = RunIdentity.create(
            user_id=state.get("user_id"),
            scope="digital_design_task",
        )
        access_key_id, secret_access_key = (
            self.sensitive_config.obs_credentials()
        )
        output_dir = state.get("output_dir") or create_output_dir(
            user_id=run_identity.user_id,
            task="digital_design_task",
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=self.digital_design_config.OBS_SERVER,
            bucket_name=self.digital_design_config.BUCKET_NAME,
            run_identity=run_identity,
        )
        tasks = [
            {
                "analysis_type": "protein_design_analysis",
                "output_dir": result_child_output_dir(output_dir, 0),
            },
            {
                "analysis_type": "promoter_design_analysis",
                "output_dir": result_child_output_dir(output_dir, 1),
            },
        ]
        return {
            "design_tasks": tasks,
            "output_dir": output_dir,
            "task_ids": {},
            "completed_count": 0,
        }

    async def run_design_node(
        self,
        state: DigitalDesignState,
    ) -> dict[str, Any] | Command:
        """Execute a single design task dispatched via Send API.

        This node is called dynamically for each task in the design_tasks list.

        Args:
            state: Current state for one dispatched design task.

        Returns:
            State update with submitted task metadata or error details.
            An A2A input-required task returns a ``Command`` that persists
            safe correlation ids before entering the resume node.
        """
        gene_id = state["gene_id"]
        analysis_type = state["analysis_type"]

        logger.info(
            "[Design-%s] Executing: %s for %s",
            state.get("task_index"),
            analysis_type,
            gene_id,
        )

        interop_task = self._design_interop_task(
            state,
            analysis_type,
            gene_id,
        )
        started = perf_counter()
        mode = cast(InteropMode, interop_task["interop_mode"])
        target_ids = tuple(interop_task["interop_targets"])
        try:
            external = await self._collect_design_external(interop_task)
        except DESIGN_INTEROP_FAILURES as exc:

            async def failed_submit(error: Exception = exc) -> dict[str, Any]:
                """Re-enter the shared failure recorder for interop errors."""
                raise error

            updates = await capture_analysis_result(
                state,
                analysis_type=analysis_type,
                submit_call=failed_submit,
                spec=AnalysisCaptureSpec(
                    result_list_key="design_task_result",
                    captured_exceptions=DESIGN_INTEROP_FAILURES,
                ),
            )
            if mode != "off":
                updates.update(
                    interop_attempt_update(
                        InteropAttempt(
                            self._interop_dependencies,
                            target_ids,
                            mode,
                            DESIGN_MCP_CAPABILITY,
                            DESIGN_A2A_CAPABILITY,
                            "failed",
                            perf_counter() - started,
                        )
                    )
                )
            return updates

        if isinstance(external, dict) and "analysis_type" in external:
            pending = cast(DesignA2APending, external)
            return Command(
                goto="design_a2a_resume_node",
                update=a2a_pending_state_update(
                    pending,
                    task_key=analysis_type,
                    latency_seconds=perf_counter() - started,
                ),
            )

        evidence = (
            external
            if isinstance(external, dict) and "analysis_type" not in external
            else None
        )

        async def _dispatch(
            a_type: str,
            species: str,
            gene: str,
            out_dir: str | None,
        ) -> dict:
            try:
                return await self._dispatch_and_wait_analysis(
                    a_type,
                    species,
                    gene,
                    _DispatchOptions(
                        output_dir=out_dir,
                        is_polling=bool(interop_task["is_polling"]),
                        external_evidence=evidence,
                    ),
                )
            except REMOTE_FANOUT_ERRORS as exc:
                return {
                    "_submission_rejected": asdict(
                        rejected_submission(gene, exc)
                    )
                }

        updates = await capture_dispatched_analysis(
            state,
            analysis_type,
            "gene_id",
            _dispatch,
            AnalysisCaptureSpec(
                result_key="design_task_result",
                result_list_key="design_task_result",
                captured_exceptions=(),
            ),
        )
        updates = _project_design_submission_updates(updates)
        if mode != "off":
            if evidence is None:
                updates.update(
                    interop_attempt_update(
                        InteropAttempt(
                            self._interop_dependencies,
                            target_ids,
                            mode,
                            DESIGN_MCP_CAPABILITY,
                            DESIGN_A2A_CAPABILITY,
                            "degraded",
                            perf_counter() - started,
                            True,
                        )
                    )
                )
            else:
                updates.update(
                    completed_interop_evidence_update(
                        evidence, perf_counter() - started
                    )
                )
        return updates

    async def resume_design_a2a(
        self,
        state: DigitalDesignState,
    ) -> dict[str, Any]:
        """Resume one paused A2A planning exchange before local dispatch."""
        pending = next(iter(state.get("a2a_pending", ())), None)
        if pending is None:
            return {}
        started = perf_counter()
        task = {
            "analysis_type": pending["analysis_type"],
            "species_code": pending["species_code"],
            "gene_id": pending["gene_id"],
            "goal_description": pending["goal_description"],
            "context": pending["context"],
            "data_list": dict(pending["data_list"]),
            "output_dir": pending["output_dir"],
            "thread_id": pending["thread_id"],
            "is_polling": pending["is_polling"],
        }
        required_mode: InteropMode = "required"
        dependencies = resolve_interop_dependencies(
            self._interop_dependencies,
            mode=required_mode,
            sensitive_config=self.sensitive_config,
        )
        while True:
            draft = build_a2a_resume_draft(
                pending,
                kind="external_a2a_design",
                label_key="analysis_type",
                extra={
                    "species_code": pending["species_code"],
                    "gene_id": pending["gene_id"],
                },
            )
            resume_payload = interrupt(draft)
            if not isinstance(resume_payload, dict):
                resume_payload = {"text": str(resume_payload)}
            result = await collect_design_a2a(
                task,
                mode="required",
                target_ids=(pending["target_id"],),
                dependencies=dependencies,
                resume=resume_payload,
                pending=pending,
            )
            result = require_a2a_result(result, "Design")
            if result["status"] != "input_required":
                break
            pending = cast(
                DesignA2APending,
                update_a2a_pending_from_result(pending, result),
            )

        evidence = cast(DesignEvidence, project_a2a_evidence(result))

        async def _dispatch(
            analysis_type: str,
            species_code: str,
            gene_id: str,
            output_dir: str | None,
        ) -> dict[str, Any]:
            try:
                return await self._dispatch_and_wait_analysis(
                    analysis_type,
                    species_code,
                    gene_id,
                    _DispatchOptions(
                        output_dir=output_dir,
                        is_polling=pending["is_polling"],
                        external_evidence=evidence,
                    ),
                )
            except REMOTE_FANOUT_ERRORS as exc:
                return {
                    "_submission_rejected": asdict(
                        rejected_submission(gene_id, exc)
                    )
                }

        resume_state = {
            **state,
            "species_code": pending["species_code"],
            "gene_id": pending["gene_id"],
            "output_dir": pending["output_dir"],
            "task_ids": {},
            "design_task_result": [],
        }
        updates = await capture_dispatched_analysis(
            resume_state,
            pending["analysis_type"],
            "gene_id",
            _dispatch,
            AnalysisCaptureSpec(
                result_key="design_task_result",
                result_list_key="design_task_result",
                captured_exceptions=(),
            ),
        )
        return {
            **_project_design_submission_updates(updates),
            **completed_interop_evidence_update(
                evidence, perf_counter() - started
            ),
        }

    async def arun(
        self,
        species_code: str,
        gene_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Submit protein design tasks and return task_ids.

        Args:
            species_code: Three-letter species code (e.g., "ath", "osa").
            gene_id: Gene identifier.
            user_id: Optional user identifier.
            batch: Whether this is batch processing.
            thread_id: Optional thread ID for checkpointer.

        Returns:
            Dict with task_ids on success, or error on failure.
        """
        result = await run_analysis_graph(
            self.app,
            {
                "species_code": species_code,
                "gene_id": gene_id,
                "locale": resolve_agent_locale(kwargs.get("locale")),
                **initial_interop_state(kwargs),
            },
            kwargs,
            ("design_task_result", "error", "failures"),
            AnalysisStateSpec(
                tasks_key="design_tasks",
                result_inits={
                    "design_task_result": [],
                    "submission_rejections": [],
                },
            ),
        )
        return finalize_analysis_submission(
            result,
            _design_submission_outcome(result),
            pending=has_pending_a2a(result),
        )


async def design_module(
    species_code: str,
    gene_id: str,
    user_id: str | None = None,
    batch: bool = True,
    *,
    locale: SupportedLocale | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compatibility wrapper around the LangGraph digital design agent.

    Args:
        species_code: Three-letter species code (e.g., "ath", "osa").
        gene_id: Target gene identifier.
        user_id: Optional user identifier for output paths.
        batch: Whether to reuse provided output directories.
        **kwargs: Keyword-compatible analysis and sensitive overrides.

    Returns:
        Digital design task submission result.
    """
    effective_locale = resolve_agent_locale(locale)
    agent = get_configured_analysis_agent(
        AnalysisAgentCacheSpec(
            "DigitalDesignAgents",
            "digital_design_config",
            DIGITAL_DESIGN_CONFIG,
            DIGITAL_DESIGN_CONFIG_FIELD_MAP,
            user_id,
        ),
        kwargs,
        get_sensitive_config(),
        lambda config, sensitive: DigitalDesignAgents(
            digital_design_config=config,
            sensitive_config=sensitive,
        ),
    )
    return await agent.arun(
        species_code=species_code,
        gene_id=gene_id,
        user_id=user_id,
        batch=batch,
        output_dir=kwargs.get("output_dir"),
        interop_mode=kwargs.get("interop_mode", "off"),
        interop_targets=kwargs.get("interop_targets", []),
        locale=effective_locale,
    )


def _design_entrypoint_dependencies() -> DesignEntrypointDependencies:
    """Capture injectable producer dependencies from this module."""
    return DesignEntrypointDependencies(
        config=DIGITAL_DESIGN_CONFIG,
        get_sensitive_config=get_sensitive_config,
        get_prompt=get_prompt,
        get_data_list=get_data_list,
        resolve_data_list_key=resolve_data_list_key,
        analyst_factory=AnalystAgent,
        submit_remote_analysis=submit_remote_analysis,
    )


async def protein_structure_for_gene(
    species_code: str,
    gene_id: str,
    output_dir: str | None = None,
    *,
    is_polling: bool = True,
) -> dict[str, Any]:
    """Submit a protein_structure_analysis task via the analyst subgraph."""
    return await submit_design_analysis(
        DesignAnalysisRequest(
            species_code=species_code,
            gene_id=gene_id,
            spec=_DesignAnalysisSpec(
                analysis_type="protein_structure_analysis",
                goal_path="user/structure_analysis",
                meta_path="user/structure_analysis_meta",
                compute_resource="medium",
            ),
            output_dir=output_dir,
            is_polling=is_polling,
        ),
        _design_entrypoint_dependencies(),
    )


async def promoter_design_for_gene(
    species_code: str,
    gene_id: str,
    output_dir: str | None = None,
    *,
    is_polling: bool = True,
) -> dict[str, Any]:
    """Submit a promoter_analysis task via the analyst subgraph."""
    return await submit_design_analysis(
        DesignAnalysisRequest(
            species_code=species_code,
            gene_id=gene_id,
            spec=_DesignAnalysisSpec(
                analysis_type="promoter_analysis",
                goal_path="user/promoter_analysis",
                meta_path="user/promoter_analysis_meta",
                compute_resource="small",
            ),
            output_dir=output_dir,
            is_polling=is_polling,
        ),
        _design_entrypoint_dependencies(),
    )
