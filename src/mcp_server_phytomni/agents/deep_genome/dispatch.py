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

from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, NamedTuple, Optional

import requests
from langgraph.graph import END
from langgraph.types import Send

from ...common.prompts import get_prompt
from ...runtime.langgraph_runner import capture_workflow_boundary
from ...runtime.workflow_mixins import WorkflowMixinBase
from ...storage.obs_storage import normalize_obs_object_key, obsfs_path_for
from ...storage.path_policy import RunIdentity
from ..analyst.storage import download_obs_out
from ..shared.analysis_storage import (
    ensure_run_output_dir,
    get_data_list,
)
from .formatting import SPECIES_CODE_MAP
from .summary import build_sub_summary

if TYPE_CHECKING:
    from .agent import DeepGenomeState
else:
    DeepGenomeState = Dict[str, Any]

ANALYSIS_GOAL_TEMPLATE_MAP = {
    "evolution_analysis": "user/evolution_analysis",
    "haplotypes_analysis": "user/haplotypes_analysis",
    "fst_analysis": "user/fst_analysis",
    "enrichment_analysis": "user/enrichment_analysis",
    "protein_structure_analysis": "user/structure_analysis",
    "promoter_analysis": "user/promoter_analysis",
    "gene_expression_tissues": "user/gene_expression_tissues",
    "gene_expression_cultivars": "user/gene_expression_cultivars",
    "gene_expression_genotypes": "user/gene_expression_genotypes",
    "gene_expression_treatments": "user/gene_expression_treatments",
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
        species: Display species name used in prompts.
        gene_id: Target gene identifier.
        output_dir: OBS output directory for task results.
    """

    analysis_type: str
    species: str
    gene_id: str
    output_dir: str


def _homology_gene_lists(
    response: Dict[str, Any],
    species_code: str,
) -> tuple[List[tuple[str, str]], List[tuple[str, str]]]:
    """Split BI homology rows into ortholog and paralog gene lists."""
    orthologs, paralogs = set(), set()
    for homology in response["data"]:
        gene_pair = (
            homology["homology_species"],
            homology["homology_gene_id"],
        )
        if homology["homology_species"] == species_code:
            paralogs.add(gene_pair)
        else:
            orthologs.add(gene_pair)
    return sorted(orthologs), sorted(paralogs)


def _interaction_gene_list(
    response: Dict[str, Any],
    gene_id: str,
    species_code: str,
) -> List[tuple[str, str]]:
    """Build a deduplicated interaction partner list from BI rows."""
    interactions = set()
    for interaction in response["data"]:
        if interaction["query_gene_id"] == gene_id:
            interactions.add((species_code, interaction["interact_gene_id"]))
        elif interaction["interact_gene_id"] == gene_id:
            interactions.add((species_code, interaction["query_gene_id"]))
    return sorted(interactions)


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
        use_analyst = state.get("config_params", {}).get(
            "use_analyst_agent", True
        )
        use_data = state.get("config_params", {}).get("use_data_agent", True)
        if use_analyst and use_data:
            return ["knowledge_node", "data_node", "prepare_tasks_node"]
        if use_analyst and not use_data:
            return ["knowledge_node", "prepare_tasks_node"]
        if use_data and not use_analyst:
            return ["knowledge_node", "data_node"]
        return ["knowledge_node"]

    def _route_after_knowledge(self: Any, state: DeepGenomeState):
        """Determine path after knowledge_node completes.

        Args:
            state: Current workflow state containing config_params.

        Returns:
            Node name: "gene_annotation_node" if use_data is True,
                       "gene_summary_node" otherwise.
        """
        use_data = state.get("config_params", {}).get("use_data_agent", True)
        if use_data:
            return "gene_annotation_node"
        return "gene_summary_node"

    def _route_after_gene_summary(self: Any, state: DeepGenomeState):
        """Determine path after gene_summary_node completes.

        Args:
            state: Current workflow state containing config_params.

        Returns:
            Node name: "experiment_node" if use_analyst is True,
                       "introduction_node" otherwise.
        """
        use_analyst = state.get("config_params", {}).get(
            "use_analyst_agent", True
        )
        if use_analyst:
            return "experiment_node"
        return "introduction_node"

    def _route_after_part1(self: Any, state: DeepGenomeState):
        """Determine path after part1_node completes.

        This decides whether to follow the complete deep analysis framework
        or jump directly to report writing.

        Args:
            state: Current workflow state containing config_params.

        Returns:
            Node name: "experiment_node" if use_analyst is True,
                       "introduction_node" otherwise.
        """
        use_analyst = state.get("config_params", {}).get(
            "use_analyst_agent", True
        )
        if use_analyst:
            return "experiment_node"
        return "introduction_node"

    def _route_after_synthesize(self: Any, state: DeepGenomeState):
        """Determine path after synthesize_node completes.

        Args:
            state: Current workflow state containing config_params.

        Returns:
            Node name: "experiment_node" if use_data is True,
                       "introduction_node" otherwise.
        """
        use_data = state.get("config_params", {}).get("use_data_agent", True)
        if use_data:
            return "experiment_node"
        return "introduction_node"

    def _route_analyst_tasks(self: Any, state: DeepGenomeState):
        """Dispatch analysis tasks in parallel using Send API.

        Args:
            state: Current workflow state containing analysis_tasks.

        Returns:
            List of Send objects for dynamic task dispatch.
        """
        tasks = state.get("analysis_tasks", [])
        return [
            Send("analyst_node", {"task_index": i, **task})
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
                - species: Species code
                - analysis_type: Type of analysis to perform

        Returns:
            Dict containing:
                - raw_analyst_data: Task execution results
                - analyst_summaries: Generated sub-summary
                - analysis_completed_branches: Increment counter by 1
        """
        task_index = state.get("task_index")
        gene_id = state["target_gene"]
        species = state["species"]
        analysis_type = state["analysis_type"]

        print(
            f"[Analyst-{task_index}] Executing: {analysis_type} for {gene_id}"
        )

        async def run_analysis() -> dict[str, Any]:
            """Dispatch one analysis branch and return state updates."""
            result = await self._dispatch_and_wait_analysis(
                analysis_type=analysis_type,
                species=species,
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

    async def _run_knowledge_agent(self: Any, state: DeepGenomeState):
        """Retrieve literature knowledge for the target gene.

        This node retrieves relevant scientific literature for the target gene
        using the KnowledgeAgent. It fetches gene symbols, queries literature
        repositories, and stores sorted results in the knowledge context.

        Args:
            state: Current workflow state containing gene_id and species_code.

        Returns:
            Dict with knowledge_context containing literature results.
        """
        gene_id = state["gene_id"]
        species_code = state["species_code"]
        print(f"[{state['gene_id']}] Retrieving literature knowledge...")

        # Get gene symbol for the target gene
        gene_symbol_list = await self._gene_symbol(
            species_code=species_code, gene_id=gene_id
        )

        if not gene_symbol_list:
            gene_symbol_list = [gene_id]

        gene_symbol = gene_symbol_list[0]
        species_name = SPECIES_CODE_MAP.get(species_code, species_code)

        # Construct user query for KnowledgeAgent
        user_query = f"{gene_symbol}\n{species_name}?"

        # Call KnowledgeAgent to retrieve literature
        knowledge_results = await self._agents.knowledge_agent.arun(
            user_query=user_query,
            repo_id_dict=self.deep_genome_config.REPO_ID_DICT,
            is_generate=False,
            is_follow_up=False,
        )
        sorted_docs = sorted(
            knowledge_results, key=lambda x: x["score"], reverse=True
        )
        if (
            self.deep_genome_config.TOP_N is not None
            and self.deep_genome_config.TOP_N > 0
        ):
            sorted_docs = sorted_docs[: self.deep_genome_config.TOP_N]
        print(sorted_docs)
        print("Literature retrieval completed <=")
        return {"knowledge_context": {"literature": sorted_docs}}

    async def _run_gene_annotation_node(self: Any, state: DeepGenomeState):
        """Retrieve annotation for the target gene.

        This node fetches comprehensive annotation information for the target
        gene including gene symbol, description, GO terms, InterPro domains,
        and MapMan bins. Results are aggregated into gene_annotation.

        Args:
            state: Current workflow state containing gene_id and species_code.

        Returns:
            Dict with gene_annotation data and part1 branch increment.
        """
        gene_id = state["gene_id"]
        species_code = state["species_code"]
        print(f"=> Retrieving gene {gene_id} annotation...")

        # Get gene symbol
        gene_symbol_list = await self._gene_symbol(
            species_code=species_code, gene_id=gene_id
        )

        if not gene_symbol_list:
            gene_symbol_list = [gene_id]

        gene_string = "|".join(gene_symbol_list)

        # Get gene annotation
        gene_anno = await self._gene_annotation(
            species_code=species_code, gene_id=gene_id
        )

        descruption_string = "; ".join(
            go["description"] for go in gene_anno.get("description", [])
        )
        go_string = "; ".join(go["go_name"] for go in gene_anno.get("go", []))
        interpro_string = "; ".join(
            ip["interpro_name"] for ip in gene_anno.get("interpro", [])
        )
        mapman_string = "; ".join(
            mm["mapman_description"] for mm in gene_anno.get("mapman", [])
        )
        print(f"Gene {gene_id} annotation query completed <=")
        return {
            "gene_annotation": {
                "gene_string": gene_string,
                "description": descruption_string,
                "go": go_string,
                "interpro": interpro_string,
                "mapman": mapman_string,
            },
            "part1_completed_branches": 1,
        }

    async def _run_gene_summary_node(self: Any, state: DeepGenomeState):
        """Generate gene summary based on retrieved information.

        Args:
            state: Current workflow state.

        Returns:
            Dict with summary_context.
        """
        _ = state
        print("Summary ...")
        return {"summary_context": "111"}

    async def _run_data_agent(self: Any, state: DeepGenomeState):
        """Retrieve gene list for network analysis.

        This node queries the database to retrieve orthologous genes,
        paralogous genes, and protein interaction partners.

        Args:
            state: Current workflow state containing gene_id and species_code.

        Returns:
            Dict containing orthologs_data, paralogs_data, and
            interaction_data.
        """
        print("=> Retrieving gene list...")
        gene_id = state["gene_id"]
        species_code = state["species_code"]
        gene_homology_response = self._bi_json(
            "SELECT query_gene_id, query_species, homology_gene_id, "
            "homology_species "
            f"FROM homology_gene WHERE query_gene_id = '{gene_id}'"
        )
        gene_interaction_response = self._bi_json(
            "SELECT query_gene_id, query_protein, interact_gene_id, "
            "interact_protein "
            "FROM protein_interaction_col "
            f"WHERE query_gene_id = '{gene_id}' OR "
            f"interact_gene_id = '{gene_id}'"
        )
        gene_orthologs_list, gene_paralogs_list = _homology_gene_lists(
            gene_homology_response,
            species_code,
        )
        gene_interaction_list = _interaction_gene_list(
            gene_interaction_response,
            gene_id,
            species_code,
        )
        print("Gene list retrieval completed <=")
        return {
            "orthologs_data": {"gene_list": gene_orthologs_list},
            "paralogs_data": {"gene_list": gene_paralogs_list},
            "interaction_data": {"gene_list": gene_interaction_list},
        }

    def _bi_json(self: Any, sql: str) -> Dict[str, Any]:
        """Query the BI SQL endpoint and return JSON payload."""
        payload = {"sql": sql, "returnType": "json"}
        return requests.post(
            url=self.deep_genome_config.BI_URL,
            json=payload,
            headers=self._sql_headers,
            timeout=self.deep_genome_config.TIMEOUT,
        ).json()

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
        species = state.get("species_code", "")
        tasks = [
            {
                "target_gene": gene_id,
                "species": species,
                "analysis_type": "evolution_analysis",
                "compute": "medium",
                "func_name": "evolution_analysis",
            },
            {
                "target_gene": gene_id,
                "species": species,
                "analysis_type": "gene_expression_tissues",
                "compute": "small",
                "func_name": "gene_expression_tissues",
            },
            {
                "target_gene": gene_id,
                "species": species,
                "analysis_type": "gene_expression_cultivars",
                "compute": "small",
                "func_name": "gene_expression_cultivars",
            },
            {
                "target_gene": gene_id,
                "species": species,
                "analysis_type": "gene_expression_treatments",
                "compute": "small",
                "func_name": "gene_expression_treatments",
            },
            {
                "target_gene": gene_id,
                "species": species,
                "analysis_type": "gene_expression_genotypes",
                "compute": "small",
                "func_name": "gene_expression_genotypes",
            },
            {
                "target_gene": gene_id,
                "species": species,
                "analysis_type": "single_cell_analysis",
                "compute": "small",
                "func_name": "single_cell_analysis",
            },
            {
                "target_gene": gene_id,
                "species": species,
                "analysis_type": "promoter_analysis",
                "compute": "small",
                "func_name": "promoter_analysis",
            },
            {
                "target_gene": gene_id,
                "species": species,
                "analysis_type": "smep_analysis",
                "compute": "small",
                "func_name": "smep_analysis",
            },
            {
                "target_gene": gene_id,
                "species": species,
                "analysis_type": "smoc_analysis",
                "compute": "small",
                "func_name": "smoc_analysis",
            },
        ]
        return {"analysis_tasks": tasks}

    async def _dispatch_and_wait_analysis(
        self: Any,
        analysis_type: str,
        species: str,
        gene_id: str,
        output_dir: Optional[str] = None,
    ) -> dict:
        """Submit analysis task using AnalystAgent and wait for completion.

        This method constructs the appropriate prompts and data lists for the
        specified analysis type, submits the task to the AnalystAgent, waits
        for completion, and downloads the results.

        Args:
            analysis_type: Analysis type name (e.g., "evolution_analysis").
            species: Species code for the analysis.
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
            species=species,
            gene_id=gene_id,
            output_dir=resolved_output_dir,
        )
        print(f"  -> Submitting {analysis_type} task via AnalystAgent...")

        result = await self._submit_analysis_task(context, run_identity)
        self._raise_if_agent_failed(result)

        task_id = result.get("task_id")
        output_path = result.get("output_dir")
        if not isinstance(output_path, str):
            raise RuntimeError("AnalystAgent returned no output directory")
        print(f"  -> {analysis_type} task completed (task_id: {task_id})")

        print(f"  -> Preparing {analysis_type} results...")
        results_dir = self._download_analysis_result(context, output_path)
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
        data_list = get_data_list(
            self.deep_genome_config.DEEPGENOME_DATA,
            analysis_type,
            context.species,
        )
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
        run_identity: RunIdentity,
    ) -> dict:
        """Submit one resolved analysis task to AnalystAgent."""
        goal_description, data_list, meta, compute_resource = (
            self._analysis_prompt_parts(context)
        )
        return await self._agents.analyst_agent.arun(
            query=None,
            goal_description=goal_description,
            preset_data_list=data_list,
            preset_plan=meta,
            output_dir=context.output_dir,
            compute_resource=compute_resource,
            is_auto_select=False,
            is_polling=True,
            thread_id=run_identity.scoped_id(
                "thread",
                context.gene_id,
                context.analysis_type,
            ),
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
    ) -> str:
        """Return a readable result directory, downloading only if needed."""
        obsfs_result_dir = self._obsfs_analysis_result_dir(output_path)
        if obsfs_result_dir is not None:
            return obsfs_result_dir
        obs_output_path = normalize_obs_object_key(
            output_path,
            self.deep_genome_config.BUCKET_NAME,
        )
        local_results_dir = (
            Path(self.deep_genome_config.DEEPGENOME_OUT) / context.gene_id
        )
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
                    download_path=self.deep_genome_config.DEEPGENOME_OUT,
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
            print(f"  Warning: Failed to download results (continuing): {exc}")
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
