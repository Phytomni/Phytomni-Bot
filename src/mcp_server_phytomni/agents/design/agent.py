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
from ..analyst.agent import (
    ANALYST_CONFIG_FIELD_MAP,
    AnalystAgent,
)
from ..shared.analysis import (
    AnalysisAgentCacheSpec,
    AnalysisStateSpec,
    capture_analysis_result,
    capture_dispatched_analysis,
    get_configured_analysis_agent,
    route_analysis_tasks,
    run_analysis_graph,
)
from ..shared.analysis_storage import get_data_list, resolve_data_list_key
from ..shared.interop import (
    InteropAttempt,
    build_a2a_resume_draft,
    has_interop_target_kind,
    initial_interop_state,
    interop_attempt_update,
    interop_evidence_update,
    interop_state_update,
    make_interop_record,
    merge_a2a_pending_fields,
    project_a2a_evidence,
    require_a2a_result,
    resolve_interop_dependencies,
    update_a2a_pending_from_result,
)
from ..shared.parallel_dispatch import (
    ParallelDispatchSpec,
    ParallelDispatchState,
    build_parallel_dispatch_graph,
)
from ..shared.remote_analysis import (
    RemoteAnalysisRequest,
    submit_remote_analysis,
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
        interop_dependencies: DesignInteropDependencies | None = None,
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
            goal_description=goal_description,
            meta=meta,
            data_list=data_list,
            compute_resource=self._get_compute_resource(analysis_type),
        )
        return await submit_remote_analysis(
            self.analyst_agent,
            self.digital_design_config,
            self.sensitive_config,
            request,
            is_polling=options.is_polling,
        )

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
                if result["status"] == "input_required":
                    task_id = result.get("task_id")
                    if not task_id:
                        raise RuntimeError(
                            "external A2A input-required response "
                            "omitted task_id"
                        )
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
        _ = state
        tasks = [
            {"analysis_type": "protein_design_analysis"},
            {"analysis_type": "promoter_design_analysis"},
        ]
        return {"design_tasks": tasks, "task_ids": {}, "completed_count": 0}

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
                result_key=None,
                result_list_key="design_task_result",
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
                update={
                    "a2a_pending": [pending],
                    "a2a_task_ids": {
                        analysis_type: pending["task_id"],
                    },
                    **interop_state_update(
                        make_interop_record(
                            target_id=pending["target_id"],
                            kind="a2a",
                            capability=pending["capability"],
                            status="input_required",
                            latency_seconds=perf_counter() - started,
                        )
                    ),
                },
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

        updates = await capture_dispatched_analysis(
            state,
            analysis_type,
            "gene_id",
            _dispatch,
            ("design_task_result", "design_task_result"),
        )
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
                    interop_evidence_update(
                        evidence,
                        status="completed",
                        latency_seconds=perf_counter() - started,
                    )
                )
        return updates

    async def resume_design_a2a(
        self,
        state: DigitalDesignState,
    ) -> dict[str, Any]:
        """Resume one paused A2A planning exchange before local dispatch."""
        pending_items = state.get("a2a_pending", [])
        if not pending_items:
            return {}
        pending = pending_items[0]
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
            ("design_task_result", "design_task_result"),
        )
        updates.update(
            interop_evidence_update(
                evidence,
                status="completed",
                latency_seconds=perf_counter() - started,
            )
        )
        return updates

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
        return await run_analysis_graph(
            self.app,
            {
                "species_code": species_code,
                "gene_id": gene_id,
                **initial_interop_state(kwargs),
            },
            kwargs,
            ("design_task_result", "error", "failures"),
            AnalysisStateSpec(
                tasks_key="design_tasks",
                result_inits={"design_task_result": []},
            ),
        )


async def design_module(
    species_code: str,
    gene_id: str,
    user_id: str | None = None,
    batch: bool = True,
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
    )


