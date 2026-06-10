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
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, NamedTuple, Optional

from langgraph.graph import END
from langgraph.types import Send

from ...common.httpx_client import get_async_client
from ...common.prompts import get_prompt
from ...config.relay_mode import relay_mode_enabled
from ...graphs.analyst_dispatch_adapters import submit_analyst_via_subgraph
from ...runtime.langgraph_runner import capture_workflow_boundary
from ...runtime.workflow_mixins import WorkflowMixinBase
from ...storage.obs_storage import normalize_obs_object_key, obsfs_path_for
from ...storage.path_policy import RunIdentity
from ...storage.scratch import ScratchTarget, resolve_scratch_dir
from ..analyst.storage import download_obs_out
from ..design.agent import (
    promoter_design_for_gene,
    protein_structure_for_gene,
)
from ..evolution.agent import evolution_analysis_for_gene
from ..shared.analysis_storage import (
    ensure_run_output_dir,
    get_data_list,
)
from ..shared.sql import relay_bi_query, sql_literal
from .summary import build_sub_summary

if TYPE_CHECKING:
    from .agent import DeepGenomeState
else:
    DeepGenomeState = Dict[str, Any]

logger = logging.getLogger(__name__)

# epic_analysis is intentionally kept registered in all four lookup
# maps below (goal template / meta template / data list / target file
# feature) but is NOT enumerated by ``prepare_tasks_node`` so the
# dispatcher never instantiates it. Activation requires three
# coordinated changes that must land in one commit:
#   1. Add ``user/epic_analysis`` and ``user/epic_analysis_meta`` keys
#      to ``config/.prompts.yaml`` with real EPIC tool spec (conda
#      env, model paths, run script args). SMEP/SMOC are sibling
#      templates and use the ``epic`` conda env, but EPIC's
#      umbrella shape is different — do not synthesise content from
#      siblings.
#   2. Insert an ``{"analysis_type": "epic_analysis", ...}`` task entry
#      into ``prepare_tasks_node`` alongside the existing 9 tasks.
#   3. Confirm the integration team's EPIC tool is reachable from the
#      compute env the analyst plan executes in.
# Until then the registration is latent: any caller that adds an
# epic_analysis task without the above coordination raises KeyError
# at prompt resolution time in ``_analysis_prompt_parts``.

ANALYSIS_GOAL_TEMPLATE_MAP = {
    "evolution_analysis": "user/evolution_analysis",
    "haplotypes_analysis": "user/haplotypes_analysis",
    "fst_analysis": "user/fst_analysis",
    "enrichment_analysis": "user/enrichment_analysis",
    "protein_structure_analysis": "user/structure_analysis",
    "promoter_analysis": "user/promoter_analysis",
    "gene_expression_tissues": "user/gene_expression_analysis/tissue",
    "gene_expression_cultivars": "user/gene_expression_analysis/cultivar",
    "gene_expression_genotypes": "user/gene_expression_analysis/genotype",
    "gene_expression_treatments": "user/gene_expression_analysis/treatment",
    "single_cell_analysis": "user/single_cell_analysis",
    "smep_analysis": "user/smep_analysis",
    "smoc_analysis": "user/smoc_analysis",
    "epic_analysis": "user/epic_analysis",
    "gene_expression_analysis": "user/gene_expression_analysis",
}

ANALYSIS_META_TEMPLATE_MAP = {
    "evolution_analysis": "user/evolution_analysis_meta",
    "haplotypes_analysis": "user/haplotypes_analysis_meta",
    "fst_analysis": "user/fst_analysis_meta",
    "enrichment_analysis": "user/enrichment_analysis_meta",
    "protein_structure_analysis": "user/structure_analysis_meta",
    "promoter_analysis": "user/promoter_analysis_meta",
    "gene_expression_tissues": "user/gene_expression_analysis_meta",
    "gene_expression_cultivars": "user/gene_expression_analysis_meta",
    "gene_expression_genotypes": "user/gene_expression_analysis_meta",
    "gene_expression_treatments": "user/gene_expression_analysis_meta",
    "single_cell_analysis": "user/single_cell_analysis_meta",
    "smep_analysis": "user/smep_analysis_meta",
    "smoc_analysis": "user/smoc_analysis_meta",
    "epic_analysis": "user/epic_analysis_meta",
    "gene_expression_analysis": "user/gene_expression_analysis_meta",
}

