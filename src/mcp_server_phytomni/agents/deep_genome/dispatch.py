# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Dispatch and analysis nodes for the DeepGenome workflow.

Exports AnalysisDispatchContext and DeepGenomeDispatchMixin. The mixin routes
LangGraph branches, prepares Analyst task prompts, dispatches deep analyses,
downloads OBS results, and builds analyst sub-summaries.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, TypedDict, Unpack, cast

from langgraph.graph import END

from ...config.relay_mode import relay_mode_enabled
from ...graphs.analyst_dispatch_adapters import submit_analyst_via_subgraph
from ...runtime.artifact_roles import append_artifact_manifest_contract
from ...runtime.deep_genome_store import (
    DeepGenomeReservation,
    DeepGenomeStore,
    DeepGenomeTrackingError,
)
from ...runtime.task_manager import resolve_tasks_db_path
from ...storage.obs_storage import obsfs_path_for
from ...storage.path_policy import RunIdentity
from ...storage.scratch import resolve_scratch_dir
from ..analyst.storage import download_obs_out, download_obs_out_via_relay
from ..analyst.task_ops import task_delete, task_status
from ..design.agent import (
    promoter_design_for_gene,
    protein_structure_for_gene,
)
from ..shared.sql import gauss_query, relay_bi_query, sql_literal
from . import remote_io as deep_genome_remote_io
from . import routing as deep_genome_routing
from .coordinator import (
    RemoteSubmission,
    WorkItemOutcome,
    poll_work_item,
)
from .summary import build_design_work_item_summary, build_sub_summary
from .tracking import DeepGenomeTransitionSink
from .work_items import (
    WorkItemSpec,
    build_logical_analysis_tasks,
    build_work_item_plan,
)

if TYPE_CHECKING:
    from .agent import DeepGenomeState
else:
    DeepGenomeState = dict[str, Any]


class _DispatchPollOptions(TypedDict, total=False):
    """Optional compatibility bindings for one remote poll."""

    summary_builder: Callable[[str], str] | None
    tracking: DeepGenomeTransitionSink | None
    work_item_key: str | None


logger = logging.getLogger(__name__)

DeepGenomeRemoteIO = deep_genome_remote_io.DeepGenomeRemoteIO
ANALYSIS_DATA_LIST_MAP = deep_genome_routing.ANALYSIS_DATA_LIST_MAP
ANALYSIS_GOAL_TEMPLATE_MAP = deep_genome_routing.ANALYSIS_GOAL_TEMPLATE_MAP
ANALYSIS_META_TEMPLATE_MAP = deep_genome_routing.ANALYSIS_META_TEMPLATE_MAP
ANALYSIS_TARGET_FILE_FEATURE_MAP = (
    deep_genome_routing.ANALYSIS_TARGET_FILE_FEATURE_MAP
)
AnalysisDispatchContext = deep_genome_routing.AnalysisDispatchContext
DEFAULT_TARGET_FILE_FEATURE = deep_genome_routing.DEFAULT_TARGET_FILE_FEATURE
GENERIC_ANALYSIS_NODE_TYPES = deep_genome_routing.GENERIC_ANALYSIS_NODE_TYPES
_analyst_node_name = deep_genome_routing.analyst_node_name

_BEST_EFFORT_ERRORS: tuple[type[Exception], ...] = (Exception,)

_DESIGN_SUMMARY_ERRORS: tuple[type[Exception], ...] = (Exception,)
_RESOLVED_GENE_ID_COLUMNS = {"osa": "msu_gene_id", "zma": "v4_id"}