class _DesignAnalysisSpec(NamedTuple):
    """Static spec bundle for one module-level design analysis wrapper.

    Bundling the four ``analysis_type`` / ``goal_path`` / ``meta_path``
    / ``compute_resource`` fields keeps ``_submit_design_analysis``
    within the pylint ``too-many-arguments`` budget while preserving
    a single shared dispatch helper for both ``protein_structure_for_gene``
    and ``promoter_design_for_gene``.
    """

    analysis_type: str
    goal_path: str
    meta_path: str
    compute_resource: Literal["small", "medium", "large"]


async def _submit_design_analysis(
    species_code: str,
    gene_id: str,
    spec: _DesignAnalysisSpec,
    output_dir: str | None,
    *,
    is_polling: bool,
) -> dict[str, Any]:
    """Build prompt parts and dispatch one analyst submission.

    Shared helper for ``protein_structure_for_gene`` and
    ``promoter_design_for_gene``. Mirrors the
    ``DigitalDesignAgents._dispatch_and_wait_analysis`` shape used
    by the parallel-dispatch graph, but at module level so deep_genome
    can route its single-gene analysis branches here without
    constructing a full design graph.
    """
    sensitive = get_sensitive_config()
    goal_description = get_prompt(
        DIGITAL_DESIGN_CONFIG.PROMPT_FILE,
        spec.goal_path,
        {"gene_id": gene_id},
    )
    meta = get_prompt(DIGITAL_DESIGN_CONFIG.PROMPT_FILE, spec.meta_path)
    data_list = get_data_list(
        DIGITAL_DESIGN_CONFIG.DEEPGENOME_DATA,
        resolve_data_list_key(spec.analysis_type),
        species_code,
    )
    request = RemoteAnalysisRequest(
        analysis_type=spec.analysis_type,
        target_id=gene_id,
        output_dir=output_dir,
        goal_description=goal_description,
        meta=meta,
        data_list=data_list,
        compute_resource=spec.compute_resource,
    )
    return await submit_remote_analysis(
        AnalystAgent(
            analyst_config=DIGITAL_DESIGN_CONFIG,
            sensitive_config=sensitive,
        ),
        DIGITAL_DESIGN_CONFIG,
        sensitive,
        request,
        is_polling=is_polling,
    )


async def protein_structure_for_gene(
    species_code: str,
    gene_id: str,
    output_dir: str | None = None,
    *,
    is_polling: bool = True,
) -> dict[str, Any]:
    """Submit a protein_structure_analysis task via the analyst subgraph.

    Producer-side counterpart to deep_genome's
    ``protein_structure_analysis`` dispatch branch. Returns the
    ``submit_analyst_via_subgraph`` projection so deep_genome's
    commit-2 rerouting is a straight callee swap.

    Args:
        species_code: Three-letter species code used to select prepared
            data.
        gene_id: Target gene identifier for the structure prompt.
        output_dir: Optional pre-allocated OBS output directory.
        is_polling: Whether the analyst graph should block until the
            submitted task reaches a terminal state. Defaults to
            ``True`` to preserve deep_genome's polling semantics.

    Returns:
        Projected dispatch dict containing ``task_id`` / ``output_dir``
        / ``plan`` / ``tool_usages`` / ``task_status``.
    """
    return await _submit_design_analysis(
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
    )


async def promoter_design_for_gene(
    species_code: str,
    gene_id: str,
    output_dir: str | None = None,
    *,
    is_polling: bool = True,
) -> dict[str, Any]:
    """Submit a promoter_analysis task via the analyst subgraph.

    Producer-side counterpart to deep_genome's ``promoter_analysis``
    dispatch branch. Returns the ``submit_analyst_via_subgraph``
    projection so deep_genome's commit-2 rerouting is a straight
    callee swap.

    Args:
        species_code: Three-letter species code used to select prepared
            data.
        gene_id: Target gene identifier for the promoter prompt.
        output_dir: Optional pre-allocated OBS output directory.
        is_polling: Whether the analyst graph should block until the
            submitted task reaches a terminal state. Defaults to
            ``True`` to preserve deep_genome's polling semantics.

    Returns:
        Projected dispatch dict containing ``task_id`` / ``output_dir``
        / ``plan`` / ``tool_usages`` / ``task_status``.
    """
    return await _submit_design_analysis(
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
    )