ANALYSIS_DATA_LIST_MAP = {
    "evolution_analysis": "evolution_analysis",
    "haplotypes_analysis": "haplotypes_analysis",
    "fst_analysis": "fst_analysis",
    "enrichment_analysis": "enrichment_analysis",
    "protein_structure_analysis": "structure_analysis",
    "promoter_analysis": "promoter_analysis",
    "gene_expression_tissues": "gene_expression_analysis/tissues",
    "gene_expression_cultivars": "gene_expression_analysis/cultivars",
    "gene_expression_genotypes": "gene_expression_analysis/genotypes",
    "gene_expression_treatments": "gene_expression_analysis/treatments",
    "single_cell_analysis": "single_cell_analysis",
    "smep_analysis": "promoter_analysis",
    "smoc_analysis": "promoter_analysis",
    "epic_analysis": "promoter_analysis",
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
    "epic_analysis": [".png", ".summary", ".legend"],
    "gene_expression_analysis": [".png", ".summary", ".legend"],
}

DEFAULT_TARGET_FILE_FEATURE = [".png", ".summary", ".legend"]
MEDIUM_COMPUTE_ANALYSIS_TYPES = {
    "protein_structure_analysis",
    "evolution_analysis",
}


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
            knowledge_node, data_node, and prepare_tasks_node.
        """
        # M11 (X3b A architecture) — ``data_node`` deleted; brief_gene
        # mount inside ``knowledge_node`` performs all the BI
        # annotation + homology + interaction fetching that the
        # legacy ``data_node`` + 3-branch fan-out used to produce.
        # ``use_data_agent`` flag is subsumed by the mount.
        use_analyst = state.get("config_params", {}).get(
            "use_analyst_agent", True
        )
        if use_analyst:
            return ["knowledge_node", "prepare_tasks_node"]
        return ["knowledge_node"]

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
        """Dispatch analysis tasks in parallel using Send API.

        Args:
            state: Current workflow state containing analysis_tasks.

        Returns:
            List of Send objects for dynamic task dispatch.
        """
        sleep_time = state.get("task_submit_sleep", 10)
        tasks = state.get("analysis_tasks", [])
        return [
            Send(
                "analyst_node",
                {"task_index": i, "task_submit_sleep": i * sleep_time, **task},
            )
            for i, task in enumerate(tasks)
        ]

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

    def _generate_sub_summary(
        self: Any,
        analysis_type: str,
        gene_id: str,
        state: DeepGenomeState,
        results_dir: Optional[str] = None,
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

    async def _bi_json(self: Any, sql: str) -> Dict[str, Any]:
        """Query the BI SQL endpoint and return the parsed JSON payload."""
        if relay_mode_enabled():
            return await relay_bi_query(sql, message="BI query failed")
        payload = {"sql": sql, "returnType": "json"}
        bi_timeout = self.deep_genome_config.TIMEOUT
        async with get_async_client(timeout=bi_timeout) as client:
            response = await client.post(
                self.deep_genome_config.BI_URL,
                json=payload,
                headers=self._sql_headers,
                timeout=bi_timeout,
            )
        return response.json()

    async def _prepare_analysis_tasks(self: Any, state: DeepGenomeState):
        """Initialize analysis tasks for parallel execution.

        This method prepares 9 analysis tasks for the deep genome analysis
        phase, including evolution analysis, expression analysis across
        tissues/cultivars/treatments/genotypes, single-cell analysis,
        promoter analysis, SMEP, and SMOC.

        Args:
            state: Current workflow state containing gene_id and species_code.

        Returns:
            Dict with analysis_tasks list.
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
        tasks = [
            {
                "target_gene": gene_id,
                "species_code": species_code,
                "analysis_type": "evolution_analysis",
                "compute": "medium",
                "func_name": "evolution_analysis",
            },
            {
                "target_gene": (
                    gene_idv2
                    if species_code in ["osa", "zma", "gma"]
                    else gene_id
                ),
                "species_code": species_code,
                "analysis_type": "gene_expression_tissues",
                "compute": "small",
                "func_name": "gene_expression_tissues",
            },
            {
                "target_gene": (
                    gene_idv2
                    if species_code in ["osa", "zma", "gma"]
                    else gene_id
                ),
                "species_code": species_code,
                "analysis_type": "gene_expression_cultivars",
                "compute": "small",
                "func_name": "gene_expression_cultivars",
            },
            {
                "target_gene": (
                    gene_idv2
                    if species_code in ["osa", "zma", "gma"]
                    else gene_id
                ),
                "species_code": species_code,
                "analysis_type": "gene_expression_treatments",
                "compute": "small",
                "func_name": "gene_expression_treatments",
            },
            {
                "target_gene": (
                    gene_idv2
                    if species_code in ["osa", "zma", "gma"]
                    else gene_id
                ),
                "species_code": species_code,
                "analysis_type": "gene_expression_genotypes",
                "compute": "small",
                "func_name": "gene_expression_genotypes",
            },
            {
                "target_gene": gene_id,
                "species_code": species_code,
                "analysis_type": "single_cell_analysis",
                "compute": "small",
                "func_name": "single_cell_analysis",
            },
            {
                "target_gene": gene_id,
                "species_code": species_code,
                "analysis_type": "promoter_analysis",
                "compute": "small",
                "func_name": "promoter_analysis",
            },
            {
                "target_gene": gene_id,
                "species_code": species_code,
                "analysis_type": "smep_analysis",
                "compute": "small",
                "func_name": "smep_analysis",
            },
            {
                "target_gene": gene_id,
                "species_code": species_code,
                "analysis_type": "smoc_analysis",
                "compute": "small",
                "func_name": "smoc_analysis",
            },
        ]
        return {"analysis_tasks": tasks}

    async def _dispatch_and_wait_analysis(
        self: Any,
        analysis_type: str,
        species_code: str,
        gene_id: str,
        output_dir: Optional[str] = None,
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
        resolved_output_dir: str = self._ensure_analysis_output_dir(
            analysis_type,
            output_dir,
            run_identity,
        )
        context = AnalysisDispatchContext(
            analysis_type=analysis_type,
            species_code=species_code,
            gene_id=gene_id,
            output_dir=resolved_output_dir,
        )
        logger.info("Submitting %s task via AnalystAgent", analysis_type)

        result = await self._submit_analysis_task(context)
        self._raise_if_agent_failed(result)

        task_id = result.get("task_id")
        output_path = result.get("output_dir")
        if not isinstance(output_path, str):
            raise RuntimeError("AnalystAgent returned no output directory")
        logger.info("%s task completed (task_id: %s)", analysis_type, task_id)

        logger.info("Preparing %s results", analysis_type)
        results_dir = self._download_analysis_result(
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
        compute_resource = self._get_compute_resource(analysis_type)
        return goal_description, data_list, meta, compute_resource

    def _ensure_analysis_output_dir(
        self: Any,
        analysis_type: str,
        output_dir: Optional[str],
        run_identity: RunIdentity,
    ) -> str:
        """Return an existing or newly created analysis output directory."""
        return ensure_run_output_dir(
            self.deep_genome_config,
            self.sensitive_config,
            f"{analysis_type}_task",
            run_identity,
            output_dir,
        )

    async def _submit_analysis_task(
        self: Any,
        context: AnalysisDispatchContext,
    ) -> dict:
        """Submit one resolved analysis task to AnalystAgent.

        For the three analysis types transferred to the evolution /
        design modules (Step 6.5), branch on the matching
        ``DeepGenomeConfig`` flag and dispatch through the producer
        wrapper. For the remaining types, build the request dict and
        route through ``submit_analyst_via_subgraph``. The shared
        helper internally mints its own run identity via
        ``prepare_analyst_dispatch_context`` so the per-call
        ``run_identity`` argument the caller used to thread through
        has retired.
        """
        analysis_type = context.analysis_type
        config = self.deep_genome_config
        if (
            analysis_type == "evolution_analysis"
            and config.USE_EVOLUTION_SUBGRAPH
        ):
            return await evolution_analysis_for_gene(
                species_code=context.species_code,
                gene_id=context.gene_id,
                output_dir=context.output_dir,
            )
        if (
            analysis_type == "protein_structure_analysis"
            and config.USE_DESIGN_SUBGRAPH
        ):
            return await protein_structure_for_gene(
                species_code=context.species_code,
                gene_id=context.gene_id,
                output_dir=context.output_dir,
            )
        if analysis_type == "promoter_analysis" and config.USE_DESIGN_SUBGRAPH:
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
        return await submit_analyst_via_subgraph(
            self._agents.analyst_agent,
            config,
            self.sensitive_config,
            request,
            is_polling=True,
        )

    @staticmethod
    def _raise_if_agent_failed(result: dict) -> None:
        """Raise when AnalystAgent returned an agent-level failure state."""
        if result.get("task_status") == "FAILED_AT_AGENT_LEVEL":
            raise RuntimeError(
                f"AnalystAgent failed: {result.get('error_detail')}"
            )

    def _download_analysis_result(
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

    def _get_compute_resource(self: Any, analysis_type: str) -> str:
        """Determine compute resource level based on analysis type.

        Analysis types that require more computational resources are assigned
        "medium" compute, while others use "small".

        Args:
            analysis_type: Type of analysis to determine resource level for.

        Returns:
            String: "medium" or "small" based on analysis type.
        """
        if analysis_type in MEDIUM_COMPUTE_ANALYSIS_TYPES:
            return "medium"
        return "small"

    def _chat_kwargs(self: Any) -> Dict[str, Any]:
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