def _outcome_work_item_delta(
    *,
    work_item_key: str,
    submission: RemoteSubmission,
    outcome: WorkItemOutcome,
    state: DeepGenomeState,
    summary_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project one polled outcome while preserving stable remote identities."""
    task_key = f"task_{state.get('task_index')}:{work_item_key}"
    record: dict[str, Any] = {
        "status": outcome.status,
        "analysis_type": work_item_key,
        "task_id": submission.submitted_task_id,
        "poll_task_id": submission.poll_task_id,
        "output_path": submission.output_dir,
        "summary_markdown": outcome.summary,
    }
    if outcome.failure_reason:
        record["error"] = outcome.failure_reason
    delta: dict[str, Any] = {
        "raw_analyst_data": {task_key: record},
        "analysis_completed_branches": 1,
    }
    if summary_data is not None:
        delta["analyst_summaries"] = summary_data
    return delta


class DeepGenomeDispatchMixin:
    """Routing, dispatch, and analysis-task nodes for DeepGenome.
    Dispatch methods share durable lifecycle and remote-submission state."""

    def _remote_io(self: Any) -> DeepGenomeRemoteIO:
        """Build the typed adapter with the current runtime seams."""
        return DeepGenomeRemoteIO(
            config=self.deep_genome_config,
            sensitive_config=getattr(self, "sensitive_config", None),
            analyst_agent=getattr(
                getattr(self, "_agents", None), "analyst_agent", None
            ),
            hooks={
                "task_status": task_status,
                "task_delete": task_delete,
                "analyst_submit": submit_analyst_via_subgraph,
                "protein_structure": protein_structure_for_gene,
                "promoter_design": promoter_design_for_gene,
                "obsfs_path": obsfs_path_for,
                "relay_enabled": relay_mode_enabled,
                "relay_download": download_obs_out_via_relay,
                "sdk_download": download_obs_out,
                "scratch_dir": resolve_scratch_dir,
                "poll_work_item": poll_work_item,
            },
        )

    @staticmethod
    def _remote_io_for(instance: Any) -> DeepGenomeRemoteIO:
        """Resolve the adapter for real agents and lightweight test hosts."""
        factory = getattr(instance, "_remote_io", None)
        if callable(factory):
            return cast(DeepGenomeRemoteIO, factory())
        return DeepGenomeDispatchMixin._remote_io(instance)

    def _transition_sink(
        self: Any,
        state: DeepGenomeState | None,
    ) -> DeepGenomeTransitionSink:
        """Bind one graph state to the shared durable transition sink."""
        return DeepGenomeTransitionSink.from_state(
            state,
            store_path=resolve_tasks_db_path(),
            cancel_submission=getattr(self, "_cancel_submission", None),
        )

    def _analysis_request_kwargs(self: Any) -> dict[str, Any]:
        """Build bounded platform kwargs for status and cancellation calls."""
        return DeepGenomeDispatchMixin._remote_io_for(
            self
        ).analysis_request_kwargs()

    async def _cancel_submission(
        self: Any, submission: RemoteSubmission
    ) -> None:
        """Best-effort terminate the caller-owned job after tracking loss.

        A deduplicated ``poll_task_id`` can point at another tenant's source
        job.  It is intentionally never cancelled from this owner-scoped
        failure path; only the accepted caller-owned id is eligible.
        """
        await DeepGenomeDispatchMixin._remote_io_for(self).cancel_submission(
            submission
        )

    def _route_start(self: Any, state: DeepGenomeState):
        """Return BriefGene as the only initial node and launch barrier."""
        return deep_genome_routing.route_start(state)

    def _route_after_brief_gene(self: Any, state: DeepGenomeState):
        """Gate analyst preparation on successful BriefGene completion."""
        return deep_genome_routing.route_after_brief_gene(state)

    def _route_synthesize_barrier(self: Any, state: DeepGenomeState):
        """Route from concrete outcomes and require usable synthesis."""
        return deep_genome_routing.route_synthesize_barrier(state)

    def _route_experiment_barrier(self: Any, state: DeepGenomeState):
        """Route only after BriefGene and synthesis are available."""
        return deep_genome_routing.route_experiment_barrier(state)

    def _route_analyst_tasks(self: Any, state: DeepGenomeState):
        """Dispatch analysis tasks in parallel using the Send API.

        The evolution task fans to the dedicated ``evolution_node`` and
        the digital_design task to the mounted ``design_node`` (both
        mounted standalone subgraphs); every other task fans to the
        generic ``analyst_node``. The logical branch list stays in
        ``analysis_tasks`` while the barrier derives readiness from the
        twelve concrete ``work_items`` rows.

        Args:
            state: Current workflow state containing analysis_tasks.

        Returns:
            List of Send objects for dynamic task dispatch.
        """
        return deep_genome_routing.build_analyst_sends(state)

    def _route_after_analyst(self: Any, state: DeepGenomeState):
        """Signal to end Send instance execution.

        This allows synthesize_node to act as a barrier, waiting for
        all Send instances to complete before proceeding.

        Args:
            state: Current workflow state.

        Returns:
            END to terminate Send instances.
        """
        _ = state
        return END

    async def _run_analyst_node(self: Any, state: DeepGenomeState) -> dict:
        """Execute a single analysis task dispatched via Send API.

        This node is called dynamically for each analysis task. It dispatches
        the task to AnalystAgent, waits for completion, and generates a
        sub-summary of the results.

        Args:
            state: Current workflow state containing task details:
                - task_index: Index of the current task
                - target_gene: Target gene identifier
                - species_code: Three-letter species code
                - analysis_type: Type of analysis to perform

        Returns:
            Dict containing:
                - raw_analyst_data: Task execution results
                - analyst_summaries: Generated sub-summary
                - analysis_completed_branches: Increment counter by 1
        """
        task_index = state.get("task_index")
        gene_id = state["target_gene"]
        species_code = state["species_code"]
        analysis_type = state["analysis_type"]
        sleep_seconds = state.get("task_submit_sleep", 0)
        if sleep_seconds > 0:
            logger.info(
                "[Analyst-%s] sleep %ss before execution",
                task_index,
                sleep_seconds,
            )
            await asyncio.sleep(sleep_seconds)
        logger.info(
            "[Analyst-%s] Executing: %s for %s",
            task_index,
            analysis_type,
            gene_id,
        )

        async def run_analysis() -> dict[str, Any]:
            """Dispatch one analysis branch and return state updates."""
            result = await self._dispatch_and_wait_analysis(
                analysis_type=analysis_type,
                species_code=species_code,
                gene_id=gene_id,
                state=state,
            )

            sub_summary = self._generate_sub_summary(
                analysis_type=analysis_type,
                gene_id=gene_id,
                state=state,
                results_dir=result.get("results_dir"),
            )

            return {
                "raw_analyst_data": {
                    f"task_{task_index}": {
                        "status": "success",
                        "analysis_type": analysis_type,
                        "task_id": result.get("task_id"),
                        "output_path": result.get("output_path"),
                    }
                },
                "analyst_summaries": sub_summary,
                "analysis_completed_branches": 1,
            }

        def failure_state(exc: Exception) -> dict[str, Any]:
            """Preserve branch failure details in state."""
            return {
                "raw_analyst_data": {
                    f"task_{task_index}": {
                        "status": "failed",
                        "analysis_type": analysis_type,
                        "error": str(exc),
                    }
                },
                "analysis_completed_branches": 1,
            }

        try:
            return await run_analysis()
        except DeepGenomeTrackingError:
            raise
        except _BEST_EFFORT_ERRORS as exc:
            return failure_state(exc)

    async def finalize_evolution_result(
        self: Any,
        task: dict | RemoteSubmission | None,
        state: DeepGenomeState,
    ) -> dict:
        """Poll an evolution submission and project its branch outcome."""
        species_code = state["species_code"]
        gene_id = state["target_gene"]
        task_index = state.get("task_index")
        if task is None:
            raise RuntimeError(
                "evolution mount produced no task (taxid resolution failed)"
            )
        if isinstance(task, RemoteSubmission):
            run_identity = RunIdentity.create(
                user_id=self.deep_genome_config.USER_ID,
                scope="evolution_analysis",
            )
            context = deep_genome_routing.build_analysis_context(
                analysis_type="evolution_analysis",
                species_code=species_code,
                gene_id=gene_id,
                output_dir=task.output_dir,
            )
            tracking = DeepGenomeDispatchMixin._transition_sink(self, state)
            await tracking.accept_remote_submission(
                "evolution_analysis",
                task,
            )
            outcome, results_dir = await self._poll_remote_submission(
                task,
                context,
                run_identity,
                tracking=tracking,
                work_item_key="evolution_analysis",
            )
            summary_data = None
            if outcome.status == "succeeded" and results_dir is not None:
                summary_data = self._generate_sub_summary(
                    analysis_type="evolution_analysis",
                    gene_id=gene_id,
                    state=state,
                    results_dir=results_dir,
                )
            return _outcome_work_item_delta(
                work_item_key="evolution_analysis",
                submission=task,
                outcome=outcome,
                state=state,
                summary_data=summary_data,
            )
        self._raise_if_agent_failed(task)
        output_path = task.get("output_dir")
        if not isinstance(output_path, str):
            raise RuntimeError("evolution mount returned no output directory")
        run_identity = RunIdentity.create(
            user_id=self.deep_genome_config.USER_ID,
            scope="evolution_analysis",
        )
        context = deep_genome_routing.build_analysis_context(
            analysis_type="evolution_analysis",
            species_code=species_code,
            gene_id=gene_id,
            output_dir=output_path,
        )
        results_dir = await self._download_analysis_result(
            context, output_path, run_identity
        )
        sub_summary = self._generate_sub_summary(
            analysis_type="evolution_analysis",
            gene_id=gene_id,
            state=state,
            results_dir=results_dir,
        )
        return {
            "raw_analyst_data": {
                f"task_{task_index}": {
                    "status": "success",
                    "analysis_type": "evolution_analysis",
                    "task_id": task.get("task_id"),
                    "output_path": output_path,
                }
            },
            "analyst_summaries": sub_summary,
            "analysis_completed_branches": 1,
        }

    async def _poll_design_work_item(
        self: Any,
        work_item_key: str,
        submission: RemoteSubmission | None,
        state: DeepGenomeState,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Poll one independent Design item and return raw/report deltas."""
        task_key = f"task_{state.get('task_index')}:{work_item_key}"
        tracking = DeepGenomeDispatchMixin._transition_sink(self, state)
        if submission is None:
            await tracking.record_work_item_failure(work_item_key)
            return (
                {
                    task_key: {
                        "status": "failed",
                        "analysis_type": work_item_key,
                        "task_id": None,
                        "poll_task_id": None,
                        "output_path": None,
                        "error": "design submission unavailable",
                    }
                },
                {},
            )
        analysis_type = {
            "protein_design": "protein_design_analysis",
            "promoter_design": "promoter_analysis",
        }[work_item_key]
        context = deep_genome_routing.build_analysis_context(
            analysis_type=analysis_type,
            species_code=state["species_code"],
            gene_id=state["target_gene"],
            output_dir=submission.output_dir,
        )
        run_identity = RunIdentity.create(
            user_id=self.deep_genome_config.USER_ID,
            scope=work_item_key,
        )
        await tracking.accept_remote_submission(work_item_key, submission)
        outcome, results_dir = await self._poll_remote_submission(
            submission,
            context,
            run_identity,
            summary_builder=lambda path: build_design_work_item_summary(
                work_item_key, path
            ),
            tracking=tracking,
            work_item_key=work_item_key,
        )
        summary_data: dict[str, Any] = {}
        if outcome.status == "succeeded" and results_dir is not None:
            display_order = next(
                (
                    item.get("display_order")
                    for item in state.get("work_items", [])
                    if item.get("work_item_key") == work_item_key
                ),
                None,
            )
            try:
                summary_data = self._generate_sub_summary(
                    analysis_type=analysis_type,
                    gene_id=state["target_gene"],
                    state=state,
                    results_dir=results_dir,
                    display_order_override=display_order,
                )
            except _DESIGN_SUMMARY_ERRORS:
                outcome = await tracking.persist_work_item_transition(
                    work_item_key,
                    submission,
                    "failed",
                    None,
                    "analysis summary generation failed",
                )
        delta = _outcome_work_item_delta(
            work_item_key=work_item_key,
            submission=submission,
            outcome=outcome,
            state=state,
            summary_data=summary_data or None,
        )
        return delta["raw_analyst_data"], summary_data

    # pylint: enable=too-many-locals

    async def _finalize_design_submissions(
        self: Any,
        submissions: dict[str, RemoteSubmission | None],
        state: DeepGenomeState,
    ) -> dict[str, Any]:
        """Poll both independent Design jobs and merge their state deltas."""
        raw_data: dict[str, Any] = {}
        summaries: dict[str, Any] = {}
        for work_item_key, submission in sorted(submissions.items()):
            raw_delta, summary_delta = await self._poll_design_work_item(
                work_item_key,
                submission,
                state,
            )
            raw_data.update(raw_delta)
            summaries.update(summary_delta)
        delta: dict[str, Any] = {
            "raw_analyst_data": raw_data,
            "analysis_completed_branches": 1,
        }
        if summaries:
            delta["analyst_summaries"] = summaries
        return delta

    async def finalize_design_result(
        self: Any,
        design_output: dict[str, Any] | dict[str, RemoteSubmission | None],
        state: DeepGenomeState,
    ) -> dict:
        """Poll independent Design submissions and project their outcomes."""
        if (
            design_output
            and set(design_output)
            == {
                "protein_design",
                "promoter_design",
            }
            and all(
                value is None or isinstance(value, RemoteSubmission)
                for value in design_output.values()
            )
        ):
            return await self._finalize_design_submissions(
                cast(dict[str, RemoteSubmission | None], design_output),
                state,
            )
        terminal_output = cast(dict[str, Any], design_output)
        task_ids: dict[str, Any] = terminal_output.get("task_ids", {})
        protein_task = next(
            (
                task
                for task in terminal_output.get("design_task_result", [])
                if str(task.get("task_id"))
                == str(task_ids.get("protein_design"))
            ),
            None,
        )
        if protein_task is None:
            raise RuntimeError("design mount produced no protein-design task")
        self._raise_if_agent_failed(protein_task)
        output_path = protein_task.get("output_dir")
        if not isinstance(output_path, str):
            raise RuntimeError("design mount returned no output directory")
        run_identity = RunIdentity.create(
            user_id=self.deep_genome_config.USER_ID,
            scope="protein_design_analysis",
        )
        context = deep_genome_routing.build_analysis_context(
            analysis_type="protein_design_analysis",
            species_code=state["species_code"],
            gene_id=state["target_gene"],
            output_dir=output_path,
        )
        results_dir = await self._download_analysis_result(
            context, output_path, run_identity
        )
        sub_summary = self._generate_sub_summary(
            analysis_type="protein_design_analysis",
            gene_id=state["target_gene"],
            state=state,
            results_dir=results_dir,
        )
        return {
            "raw_analyst_data": {
                f"task_{state.get('task_index')}": {
                    "status": "success",
                    "analysis_type": "digital_design",
                    "task_id": protein_task.get("task_id"),
                    "output_path": output_path,
                }
            },
            "analyst_summaries": sub_summary,
            "analysis_completed_branches": 1,
        }

    def _generate_sub_summary(
        self: Any,
        analysis_type: str,
        gene_id: str,
        state: DeepGenomeState,
        results_dir: str | None = None,
        display_order_override: int | None = None,
    ) -> dict:
        """Generate sub-summary for a specific analysis type."""
        display_order = display_order_override
        if display_order is None:
            display_order = cast(int | None, state.get("display_order"))
        if display_order is None:
            work_item_key = state.get("work_item_key")
            if work_item_key is None:
                work_item_key = analysis_type
            for item in state.get("work_items", []):
                if item.get("work_item_key") == work_item_key or (
                    item.get("analysis_type") == analysis_type
                    and item.get("section_key") != "digital_design"
                ):
                    display_order = item.get("display_order")
                    break
        figure_index = (
            int(display_order) + 1
            if isinstance(display_order, int) and display_order >= 0
            else 1
        )
        result = build_sub_summary(
            analysis_type=analysis_type,
            gene_id=gene_id,
            deepgenome_out=self.deep_genome_config.DEEPGENOME_OUT,
            data=state.get("analyst_summaries"),
            figure_index=figure_index,
            results_dir=results_dir,
            display_order=display_order,
        )
        return result.data

    async def _bi_json(self: Any, sql: str) -> dict[str, Any]:
        """Query GaussDB and return the parsed JSON payload."""
        if relay_mode_enabled():
            return await relay_bi_query(sql, message="BI query failed")
        return await gauss_query(sql)

    async def _resolve_analysis_gene_id(
        self: Any,
        species_code: str,
        gene_id: str,
    ) -> str:
        """Resolve the species-specific id required by expression tables."""
        column = _RESOLVED_GENE_ID_COLUMNS.get(species_code)
        if column is None:
            return (
                gene_id.replace("_", "") if species_code == "gma" else gene_id
            )
        response = await self._bi_json(
            f"SELECT * FROM id_table WHERE gene_id = "
            f"{sql_literal(gene_id)} AND species_code = '{species_code}'"
        )
        return response["data"][0][column]

    def _seed_analysis_plan(
        self: Any,
        state: DeepGenomeState,
        plan: tuple[WorkItemSpec, ...],
    ) -> None:
        """Persist the plan when the graph carries reservation data."""
        task_id = state.get("task_id")
        run_id = state.get("run_id")
        if not (
            isinstance(task_id, str)
            and isinstance(run_id, str)
            and task_id.strip()
            and run_id.strip()
        ):
            return
        store = DeepGenomeStore(resolve_tasks_db_path())
        store.seed_plan(
            DeepGenomeReservation(
                run_id=run_id,
                umbrella_task_id=task_id,
                owner=str(
                    state.get("owner")
                    or getattr(self.deep_genome_config, "USER_ID", "anonymous")
                ),
                output_dir=str(state.get("output_dir") or ""),
            ),
            plan,
        )

    async def _prepare_analysis_tasks(self: Any, state: DeepGenomeState):
        """Initialize analysis tasks for parallel execution.

        The immutable work-item plan owns the concrete analysis universe.
        ``analysis_tasks`` remains the eleven-entry logical branch list used
        by the existing LangGraph barrier, while ``work_items`` carries all
        twelve serialized concrete jobs (protein and promoter design are two
        jobs under the one ``digital_design`` section).

        Args:
            state: Current workflow state containing gene_id and species_code.

        Returns:
            Dict with the legacy logical ``analysis_tasks`` list and the
            serialized concrete ``work_items`` list.
        """
        gene_id = state["gene_id"]
        species_code = state.get("species_code", "")
        resolved_gene_id = (
            await DeepGenomeDispatchMixin._resolve_analysis_gene_id(
                self,
                species_code,
                gene_id,
            )
        )
        plan = build_work_item_plan(species_code, gene_id, resolved_gene_id)
        DeepGenomeDispatchMixin._seed_analysis_plan(self, state, plan)
        return {
            "analysis_tasks": build_logical_analysis_tasks(
                plan,
                species_code,
                gene_id,
            ),
            "work_items": [asdict(item) for item in plan],
        }

    async def _poll_remote_submission(
        self: Any,
        submission: RemoteSubmission,
        context: AnalysisDispatchContext,
        run_identity: RunIdentity,
        **options: Unpack[_DispatchPollOptions],
    ) -> tuple[WorkItemOutcome, str | None]:
        """Poll and resolve one accepted submission through the adapter."""
        remote_io = DeepGenomeDispatchMixin._remote_io_for(self)
        override = self.__dict__.get("_download_analysis_result")
        if override is not None:
            remote_io.hooks["download_result"] = override
        return await remote_io.poll_remote_submission(
            submission,
            context,
            run_identity,
            **options,
            transition_sink_factory=lambda: self._transition_sink(None),
        )

    async def _resolve_remote_analysis(
        self: Any,
        submission: RemoteSubmission,
        context: AnalysisDispatchContext,
        run_identity: RunIdentity,
        tracking: DeepGenomeTransitionSink,
        work_item_key: str,
    ) -> tuple[object, str, str]:
        """Track, poll, and resolve one caller-owned remote submission."""
        await tracking.accept_remote_submission(work_item_key, submission)
        outcome, results_dir = await self._poll_remote_submission(
            submission,
            context,
            run_identity,
            tracking=tracking,
            work_item_key=work_item_key,
        )
        if outcome.status != "succeeded":
            raise RuntimeError(
                outcome.failure_reason or "analysis task failed"
            )
        if results_dir is None:
            raise RuntimeError("analysis result resolution failed")
        return submission.submitted_task_id, submission.output_dir, results_dir

    async def _resolve_direct_analysis(
        self: Any,
        result: dict,
        context: AnalysisDispatchContext,
        analysis_type: str,
        run_identity: RunIdentity,
    ) -> tuple[object, str, str]:
        """Validate and download a direct AnalystAgent result."""
        self._raise_if_agent_failed(result)
        task_id = result.get("task_id")
        output_path = result.get("output_dir")
        if not isinstance(output_path, str):
            raise RuntimeError("AnalystAgent returned no output directory")
        logger.info("Preparing %s results", analysis_type)
        results_dir = await self._download_analysis_result(
            context,
            output_path,
            run_identity,
        )
        return task_id, output_path, results_dir

    async def _dispatch_and_wait_analysis(
        self: Any,
        analysis_type: str,
        species_code: str,
        gene_id: str,
        output_dir: str | None = None,
        state: DeepGenomeState | None = None,
    ) -> dict:
        """Submit an analysis task, poll it, and download its result."""
        run_identity = RunIdentity.create(
            user_id=self.deep_genome_config.USER_ID,
            scope=analysis_type,
        )
        context = deep_genome_routing.build_analysis_context(
            analysis_type=analysis_type,
            species_code=species_code,
            gene_id=gene_id,
            output_dir=output_dir or "",
        )
        tracking = DeepGenomeDispatchMixin._transition_sink(self, state)
        work_item_key = analysis_type
        if state is not None and isinstance(state.get("work_item_key"), str):
            work_item_key = cast(str, state.get("work_item_key"))
        logger.info("Submitting %s task via AnalystAgent", analysis_type)

        try:
            result = await self._submit_analysis_task(context)
            if isinstance(result, RemoteSubmission):
                task_id, output_path, results_dir = (
                    await DeepGenomeDispatchMixin._resolve_remote_analysis(
                        self,
                        result,
                        context,
                        run_identity,
                        tracking,
                        work_item_key,
                    )
                )
            else:
                task_id, output_path, results_dir = (
                    await DeepGenomeDispatchMixin._resolve_direct_analysis(
                        self,
                        result,
                        context,
                        analysis_type,
                        run_identity,
                    )
                )
        except DeepGenomeTrackingError:
            raise
        except _BEST_EFFORT_ERRORS:
            if state is not None:
                await tracking.record_work_item_failure(work_item_key)
            raise
        logger.info("%s task completed (task_id: %s)", analysis_type, task_id)
        return {
            "task_id": task_id,
            "output_path": output_path,
            "results_dir": results_dir,
            "status": "completed",
        }

    def _analysis_prompt_parts(
        self: Any,
        context: AnalysisDispatchContext,
    ) -> tuple[str, dict[str, Any], str, str]:
        """Build goal, data list, meta prompt, and compute resource."""
        goal, data_list, meta, compute_resource = (
            deep_genome_routing.build_analysis_prompt_parts(
                context,
                prompt_file=self.deep_genome_config.PROMPT_FILE,
                data_file=self.deep_genome_config.DEEPGENOME_DATA,
            )
        )
        return (
            goal,
            data_list,
            append_artifact_manifest_contract(meta),
            compute_resource,
        )

    async def _submit_analysis_task(
        self: Any,
        context: AnalysisDispatchContext,
    ) -> dict | RemoteSubmission:
        """Submit one resolved analysis task through the remote adapter."""
        return await DeepGenomeDispatchMixin._remote_io_for(
            self
        ).submit_analysis_task(
            context,
            prompt_parts=self._analysis_prompt_parts,
        )

    @staticmethod
    def _raise_if_agent_failed(result: dict) -> None:
        """Raise when AnalystAgent returned an agent-level failure state."""
        if result.get("task_status") == "FAILED_AT_AGENT_LEVEL":
            raise RuntimeError(
                f"AnalystAgent failed: {result.get('error_detail')}"
            )

    async def _download_analysis_result(
        self: Any,
        context: AnalysisDispatchContext,
        output_path: str,
        run_identity: RunIdentity,
    ) -> str:
        """Return a readable result directory through the remote adapter."""
        return await DeepGenomeDispatchMixin._remote_io_for(
            self
        ).download_analysis_result(
            context,
            output_path,
            run_identity,
        )

    def _obsfs_analysis_result_dir(self: Any, output_path: str) -> str | None:
        """Return the obsfs result directory when it is directly readable."""
        return DeepGenomeDispatchMixin._remote_io_for(
            self
        ).obsfs_analysis_result_dir(output_path)

    def _chat_kwargs(self: Any) -> dict[str, Any]:
        """Return shared Phyto chat kwargs for report nodes."""
        return {
            "prompt_file": self.deep_genome_config.PROMPT_FILE,
            "prompt_path": self.deep_genome_config.PROMPT_PATH,
            "api_key": self.sensitive_config.API_KEY.get_secret_value(),
            "base_url": self.sensitive_config.BASE_URL,
            "model": self.sensitive_config.MODEL_ID,
            "frequency_penalty": self.deep_genome_config.FREQUENCY_PENALTY,
            "n": self.deep_genome_config.N,
            "presence_penalty": self.deep_genome_config.PRESENCE_PENALTY,
            "reasoning_effort": self.deep_genome_config.REASONING_EFFORT,
            "response_format": self.deep_genome_config.RESPONSE_FORMAT,
            "stream": self.deep_genome_config.STREAM,
            "temperature": self.deep_genome_config.TEMPERATURE,
            "top_p": self.deep_genome_config.TOP_P,
            "user": self.deep_genome_config.USER,
            "timeout": self.deep_genome_config.TIMEOUT,
            "retriable_codes": self.deep_genome_config.RETRIABLE_CODES,
            "max_retries": self.deep_genome_config.MAX_RETRIES,
        }
