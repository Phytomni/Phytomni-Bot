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

# The dispatch mixin is the single DeepGenome coordinator boundary; keeping
# routing, submission, polling, and result projection together preserves one
# owner-scoped transition seam. See the lint-exemption catalog.
# pylint: disable=too-many-lines

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from langgraph.graph import END
from langgraph.types import Send

from ...common.prompts import get_prompt
from ...config.relay_mode import relay_mode_enabled
from ...graphs.analyst_dispatch_adapters import submit_analyst_via_subgraph
from ...runtime.deep_genome_store import (
    DeepGenomeReservation,
    DeepGenomeStore,
    DeepGenomeTrackingError,
)
from ...runtime.task_manager import resolve_tasks_db_path
from ...storage.obs_storage import normalize_obs_object_key, obsfs_path_for
from ...storage.path_policy import RunIdentity
from ...storage.scratch import ScratchTarget, resolve_scratch_dir
from ..analyst.storage import download_obs_out, download_obs_out_via_relay
from ..analyst.task_ops import task_delete, task_status
from ..design.agent import (
    promoter_design_for_gene,
    protein_structure_for_gene,
)
from ..shared.analysis_storage import (
    ANALYSIS_DATA_LIST_MAP,
    get_data_list,
)
from ..shared.sql import gauss_query, relay_bi_query, sql_literal
from .coordinator import (
    DeepGenomeWorkflowError,
    RemoteSubmission,
    WorkItemOutcome,
    normalize_submission,
    poll_work_item,
    workflow_outcome_for_state,
)
from .summary import build_design_work_item_summary, build_sub_summary
from .tracking import DeepGenomeTransitionSink
from .work_items import build_work_item_plan, section_keys

if TYPE_CHECKING:
    from .agent import DeepGenomeState
else:
    DeepGenomeState = dict[str, Any]

logger = logging.getLogger(__name__)

_BEST_EFFORT_ERRORS: tuple[type[Exception], ...] = (Exception,)

ANALYSIS_GOAL_TEMPLATE_MAP = {
    "haplotypes_analysis": "user/haplotypes_analysis",
    "fst_analysis": "user/fst_analysis",
    "enrichment_analysis": "user/enrichment_analysis",
    "gene_expression_tissues": "user/gene_expression_analysis/tissue",
    "gene_expression_cultivars": "user/gene_expression_analysis/cultivar",
    "gene_expression_genotypes": "user/gene_expression_analysis/genotype",
    "gene_expression_treatments": "user/gene_expression_analysis/treatment",
    "single_cell_analysis": "user/single_cell_analysis",
    "smep_analysis": "user/smep_analysis",
    "smoc_analysis": "user/smoc_analysis",
}

ANALYSIS_META_TEMPLATE_MAP = {
    "haplotypes_analysis": "user/haplotypes_analysis_meta",
    "fst_analysis": "user/fst_analysis_meta",
    "enrichment_analysis": "user/enrichment_analysis_meta",
    "gene_expression_tissues": "user/gene_expression_analysis_meta",
    "gene_expression_cultivars": "user/gene_expression_analysis_meta",
    "gene_expression_genotypes": "user/gene_expression_analysis_meta",
    "gene_expression_treatments": "user/gene_expression_analysis_meta",
    "single_cell_analysis": "user/single_cell_analysis_meta",
    "smep_analysis": "user/smep_analysis_meta",
    "smoc_analysis": "user/smoc_analysis_meta",
}

ANALYSIS_TARGET_FILE_FEATURE_MAP = {
    "evolution_analysis": [".md", ".png", ".summary", ".legend"],
    "haplotypes_analysis": [".png", ".summary", ".legend"],
    "fst_analysis": [".png"],
    "enrichment_analysis": [".png", ".summary", ".legend"],
    "protein_structure_analysis": ["sample_0.cif", ".summary", ".legend"],
    "promoter_analysis": ["motif_all_logo.png", ".summary", ".legend"],
    "gene_expression_tissues": [".png", ".summary", ".legend"],
    "gene_expression_cultivars": [".png", ".summary", ".legend"],
    "gene_expression_genotypes": [".png", ".summary", ".legend"],
    "gene_expression_treatments": [".png", ".summary", ".legend"],
    "single_cell_analysis": [".png", ".summary", ".legend"],
    "smep_analysis": [".png", ".summary", ".legend"],
    "smoc_analysis": [".png", ".summary", ".legend"],
}

