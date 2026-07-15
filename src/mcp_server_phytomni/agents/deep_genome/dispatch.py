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
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from langgraph.graph import END
from langgraph.types import Send

from ...common.prompts import get_prompt
from ...config.relay_mode import relay_mode_enabled
from ...graphs.analyst_dispatch_adapters import submit_analyst_via_subgraph
from ...runtime.langgraph_runner import capture_workflow_boundary
from ...runtime.workflow_mixins import WorkflowMixinBase
from ...storage.obs_storage import normalize_obs_object_key, obsfs_path_for
from ...storage.path_policy import RunIdentity
from ...storage.scratch import ScratchTarget, resolve_scratch_dir
from ..analyst.storage import download_obs_out, download_obs_out_via_relay
from ..design.agent import (
    promoter_design_for_gene,
    protein_structure_for_gene,
)
from ..shared.analysis_storage import (
    ANALYSIS_DATA_LIST_MAP,
    get_data_list,
)
from ..shared.sql import gauss_query, relay_bi_query, sql_literal
from .coordinator import RemoteSubmission, normalize_submission
from .summary import build_sub_summary
from .work_items import build_work_item_plan, section_keys

if TYPE_CHECKING:
    from .agent import DeepGenomeState
else:
    DeepGenomeState = dict[str, Any]

logger = logging.getLogger(__name__)

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