DEFAULT_TARGET_FILE_FEATURE = [".png", ".summary", ".legend"]
_DESIGN_SUMMARY_ERRORS: tuple[type[Exception], ...] = (Exception,)


def _read_result_markdown(results_dir: str) -> str:
    """Read deterministic nonblank Markdown summaries from a result dir."""
    root = Path(results_dir)
    summaries: list[str] = []
    for summary_path in sorted(root.rglob("*.summary")):
        if not summary_path.is_file():
            continue
        content = summary_path.read_text(encoding="utf-8").strip()
        if content:
            summaries.append(content)
    return "\n\n".join(summaries)


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


# Generic types use worker nodes; evolution/design use mounted subgraphs.
# Keep this ordered source aligned with the work-item plan and its test oracle.
_GENERIC_ANALYSIS_NODE_TYPE_SOURCE = (
    "gene_expression_tissues|gene_expression_cultivars|"
    "gene_expression_treatments|gene_expression_genotypes|"
    "single_cell_analysis|promoter_analysis|smep_analysis|smoc_analysis|"
    "protein_structure_analysis"
)
GENERIC_ANALYSIS_NODE_TYPES: tuple[str, ...] = tuple(
    _GENERIC_ANALYSIS_NODE_TYPE_SOURCE.split("|")
)


def _analyst_node_name(analysis_type: str) -> str:
    """Map a generic ``analysis_type`` to its LangGraph node name.

    Deterministic: strip a trailing ``_analysis`` and append ``_node``
    (``smep_analysis`` -> ``smep_node``; ``gene_expression_tissues`` ->
    ``gene_expression_tissues_node``). Yields nine distinct names for
    :data:`GENERIC_ANALYSIS_NODE_TYPES`. It also maps
    ``evolution_analysis`` -> ``evolution_node`` (the SP1 mount), but
    ``digital_design`` has no ``_analysis`` suffix and keeps its explicit
    ``design_node`` special-case in
    :meth:`DeepGenomeDispatchMixin._route_analyst_tasks`.
    """
    return analysis_type.removesuffix("_analysis") + "_node"


class AnalysisDispatchContext(NamedTuple):
    """Resolved request context for one deep analysis task.

    Attributes:
        analysis_type: DeepGenome analysis task type.
        species_code: Three-letter species code used in get_data_list lookup.
        gene_id: Target gene identifier.
        output_dir: OBS output directory for task results.
    """

    analysis_type: str
    species_code: str
    gene_id: str
    output_dir: str