# The nine generic analysis types that fan out to per-type worker
# nodes (one LangGraph node each, all running ``_run_analyst_node``).
# ``evolution_analysis`` and ``digital_design`` are excluded — they
# mount standalone subgraphs (``evolution_node`` / ``design_node``) and
# keep explicit special-cases in ``_route_analyst_tasks``.
GENERIC_ANALYSIS_NODE_TYPES: tuple[str, ...] = (
    "gene_expression_tissues",
    "gene_expression_cultivars",
    "gene_expression_treatments",
    "gene_expression_genotypes",
    "single_cell_analysis",
    "promoter_analysis",
    "smep_analysis",
    "smoc_analysis",
    "protein_structure_analysis",
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


class DeepGenomeDispatchMixin(WorkflowMixinBase):
    """Routing, dispatch, and analysis-task nodes for DeepGenome."""

    def _route_start(self: Any, state: DeepGenomeState):
        """Determine initial nodes to execute based on config_params.

        This routing function decides which initial nodes to wake up based on
        the use_analyst_agent and use_data_agent configuration flags.

        Args:
            state: Current workflow state containing config_params.

        Returns:
            List of node names to execute. When both flags are True, run
            brief_gene_node, data_node, and prepare_tasks_node.
        """
        # M11 (X3b A architecture) — ``data_node`` deleted; brief_gene
        # mount inside ``brief_gene_node`` performs all the BI
        # annotation + homology + interaction fetching that the
        # legacy ``data_node`` + 3-branch fan-out used to produce.
        # ``use_data_agent`` flag is subsumed by the mount.
        use_analyst = state.get("config_params", {}).get(
            "use_analyst_agent", True
        )
        if use_analyst:
            return ["brief_gene_node", "prepare_tasks_node"]
        return ["brief_gene_node"]

    def _route_synthesize_barrier(self: Any, state: DeepGenomeState):
        """Route back to synthesize_node while waiting for analysis tasks.

        Args:
            state: Current workflow state.

        Returns:
            "synthesize_node" to re-enter the barrier check,
            or END/"experiment_node" when synthesis is complete.
        """
        if state.get("synthesis_waiting"):
            return "synthesize_node"
        if state.get("synthesize_report"):
            return "experiment_node"
        return END

    def _route_experiment_barrier(self: Any, state: DeepGenomeState):
        """Route back to experiment_node while waiting for both branches.

        Args:
            state: Current workflow state.

        Returns:
            "experiment_node" to re-enter the barrier check,
            or "protocol_node" when experiment report is ready.
        """
        if state.get("experiment_waiting"):
            return "experiment_node"
        if state.get("report_triggered"):
            return "protocol_node"
        return END

    def _route_analyst_tasks(self: Any, state: DeepGenomeState):
        """Dispatch analysis tasks in parallel using the Send API.

        The evolution task fans to the dedicated ``evolution_node`` and
        the digital_design task to the mounted ``design_node`` (both
        mounted standalone subgraphs); every other task fans to the
        generic ``analyst_node``. Both stay entries in ``analysis_tasks``
        so the synthesize barrier's ``total_expected`` count is unchanged.

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
            sends.append(
                Send(
                    node,
                    {
                        "task_index": i,
                        "task_submit_sleep": i * sleep_time,
                        **task,
                    },
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

        return await capture_workflow_boundary(run_analysis, failure_state)

    async def finalize_evolution_result(
        self: Any,
        task: dict | None,
        state: DeepGenomeState,
    ) -> dict:
        """Turn a submitted evolution task into an analyst-branch delta.

        Shared tail of the evolution mount node: it runs the same
        agent-failure check, result download, and ``build_sub_summary``
        that ``_run_analyst_node`` runs for the generic types, then
        contributes the ``analysis_completed_branches: 1`` the synthesize
        barrier counts. ``task`` is ``None`` when taxonomy resolution
        produced no task; that raises so the mount node's degraded path
        records a failed branch and the barrier still advances. The
        species, gene, and Send index are read from ``state`` (the Send
        payload the mount node received) so the signature stays within
        the pylint argument budget.

        Args:
            task: Submitted analyst task dict from the mounted evolution
                subgraph, or ``None`` when resolution produced no task.
            state: Send payload carrying ``species_code`` / ``target_gene``
                / ``task_index``; also passed to the sub-summary builder.

        Returns:
            Analyst-branch state delta (``raw_analyst_data`` /
            ``analyst_summaries`` / ``analysis_completed_branches``).
        """
        species_code = state["species_code"]
        gene_id = state["target_gene"]
        task_index = state.get("task_index")
        if task is None:
            raise RuntimeError(
                "evolution mount produced no task (taxid resolution failed)"
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

    async def finalize_design_result(
        self: Any,
        design_output: dict,
        state: DeepGenomeState,
    ) -> dict:
        """Light §8.2 from the mounted design graph's protein-design task.

        Correlates the ``protein_design`` task id to its result entry,
        downloads it, and builds the protein-design sub-summary. The
        promoter-design task is submitted but NOT summarized here -- its
        §8.1 rendering is a tracked follow-up (the restored protocol emits
        no summary artifact yet). Contributes the single barrier branch.

        Args:
            design_output: Final state of the mounted DigitalDesignAgents
                graph (``task_ids`` + ``design_task_result``).
            state: Send payload carrying ``species_code`` / ``target_gene``
                / ``task_index``; also passed to the sub-summary builder.

        Returns:
            Analyst-branch state delta (``raw_analyst_data`` /
            ``analyst_summaries`` / ``analysis_completed_branches``).
        """
        species_code = state["species_code"]
        gene_id = state["target_gene"]
        task_index = state.get("task_index")
        task_ids = design_output.get("task_ids", {})
        protein_id = task_ids.get("protein_design")
        results = design_output.get("design_task_result", [])
        protein_task = next(
            (t for t in results if str(t.get("task_id")) == str(protein_id)),
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
            species_code=species_code,
            gene_id=gene_id,
            output_dir=output_path,
        )
        results_dir = await self._download_analysis_result(
            context, output_path, run_identity
        )
        sub_summary = self._generate_sub_summary(
            analysis_type="protein_design_analysis",
            gene_id=gene_id,
            state=state,
            results_dir=results_dir,
        )
        return {
            "raw_analyst_data": {
                f"task_{task_index}": {
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
    ) -> dict:
        """Generate sub-summary for a specific analysis type."""
        result = build_sub_summary(
            analysis_type=analysis_type,
            gene_id=gene_id,
            deepgenome_out=self.deep_genome_config.DEEPGENOME_OUT,
            data=state.get("analyst_summaries"),
            figure_index=self._figure_index,
            results_dir=results_dir,
        )
        self._figure_index = result.figure_index
        return result.data

    async def _bi_json(self: Any, sql: str) -> dict[str, Any]:
        """Query GaussDB and return the parsed JSON payload."""
        if relay_mode_enabled():
            return await relay_bi_query(sql, message="BI query failed")
        return await gauss_query(sql)

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

    async def _dispatch_and_wait_analysis(
        self: Any,
        analysis_type: str,
        species_code: str,
        gene_id: str,
        output_dir: str | None = None,
    ) -> dict:
        """Submit analysis task using AnalystAgent and wait for completion.

        This method constructs the appropriate prompts and data lists for the
        specified analysis type, submits the task to the AnalystAgent, waits
        for completion, and downloads the results.

        Args:
            analysis_type: Analysis type name (e.g., "evolution_analysis").
            species_code: Three-letter species code for the analysis.
            gene_id: Gene identifier for the analysis.
            output_dir: Optional output directory path.

        Returns:
            Dict containing task_id and output_path.
        """
        run_identity = RunIdentity.create(
            user_id=self.deep_genome_config.USER_ID,
            scope=analysis_type,
        )
        # The dispatch seam (ensure_analysis_output_dir) creates the
        # tenant-neutral shared dir from the input fingerprint, overriding
        # any preset, so pre-creating a user-scoped dir here would only
        # leave an unused marker. Pass the caller's output_dir through
        # (empty for the generic path) and let the seam create the dir.
        context = AnalysisDispatchContext(
            analysis_type=analysis_type,
            species_code=species_code,
            gene_id=gene_id,
            output_dir=output_dir or "",
        )
        logger.info("Submitting %s task via AnalystAgent", analysis_type)

        result = await self._submit_analysis_task(context)
        if isinstance(result, RemoteSubmission):
            task_id = result.submitted_task_id
            output_path = result.output_dir
        else:
            self._raise_if_agent_failed(result)
            task_id = result.get("task_id")
            output_path = result.get("output_dir")
        if not isinstance(output_path, str):
            raise RuntimeError("AnalystAgent returned no output directory")
        logger.info("%s task completed (task_id: %s)", analysis_type, task_id)

        logger.info("Preparing %s results", analysis_type)
        results_dir = await self._download_analysis_result(
            context, output_path, run_identity
        )
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
        # The medium-tier analysis types (evolution / protein structure)
        # moved to the evolution and design producer modules, which set
        # their own compute tier; every type that still reaches this
        # dispatcher runs on the small tier.
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
            return await protein_structure_for_gene(
                species_code=context.species_code,
                gene_id=context.gene_id,
                output_dir=context.output_dir,
            )
        if analysis_type == "promoter_analysis":
            return await promoter_design_for_gene(
                species_code=context.species_code,
                gene_id=context.gene_id,
                output_dir=context.output_dir,
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