class DeepGenomeDispatchMixin:
    """Routing, dispatch, and analysis-task nodes for DeepGenome.
    Dispatch methods share durable lifecycle and remote-submission state."""

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
        values: dict[str, Any] = {}
        for config_name, argument_name in (
            ("ANALYSIS_URL", "analysis_url"),
            ("ANALYSIS_REGION", "region"),
            ("RETRIABLE_CODES", "retriable_codes"),
            ("MAX_RETRIES", "max_retries"),
        ):
            value = getattr(self.deep_genome_config, config_name, None)
            if value is not None:
                values[argument_name] = value
        return values

    async def _cancel_submission(
        self: Any, submission: RemoteSubmission
    ) -> None:
        """Best-effort terminate the caller-owned job after tracking loss.

        A deduplicated ``poll_task_id`` can point at another tenant's source
        job.  It is intentionally never cancelled from this owner-scoped
        failure path; only the accepted caller-owned id is eligible.
        """
        try:
            await task_delete(
                submission.submitted_task_id,
                timeout=getattr(self.deep_genome_config, "TIMEOUT", 600.0),
                **DeepGenomeDispatchMixin._analysis_request_kwargs(self),
            )
        except _BEST_EFFORT_ERRORS as exc:
            logger.warning(
                "DeepGenome cancellation unavailable; error_type=%s",
                type(exc).__name__,
            )

    def _route_start(self: Any, state: DeepGenomeState):
        """Return BriefGene as the only initial node and launch barrier."""
        del state
        return ["brief_gene_node"]

    def _route_after_brief_gene(self: Any, state: DeepGenomeState):
        """Gate analyst preparation on successful BriefGene completion."""
        use_analyst = state.get("config_params", {}).get(
            "use_analyst_agent", True
        )
        if use_analyst:
            return ["prepare_tasks_node", "experiment_node"]
        return "experiment_node"

    def _route_synthesize_barrier(self: Any, state: DeepGenomeState):
        """Route from concrete outcomes and require usable synthesis."""
        if state.get("skip_synthesize"):
            if not state.get("synthesize_report"):
                raise DeepGenomeWorkflowError("final synthesis unavailable")
            return "experiment_node"
        outcome = workflow_outcome_for_state(state)
        if not outcome.all_terminal:
            return "synthesize_node"
        if not outcome.may_synthesize:
            raise DeepGenomeWorkflowError("no usable analysis result")
        if not state.get("synthesize_report"):
            raise DeepGenomeWorkflowError("final synthesis unavailable")
        return "experiment_node"

    def _route_experiment_barrier(self: Any, state: DeepGenomeState):
        """Route only after BriefGene and synthesis are available."""
        use_analyst = state.get("config_params", {}).get(
            "use_analyst_agent", True
        )
        if not use_analyst:
            return (
                "discussion_node"
                if state.get("report_triggered")
                else "experiment_node"
            )
        if not state.get("preamble") or not state.get("synthesize_report"):
            return "experiment_node"
        if state.get("report_triggered"):
            return "protocol_node"
        return "experiment_node"

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
        sleep_time = state.get("task_submit_sleep", 10)
        sends = []
        for i, task in enumerate(state.get("analysis_tasks", [])):
            analysis_type = task.get("analysis_type")
            if analysis_type == "evolution_analysis":
                node = "evolution_node"
            elif analysis_type == "digital_design":
                node = "design_node"
            else:
                node = _analyst_node_name(str(analysis_type))
            send_payload: dict[str, Any] = {
                "task_index": i,
                "task_submit_sleep": i * sleep_time,
                **task,
            }
            # ``Send`` receives a fresh partial state rather than inheriting
            # the parent state. Carry the reserved owner identity into every
            # branch so submission and poll transitions stay scoped to the
            # same DeepGenome umbrella.
            for identity_key in ("task_id", "run_id", "owner", "output_dir"):
                send_payload[identity_key] = state.get(identity_key)
            work_item = next(
                (
                    item
                    for item in state.get("work_items", [])
                    if item.get("section_key") == analysis_type
                    or item.get("work_item_key") == analysis_type
                ),
                None,
            )
            if work_item is not None:
                send_payload.update(
                    {
                        "work_item_key": work_item.get("work_item_key"),
                        "display_order": work_item.get("display_order"),
                    }
                )
            sends.append(
                Send(
                    node,
                    send_payload,
                )
            )
        return sends

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
            context = AnalysisDispatchContext(
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
        context = AnalysisDispatchContext(
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

    # pylint: disable=too-many-locals
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
        context = AnalysisDispatchContext(
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
        context = AnalysisDispatchContext(
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

    # pylint: disable=too-many-locals
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
        match species_code:
            case "osa":
                gene_id_reponse = await self._bi_json(
                    f"SELECT * FROM id_table WHERE gene_id = "
                    f"{sql_literal(gene_id)} AND species_code = 'osa'"
                )
                gene_idv2 = gene_id_reponse["data"][0]["msu_gene_id"]
            case "zma":
                gene_id_reponse = await self._bi_json(
                    f"SELECT * FROM id_table WHERE gene_id = "
                    f"{sql_literal(gene_id)} AND species_code = 'zma'"
                )
                gene_idv2 = gene_id_reponse["data"][0]["v4_id"]
            case "gma":
                gene_idv2 = gene_id.replace("_", "")
            case _:
                gene_idv2 = gene_id
        plan = build_work_item_plan(species_code, gene_id, gene_idv2)
        task_id = state.get("task_id")
        run_id = state.get("run_id")
        if (
            isinstance(task_id, str)
            and isinstance(run_id, str)
            and task_id.strip()
            and run_id.strip()
        ):
            store = DeepGenomeStore(resolve_tasks_db_path())
            store.seed_plan(
                DeepGenomeReservation(
                    run_id=run_id,
                    umbrella_task_id=task_id,
                    owner=str(
                        state.get("owner")
                        or getattr(
                            self.deep_genome_config, "USER_ID", "anonymous"
                        )
                    ),
                    output_dir=str(state.get("output_dir") or ""),
                ),
                plan,
            )
        concrete_items = [asdict(item) for item in plan]
        logical_tasks = []
        for section_key in section_keys(plan):
            section_items = [
                item for item in plan if item.section_key == section_key
            ]
            representative = section_items[0]
            analysis_type = (
                "digital_design"
                if section_key == "digital_design"
                else representative.analysis_type
            )
            logical_tasks.append(
                {
                    "target_gene": (
                        gene_id
                        if section_key == "digital_design"
                        else representative.target_gene
                    ),
                    "species_code": species_code,
                    "analysis_type": analysis_type,
                    "compute": representative.compute_resource,
                    "func_name": analysis_type,
                }
            )
        return {
            "analysis_tasks": logical_tasks,
            "work_items": concrete_items,
        }

    # pylint: enable=too-many-locals

    # pylint: disable=too-many-arguments
    # pylint: disable=too-many-locals
    async def _poll_remote_submission(
        self: Any,
        submission: RemoteSubmission,
        context: AnalysisDispatchContext,
        run_identity: RunIdentity,
        *,
        summary_builder: Callable[[str], str] | None = None,
        tracking: DeepGenomeTransitionSink | None = None,
        work_item_key: str | None = None,
    ) -> tuple[WorkItemOutcome, str | None]:
        """Poll and resolve one accepted submission through the coordinator."""
        resolved_results_dir: str | None = None
        tracked_work_item = work_item_key or context.analysis_type

        async def read_remote_status(
            poll_task_id: str,
            request_timeout: float,
        ) -> Any:
            """Read one remote status through the shared task seam."""
            status_kwargs: dict[str, Any] = {
                "timeout": request_timeout,
                **DeepGenomeDispatchMixin._analysis_request_kwargs(self),
            }
            return await task_status(poll_task_id, **status_kwargs)

        async def resolve_remote_result(
            accepted: RemoteSubmission,
        ) -> str:
            """Download and resolve nonblank local Markdown content."""
            nonlocal resolved_results_dir
            resolved_results_dir = await self._download_analysis_result(
                context,
                accepted.output_dir,
                run_identity,
            )
            if resolved_results_dir is None:
                raise RuntimeError("analysis result directory unavailable")
            resolver = summary_builder
            if (
                resolver is None
                and context.analysis_type == "promoter_analysis"
            ):

                def resolve_promoter(path: str) -> str:
                    """Build the promoter artifact fallback summary."""
                    return build_design_work_item_summary(
                        "promoter_design", path
                    )

                resolver = resolve_promoter
            resolver = resolver or _read_result_markdown
            return resolver(resolved_results_dir)

        async def record_transition(
            status: str,
            summary: str | None,
            failure_reason: str | None,
        ) -> WorkItemOutcome:
            """Persist one local transition before continuing the poll."""
            sink = tracking or DeepGenomeDispatchMixin._transition_sink(
                self, None
            )
            return await sink.persist_work_item_transition(
                tracked_work_item,
                submission,
                status,
                summary,
                failure_reason,
            )

        request_timeout = float(
            getattr(self.deep_genome_config, "TIMEOUT", 600.0)
        )
        poll_interval = float(
            getattr(self.deep_genome_config, "POLL_INTERVAL", 300.0)
        )
        deadline_seconds = float(
            getattr(self.deep_genome_config, "MAX_POLL", 86400.0)
        )
        outcome = await poll_work_item(
            submission,
            status_reader=read_remote_status,
            result_resolver=resolve_remote_result,
            transition_sink=record_transition,
            request_timeout=request_timeout,
            poll_interval=poll_interval,
            deadline_seconds=deadline_seconds,
        )
        return outcome, resolved_results_dir

    # pylint: enable=too-many-arguments,too-many-positional-arguments
    # pylint: enable=too-many-locals

    # pylint: disable=too-many-locals
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
        context = AnalysisDispatchContext(
            analysis_type=analysis_type,
            species_code=species_code,
            gene_id=gene_id,
            output_dir=output_dir or "",
        )
        tracking = DeepGenomeDispatchMixin._transition_sink(self, state)
        work_item_value = (
            state.get("work_item_key") if state is not None else None
        )
        work_item_key = (
            work_item_value
            if isinstance(work_item_value, str)
            else analysis_type
        )
        logger.info("Submitting %s task via AnalystAgent", analysis_type)

        try:
            result = await self._submit_analysis_task(context)
        except DeepGenomeTrackingError:
            raise
        except _BEST_EFFORT_ERRORS:
            if state is not None:
                await tracking.record_work_item_failure(work_item_key)
            raise
        if isinstance(result, RemoteSubmission):
            await tracking.accept_remote_submission(work_item_key, result)
            outcome, resolved_results_dir = await self._poll_remote_submission(
                result,
                context,
                run_identity,
                tracking=tracking,
                work_item_key=work_item_key,
            )
            if outcome.status != "succeeded":
                raise RuntimeError(
                    outcome.failure_reason or "analysis task failed"
                )
            task_id = result.submitted_task_id
            output_path = result.output_dir
            if resolved_results_dir is None:
                raise RuntimeError("analysis result resolution failed")
            results_dir = resolved_results_dir
        else:
            self._raise_if_agent_failed(result)
            task_id = result.get("task_id")
            output_path = result.get("output_dir")
            if not isinstance(output_path, str):
                raise RuntimeError("AnalystAgent returned no output directory")
            logger.info("Preparing %s results", analysis_type)
            results_dir = await self._download_analysis_result(
                context, output_path, run_identity
            )
        if not isinstance(output_path, str):
            raise RuntimeError("AnalystAgent returned no output directory")
        logger.info("%s task completed (task_id: %s)", analysis_type, task_id)
        return {
            "task_id": task_id,
            "output_path": output_path,
            "results_dir": results_dir,
            "status": "completed",
        }

    # pylint: enable=too-many-locals

    def _analysis_prompt_parts(
        self: Any,
        context: AnalysisDispatchContext,
    ) -> tuple[str, dict[str, Any], str, str]:
        """Build goal, data list, meta prompt, and compute resource."""
        analysis_type = context.analysis_type
        goal_path = ANALYSIS_GOAL_TEMPLATE_MAP.get(analysis_type)
        meta_path = ANALYSIS_META_TEMPLATE_MAP.get(analysis_type)
        if not goal_path or not meta_path:
            raise ValueError(f"Unknown analysis type: {analysis_type}")

        goal_description = get_prompt(
            self.deep_genome_config.PROMPT_FILE,
            goal_path,
            {"gene_id": context.gene_id},
        )
        meta = get_prompt(self.deep_genome_config.PROMPT_FILE, meta_path)
        data_json_path = ANALYSIS_DATA_LIST_MAP.get(analysis_type, "")
        if "/" in data_json_path:
            data_json_path, sub_title = data_json_path.split("/")
        else:
            sub_title = None
        data_list = get_data_list(
            self.deep_genome_config.DEEPGENOME_DATA,
            data_json_path,
            context.species_code,
        )
        if sub_title:
            data_list = data_list[sub_title]
        # Evolution/protein structure set medium tier; the rest use small.
        compute_resource = "small"
        return goal_description, data_list, meta, compute_resource

    async def _submit_analysis_task(
        self: Any,
        context: AnalysisDispatchContext,
    ) -> dict | RemoteSubmission:
        """Submit one resolved analysis task to AnalystAgent.

        For the two design analysis types (protein_structure /
        promoter), dispatch through the matching design producer
        wrapper. For the remaining types, build the request dict and
        route through ``submit_analyst_via_subgraph``. The shared
        helper internally mints its own run identity via
        ``prepare_analyst_dispatch_context`` so the per-call
        ``run_identity`` argument the caller used to thread through
        has retired. Evolution is no longer dispatched here; it routes
        to the mounted ``evolution_node``.
        """
        analysis_type = context.analysis_type
        config = self.deep_genome_config
        if analysis_type == "protein_structure_analysis":
            return normalize_submission(
                await protein_structure_for_gene(
                    species_code=context.species_code,
                    gene_id=context.gene_id,
                    output_dir=context.output_dir,
                    is_polling=False,
                )
            )
        if analysis_type == "promoter_analysis":
            return normalize_submission(
                await promoter_design_for_gene(
                    species_code=context.species_code,
                    gene_id=context.gene_id,
                    output_dir=context.output_dir,
                    is_polling=False,
                )
            )
        goal_description, data_list, meta, compute_resource = (
            self._analysis_prompt_parts(context)
        )
        request = {
            "analysis_type": analysis_type,
            "target_id": context.gene_id,
            "output_dir": context.output_dir,
            "prompt_parts": (goal_description, meta, data_list),
            "compute_resource": compute_resource,
        }
        submission = await submit_analyst_via_subgraph(
            self._agents.analyst_agent,
            config,
            self.sensitive_config,
            request,
            is_polling=False,
        )
        return normalize_submission(submission)

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
        """Return a readable result directory, downloading only if needed."""
        obsfs_result_dir = self._obsfs_analysis_result_dir(output_path)
        if obsfs_result_dir is not None:
            return obsfs_result_dir
        obs_output_path = normalize_obs_object_key(
            output_path,
            self.deep_genome_config.BUCKET_NAME,
        )
        scratch_root = resolve_scratch_dir(
            "downloads",
            run_identity,
            context.analysis_type,
            ScratchTarget(
                bucket_name=self.deep_genome_config.BUCKET_NAME,
                local_fallback=Path(self.deep_genome_config.DEEPGENOME_OUT),
            ),
        )
        local_results_dir = Path(scratch_root) / context.gene_id
        target_file_feature = ANALYSIS_TARGET_FILE_FEATURE_MAP.get(
            context.analysis_type,
            DEFAULT_TARGET_FILE_FEATURE,
        )
        if relay_mode_enabled():
            statuses = await download_obs_out_via_relay(
                task_dir=context.gene_id,
                obs_output_path=obs_output_path,
                download_path=scratch_root,
                target_file_feature=target_file_feature,
                if_download_all=False,
            )
            if not statuses:
                raise RuntimeError(
                    "relay returned no analysis results for "
                    f"{obs_output_path}"
                )
            return str(local_results_dir)
        try:
            access_key_id, secret_access_key = (
                self.sensitive_config.obs_credentials()
            )
            deque(
                download_obs_out(
                    task_dir=context.gene_id,
                    obs_output_path=obs_output_path,
                    download_path=scratch_root,
                    access_key_id=access_key_id,
                    secret_access_key=secret_access_key,
                    obs_server=self.deep_genome_config.OBS_SERVER,
                    bucket_name=self.deep_genome_config.BUCKET_NAME,
                    target_file_feature=target_file_feature,
                    if_download_all=False,
                ),
                maxlen=0,
            )
        except OSError as exc:
            logger.warning("Failed to download results (continuing): %s", exc)
        return str(local_results_dir)

    def _obsfs_analysis_result_dir(self: Any, output_path: str) -> str | None:
        """Return the obsfs result directory when it is directly readable."""
        try:
            obsfs_path = obsfs_path_for(
                output_path,
                self.deep_genome_config.BUCKET_NAME,
            )
            if obsfs_path.is_dir():
                return str(obsfs_path)
        except OSError:
            return None
        local_path = Path(output_path)
        if local_path.is_dir():
            return str(local_path)
        return None

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
