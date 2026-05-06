# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Deep genome agents for gene profile, annotation, and workflow synthesis."""

import asyncio
import operator
from collections import deque
from json import loads
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional, TypedDict
from uuid import uuid1

import requests
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .agent_registry import agent_fingerprint_values, get_cached_agent
from .analyst_agents import (
    ANALYST_CONFIG_FIELD_MAP,
    ANALYST_SECRET_FIELD_MAP,
    ANALYST_SENSITIVE_FIELD_MAP,
    AnalystAgent,
    create_output_dir,
    download_obs_out,
    get_data_list,
)
from .chat_agents import phyto_chat
from .config.defaults import DeepGenomeConfig
from .config.overrides import (
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from .config.settings import SensitiveConfig
from .data_agents import DataAgent
from .deep_genome_formatting import SPECIES_CODE_MAP, network_to_string
from .func_cache import func_cache
from .knowledge_agents import KnowledgeAgent
from .langgraph_runner import ainvoke_graph, ensure_checkpointer
from .utils import get_prompt, message_content, parse_follow_up_questions

DEEP_GENOME_CONFIG = DeepGenomeConfig()
SENSITIVE_CONFIG = SensitiveConfig.load()
_manager_cache: Dict[str, Any] = {}
GENE_LOOKUP_CACHE_TTL = 300

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


def _post_bi_sql(
    bi_url: str,
    sql_headers: Dict[str, str],
    sql: str,
    timeout: float = DEEP_GENOME_CONFIG.TIMEOUT,
) -> Dict[str, Any]:
    """Run one BI SQL query and return the JSON payload."""
    return requests.post(
        url=bi_url,
        json={"sql": sql, "returnType": "json"},
        headers=sql_headers,
        timeout=timeout,
    ).json()


@func_cache(
    key_params=["bi_url", "species_code", "gene_id"],
    ttl=GENE_LOOKUP_CACHE_TTL,
    exclude_params=["sql_headers"],
)
async def _cached_gene_symbol_lookup(
    bi_url: str,
    sql_headers: Dict[str, str],
    species_code: str,
    gene_id: str,
    timeout: float = DEEP_GENOME_CONFIG.TIMEOUT,
) -> List[str]:
    """Retrieve and cache gene symbols for one species/gene pair."""
    sql = (
        "SELECT * FROM id_table WHERE gene_id = "
        f"'{gene_id}' AND species_code = '{species_code}'"
    )
    response = await asyncio.to_thread(
        _post_bi_sql,
        bi_url,
        sql_headers,
        sql,
        timeout,
    )
    gene_symbol_list: List[str] = []
    if response["data"][0]["symbol"] is not None:
        cell_raw_value = response["data"][0]["symbol"]
        if "|" in cell_raw_value:
            gene_symbol_list.extend(set(cell_raw_value.split("|")))
        elif "," in cell_raw_value:
            gene_symbol_list.extend(set(cell_raw_value.split(",")))
        else:
            gene_symbol_list.append(cell_raw_value)
        return gene_symbol_list
    return []


@func_cache(
    key_params=["bi_url", "species_code", "gene_id"],
    ttl=GENE_LOOKUP_CACHE_TTL,
    exclude_params=["sql_headers"],
)
async def _cached_gene_annotation_lookup(
    bi_url: str,
    sql_headers: Dict[str, str],
    species_code: str,
    gene_id: str,
    timeout: float = DEEP_GENOME_CONFIG.TIMEOUT,
) -> Dict[str, Any]:
    """Retrieve and cache gene annotations for one species/gene pair."""
    sql_list = (
        "SELECT description FROM annotation_gene_description "
        f"WHERE gene_id = '{gene_id}' "
        f"AND species_code = '{species_code}'",
        "SELECT go_id, go_name FROM annotation_gene_ontology WHERE "
        f"gene_id = '{gene_id}' "
        f"AND species_code = '{species_code}'",
        "SELECT interpro_id, interpro_name "
        "FROM annotation_gene_interpro "
        f"WHERE gene_id = '{gene_id}' "
        f"AND species_code = '{species_code}'",
        "SELECT mapman, mapman_description "
        "FROM annotation_gene_mapman "
        f"WHERE gene_id = '{gene_id}' "
        f"AND species_code = '{species_code}'",
    )
    responses = await asyncio.gather(
        *(
            asyncio.to_thread(_post_bi_sql, bi_url, sql_headers, sql, timeout)
            for sql in sql_list
        )
    )
    gene_anno_dict: Dict[str, Any] = {}
    if responses[0]["data"]:
        gene_anno_dict.update({"description": responses[0]["data"]})
    if responses[1]["data"]:
        gene_anno_dict.update({"go": responses[1]["data"]})
    if responses[2]["data"]:
        gene_anno_dict.update({"interpro": responses[2]["data"]})
    if responses[3]["data"]:
        gene_anno_dict.update({"mapman": responses[3]["data"]})
    return gene_anno_dict


def clear_gene_lookup_caches() -> None:
    """Clear cached gene symbol and annotation lookup results."""
    _cached_gene_symbol_lookup.cache_clear()
    _cached_gene_annotation_lookup.cache_clear()


DEEP_GENOME_CONFIG_FIELD_MAP = {
    **ANALYST_CONFIG_FIELD_MAP,
    "batch": "BATCH",
    "epic_type": "EPIC_TYPE",
    "create_task_url": "CREATE_TASK_URL",
    "update_task_url": "UPDATE_TASK_URL",
    "database_url": "DATABASE_URL",
    "workspace_id": "WORKSPACE_ID",
    "subject_id": "SUBJECT_ID",
    "dialog_id": "DIALOG_ID",
    "need_insight": "NEED_INSIGHT",
    "prompt_file": "PROMPT_FILE",
    "deepgenome_data": "DEEPGENOME_DATA",
    "output_dir": "OUTPUT_DIR",
    "retrieve_url": "RETRIEVE_URL",
    "repo_id_dict": "REPO_ID_DICT",
    "page_num": "PAGE_NUM",
    "filter_string": "FILTER_STRING",
    "extra_repo_ids": "EXTRA_REPO_IDS",
    "rerank_url": "RERANK_URL",
    "rerank_batch_size": "RERANK_BATCH_SIZE",
    "score_threshold": "SCORE_THRESHOLD",
    "top_n": "TOP_N",
    "prompt_path": "PROMPT_PATH",
    "frequency_penalty": "FREQUENCY_PENALTY",
    "max_tokens": "MAX_TOKENS",
    "n": "N",
    "presence_penalty": "PRESENCE_PENALTY",
    "reasoning_effort": "REASONING_EFFORT",
    "response_format": "RESPONSE_FORMAT",
    "stream": "STREAM",
    "temperature": "TEMPERATURE",
    "top_p": "TOP_P",
    "user": "USER",
    "deepgenome_out": "DEEPGENOME_OUT",
    "download_path": "DOWNLOAD_PATH",
    "marker": "DOWNLOAD_MARKER",
    "max_keys": "DOWNLOAD_MAX_KEYS",
    "bi_url": "BI_URL",
    "obs_server": "OBS_SERVER",
    "bucket_name": "BUCKET_NAME",
    "part_size": "PART_SIZE",
    "task_num": "TASK_NUM",
    "timeout": "TIMEOUT",
    "retriable_codes": "RETRIABLE_CODES",
    "max_retries": "MAX_RETRIES",
    "max_concurrency": "MAX_CONCURRENCY",
    "max_workers": "MAX_WORKERS",
    "max_poll": "MAX_POLL",
}
DEEP_GENOME_SECRET_FIELD_MAP = {
    **ANALYST_SECRET_FIELD_MAP,
    "bi_token": "BI_TOKEN",
}


def update_dict(left: dict, right: dict) -> dict:
    """Merge two branch result dictionaries for LangGraph reducers."""
    merged = (left or {}).copy()
    merged.update(right or {})
    return merged


class DeepGenomeState(TypedDict):
    """State schema for the deep genome analysis workflow.

    This TypedDict defines the state structure used throughout the gene
    analysis workflow, managing parallel execution of multiple analysis tasks
    and aggregating results into a final research report.

    Attributes:
        species_code: Three-letter species code (e.g., 'osa', 'ath', 'zma').
        gene_id: Target gene identifier for analysis.
        config_params: Configuration parameters controlling workflow behavior:
            - use_analyst_agent: bool, whether to run deep analyst analysis.
            - use_data_agent: bool, whether to fetch gene network data.
        gene_annotation: Dictionary containing gene annotation data including
            gene symbol, description, GO terms, InterPro domains, and
            MapMan bins.
        skip_synthesize: Flag to skip synthesis node in test mode.
        knowledge_context: Context data from knowledge agent retrieval.
        orthologs_data: Orthologous gene list with species and gene symbols.
        paralogs_data: Paralogous gene list with species and gene symbols.
        interaction_data: Protein interaction gene list with species and
            symbols.
        orthologs_summary: Formatted string summarizing ortholog gene network.
        paralogs_summary: Formatted string summarizing paralog gene network.
        interaction_summary: Formatted string summarizing interaction network.
        part1_report: Aggregated basic gene network profile report.
        analysis_tasks: Analysis task dictionaries for parallel execution.
        raw_analyst_data: Raw data from analyst tasks.
        analyst_summaries: Processed summaries from analyst tasks.
        synthesize_report: Aggregated deep analysis synthesis report.
        experiment_report: Recommended experiments section.
        protocol_report: Step-by-step experimental protocols section.
        introduction_report: Report introduction section.
        discussion_report: Report discussion section.
        summary_report: Report summary and conclusion section.
        follow_up_questions: List of suggested follow-up research questions.
        part1_completed_branches: Counter for part1 barrier (target: 4)
        analysis_completed_branches: Counter for the analysis barrier.
        experiment_completed_branches: Counter for the experiment barrier.
        report_triggered: Boolean to prevent duplicate report node execution.
    """

    species_code: str
    gene_id: str
    config_params: Dict[str, Any]
    gene_annotation: Dict[str, Any]
    skip_synthesize: bool
    knowledge_context: Dict[str, Any]
    orthologs_data: Dict[str, Any]
    paralogs_data: Dict[str, Any]
    interaction_data: Dict[str, Any]
    orthologs_summary: Optional[str]
    paralogs_summary: Optional[str]
    interaction_summary: Optional[str]
    part1_report: Optional[str]
    analysis_tasks: List[Dict[str, Any]]
    raw_analyst_data: Annotated[Dict[str, Any], update_dict]
    analyst_summaries: Dict
    synthesize_report: Optional[str]
    experiment_report: Optional[str]
    protocol_report: Optional[str]
    introduction_report: Optional[str]
    discussion_report: Optional[str]
    summary_report: Optional[str]
    follow_up_questions: Optional[List[str]]
    part1_completed_branches: Annotated[int, operator.add]
    analysis_completed_branches: Annotated[int, operator.add]
    experiment_completed_branches: Annotated[int, operator.add]
    report_triggered: bool
    target_gene: str
    species: str
    analysis_type: str
    part12_combined: Optional[str]


class DeepGenomeAgents:
    """LangGraph-based agent for comprehensive gene function analysis.

    This agent provides a sophisticated workflow for analyzing gene function
    and related biological processes in plant genomes. It orchestrates multiple
    specialized agents including data_agent, knowledge_agent, and
    analyst_agent to perform parallel gene network and deep computational
    analysis.

    The workflow consists of three main phases:
    1. **Part 1 - Gene Network Profile**: Retrieves orthologs, paralogs, and
       protein interactions; fetches gene annotations; aggregates into a basic
       gene function network report.
    2. **Part 2 - Deep Analysis**: Executes 9 parallel analysis tasks including
       evolution analysis, expression analysis across tissues/cultivars/
       treatments/genotypes, single-cell analysis, promoter analysis, SMEP,
       and SMOC analysis.
    3. **Part 3 - Report Generation**: Synthesizes experiment recommendations,
       protocols, introduction, discussion, and summary sections.

    Attributes:
        data_agent: Data agent for retrieving gene network information.
        knowledge_agent: Knowledge agent for literature retrieval.
        analyst_agent: Analyst agent for submitting and managing tasks.
        checkpointer: LangGraph MemorySaver for state persistence.
        DEEP_GENOME_CONFIG: Deep genome configuration object.
        SENSITIVE_CONFIG: Sensitive configuration settings.
        app: Compiled LangGraph application.

    Example:
        >>> agents = DeepGenomeAgents(
        ...     data_agent=data_agent,
        ...     knowledge_agent=knowledge_agent,
        ...     analyst_agent=analyst_agent
        ... )
        >>> result = await agents.arun(
        ...     species_code='osa',
        ...     gene_id='Os01g0177400'
        ... )
    """

    def __init__(
        self,
        data_agent,
        knowledge_agent,
        analyst_agent,
        checkpointer: Optional[MemorySaver] = None,
        deep_genome_config=DEEP_GENOME_CONFIG,
        sensitive_config=SENSITIVE_CONFIG,
    ):
        """Initialize the DeepGenomeAgents.

        Args:
            data_agent: Data agent for retrieving gene network information.
            knowledge_agent: Knowledge agent for literature retrieval.
            analyst_agent: Analyst agent for submitting and managing tasks.
            checkpointer: LangGraph MemorySaver for state persistence.
            deep_genome_config: Deep genome configuration object.
            sensitive_config: Sensitive configuration for credentials.
        """
        self.data_agent = data_agent
        self.knowledge_agent = knowledge_agent
        self.analyst_agent = analyst_agent
        self.checkpointer = ensure_checkpointer(checkpointer)
        self.deep_genome_config = deep_genome_config
        self.sensitive_config = sensitive_config
        self._figure_index = 1
        self._sql_headers = {
            "Content-Type": "application/json",
            "token": self.sensitive_config.BI_TOKEN.get_secret_value(),
        }
        self.app = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(DeepGenomeState)

        workflow.add_node("knowledge_node", self.run_knowledge_agent)
        workflow.add_node(
            "gene_annotation_node", self.run_gene_annotation_node
        )
        workflow.add_node("gene_summary_node", self.run_gene_summary_node)
        workflow.add_node("data_node", self.run_data_agent)
        workflow.add_node("orthologs_node", self.run_orthologs_node)
        workflow.add_node("paralogs_node", self.run_paralogs_node)
        workflow.add_node("interaction_node", self.run_interaction_node)
        workflow.add_node(
            "orthologs_annotation_node", self.run_orthologs_annotation_node
        )
        workflow.add_node(
            "paralogs_annotation_node", self.run_paralogs_annotation_node
        )
        workflow.add_node(
            "interaction_annotation_node", self.run_interaction_annotation_node
        )
        workflow.add_node("part1_node", self.run_part1_node)

        workflow.add_node("prepare_tasks_node", self.prepare_analysis_tasks)
        workflow.add_node("synthesize_node", self.run_report_synthesizer)

        # 使用单一节点，通过 Send API 动态派发
        workflow.add_node("analyst_node", self.run_analyst_node)

        workflow.add_node("experiment_node", self.run_report_experiment)
        workflow.add_node("protocol_node", self.run_report_protocol)
        workflow.add_node("introduction_node", self.run_report_introduction)
        workflow.add_node("discussion_node", self.run_report_discussion)
        workflow.add_node("summary_node", self.run_report_summary)
        workflow.add_node("follow_up_node", self.run_follow_up_node)

        workflow.add_conditional_edges(
            START,
            self.route_start,
            ["knowledge_node", "data_node", "prepare_tasks_node"],
        )
        workflow.add_edge("knowledge_node", "gene_annotation_node")
        workflow.add_conditional_edges(
            "knowledge_node",
            self.route_after_knowledge,
            ["gene_summary_node", "gene_annotation_node"],
        )
        workflow.add_edge("gene_annotation_node", "part1_node")
        workflow.add_conditional_edges(
            "gene_summary_node",
            self.route_after_gene_summary,
            ["introduction_node", "experiment_node"],
        )

        workflow.add_edge("data_node", "orthologs_node")
        workflow.add_edge("data_node", "paralogs_node")
        workflow.add_edge("data_node", "interaction_node")
        workflow.add_edge("orthologs_node", "orthologs_annotation_node")
        workflow.add_edge("paralogs_node", "paralogs_annotation_node")
        workflow.add_edge("interaction_node", "interaction_annotation_node")
        workflow.add_edge("orthologs_annotation_node", "part1_node")
        workflow.add_edge("paralogs_annotation_node", "part1_node")
        workflow.add_edge("interaction_annotation_node", "part1_node")

        # 使用 Send API 动态派发分析任务
        workflow.add_conditional_edges(
            "prepare_tasks_node", self.route_analyst_tasks, ["analyst_node"]
        )
        # Send 实例完成后结束
        workflow.add_conditional_edges(
            "analyst_node", self.route_after_analyst, [END]
        )
        # prepare_tasks_node 完成后直接到 synthesize_node 作为 barrier
        workflow.add_edge("prepare_tasks_node", "synthesize_node")
        # synthesize_node 完成后进入实验节点
        workflow.add_conditional_edges(
            "synthesize_node",
            self.route_after_synthesize,
            ["experiment_node", "introduction_node"],
        )

        # part1_node 通过条件边决定后续节点
        workflow.add_conditional_edges(
            "part1_node",
            self.route_after_part1,
            [
                "experiment_node",
                "introduction_node",
            ],  # introduction_node 只在 use_analyst=False 时触发
        )

        # 报告节点线性连接
        workflow.add_edge("experiment_node", "protocol_node")
        workflow.add_edge("protocol_node", "introduction_node")
        workflow.add_edge("introduction_node", "discussion_node")
        workflow.add_edge("discussion_node", "summary_node")
        workflow.add_edge("summary_node", "follow_up_node")

        workflow.add_edge("follow_up_node", END)

        return workflow.compile(checkpointer=self.checkpointer)

    async def arun(
        self,
        species_code: str,
        gene_id: str,
        config_params: Optional[Dict[str, Any]] = None,
        thread_id: Optional[str] = None,
        test_mode: bool = False,
        mock_analyst_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Main entry function for gene analysis workflow.

        Args:
            species_code: Three-letter species code (e.g., 'osa', 'ath').
            gene_id: Target gene identifier.
            config_params: Optional configuration parameters:
                - use_analyst_agent: bool, whether to run deep analyst
                  analysis.
                - use_data_agent: bool, whether to fetch gene network data.
            thread_id: Optional thread ID for checkpointer.
            test_mode: If True, skip actual analyst tasks and use mock data.
            mock_analyst_data: Pre-prepared analyst data. Should contain:
                - analyst_summaries: Dict mapping task names to summary strings
                - synthesis_report: Optional pre-generated synthesis report
                - analysis_tasks: List of task dicts (for counter purposes)

        Returns:
            Final state containing all analysis results including:
            - part1_report: Gene function and network summary
            - synthesize_report: Deep analysis synthesis
            - experiment_report: Recommended experiments
            - protocol_report: Experimental protocols
            - introduction_report: Report introduction
            - discussion_report: Report discussion
            - summary_report: Report summary
            - final_report: Complete final report
            - follow_up_questions: Suggested follow-up questions
        """
        if config_params is None:
            config_params = {}

        initial_state = {
            "species_code": species_code,
            "gene_id": gene_id,
            "config_params": config_params,
            # Initialize barrier counters
            "part1_completed_branches": 0,
            "experiment_completed_branches": 0,
            # Initialize empty context
            "knowledge_context": {},
            "orthologs_data": {},
            "paralogs_data": {},
            "interaction_data": {},
            "orthologs_summary": None,
            "paralogs_summary": None,
            "interaction_summary": None,
            "part1_report": None,
            "analysis_tasks": [],
            "raw_analyst_data": {},
            "analyst_summaries": {},
            "synthesize_report": None,
            "experiment_report": None,
            "protocol_report": None,
            "introduction_report": None,
            "discussion_report": None,
            "summary_report": None,
            "follow_up_questions": [],
            "final_report": None,
            "report_triggered": False,
        }

        # 测试模式：跳过 analyst 任务，直接使用预准备的数据
        if test_mode and mock_analyst_data:
            print("🧪 [测试模式] 使用预准备的分析师数据")
            task_count = len(mock_analyst_data.get("analysis_tasks", []))
            initial_state.update(
                {
                    "analysis_tasks": mock_analyst_data.get(
                        "analysis_tasks", []
                    ),
                    "analyst_summaries": mock_analyst_data.get(
                        "analyst_summaries", {}
                    ),
                    "synthesize_report": mock_analyst_data.get(
                        "synthesize_report", "这是预生成的综合分析报告。"
                    ),
                    "experiment_completed_branches": 2,  # 直接设为目标值，跳过等待
                    "analysis_completed_branches": task_count,  # 模拟已完成
                    "skip_synthesize": True,  # 跳过 synthesize_node
                }
            )
            print(f"skip_synthesize: {initial_state['skip_synthesize']}")

        result = await ainvoke_graph(
            self.app, initial_state, thread_id=thread_id
        )
        return result

    def route_start(self, state: DeepGenomeState):
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

    def route_after_knowledge(self, state: DeepGenomeState):
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

    def route_after_gene_summary(self, state: DeepGenomeState):
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

    def route_after_part1(self, state: DeepGenomeState):
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

    def route_after_synthesize(self, state: DeepGenomeState):
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

    def route_analyst_tasks(self, state: DeepGenomeState):
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

    def route_after_analyst(self, state: DeepGenomeState):
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

    async def run_analyst_node(self, state: DeepGenomeState) -> dict:
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

        try:
            result = await self._dispatch_and_wait_analysis(
                analysis_type=analysis_type,
                species=species,
                gene_id=gene_id,
            )

            # 直接生成子总结
            sub_summary = self._generate_sub_summary(
                analysis_type=analysis_type,
                gene_id=gene_id,
                state=state,
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
        except Exception as e:  # pylint: disable=broad-exception-caught
            # Workflow boundary: preserve branch failure details in state.
            return {
                "raw_analyst_data": {
                    f"task_{task_index}": {
                        "status": "failed",
                        "analysis_type": analysis_type,
                        "error": str(e),
                    }
                },
                "analysis_completed_branches": 1,
            }

    def _generate_sub_summary(
        self, analysis_type: str, gene_id: str, state: DeepGenomeState
    ) -> dict:
        """Generate sub-summary for a specific analysis type.

        This method reads output files from completed analysis tasks and
        formats them into a structured dictionary for report generation.
        It handles multiple analysis types including evolution, expression,
        single-cell, promoter, and protein structure analysis.

        Args:
            analysis_type: Type of analysis (e.g., 'evolution_analysis',
                         'gene_expression_tissues').
            gene_id: Target gene identifier.
            state: Current workflow state containing analyst_summaries.

        Returns:
            Dict containing analysis results with image paths, summaries,
            and legends formatted for report integration.
        """
        deepgenome_out = self.deep_genome_config.DEEPGENOME_OUT
        out_path = Path(f"{deepgenome_out}/{gene_id}")
        gene_results_data = state.get(
            "analyst_summaries", {"gene_name": gene_id}
        )
        match analysis_type:
            case "evolution_analysis":
                try:
                    target_file = next(out_path.rglob("*tree.png")).name
                    tree_img_path = f"{gene_id}/{target_file}"
                    gene_results_data["tree_path"] = tree_img_path
                    with open(
                        out_path / f"{gene_id}_tree.summary",
                        "r",
                        encoding="utf-8",
                    ) as summary_file:
                        summary = summary_file.read()
                        summary = summary.replace(
                            "Figure 1", f"Figure {self._figure_index}"
                        )
                        gene_results_data["tree_summary"] = summary
                    with open(
                        out_path / f"{gene_id}_tree.legend",
                        "r",
                        encoding="utf-8",
                    ) as legend_file:
                        legend = legend_file.read()
                        legend = legend.replace(
                            "Figure 1", f"Figure {self._figure_index}"
                        )
                        gene_results_data["tree_legend"] = legend
                    self._figure_index += 1
                except (StopIteration, FileNotFoundError, OSError, IOError):
                    gene_results_data["tree_path"] = ""
                    gene_results_data["tree_summary"] = "None Results"
                    gene_results_data["tree_legend"] = ""
                try:
                    with open(
                        out_path / f"{gene_id}_domain.md",
                        "r",
                        encoding="utf-8",
                    ) as domain_f:
                        domain = domain_f.read()
                        gene_results_data["domain_table"] = domain
                    with open(
                        out_path / f"{gene_id}_domain.summary",
                        "r",
                        encoding="utf-8",
                    ) as summary_file:
                        summary = summary_file.read()
                        gene_results_data["domain_summary"] = summary
                    with open(
                        out_path / f"{gene_id}_domain.legend",
                        "r",
                        encoding="utf-8",
                    ) as summary_file:
                        summary = summary_file.read()
                        gene_results_data["domain_legend"] = summary
                except (FileNotFoundError, OSError, IOError):
                    gene_results_data["domain_table"] = ""
                    gene_results_data["domain_summary"] = "None Results"
                    gene_results_data["domain_legend"] = ""
            case "gene_expression_tissues":
                try:
                    target_file = next(out_path.rglob("*tissues.png")).name
                    tissue_img = f"{gene_id}/{target_file}"
                    gene_results_data["tissue_path"] = tissue_img
                    target_file = next(out_path.rglob("*tissues.summary")).name
                    with open(
                        out_path / f"{target_file}", "r", encoding="utf-8"
                    ) as summary_file:
                        summary = summary_file.read()
                        summary = summary.replace(
                            "Figure 1", f"Figure {self._figure_index}"
                        )
                        gene_results_data["tissue_summary"] = summary
                    target_file = next(out_path.rglob("*tissues.legend")).name
                    with open(
                        out_path / f"{target_file}", "r", encoding="utf-8"
                    ) as legend_file:
                        legend = legend_file.read()
                        legend = legend.replace(
                            "Figure 1", f"Figure {self._figure_index}"
                        )
                        gene_results_data["tissue_legend"] = legend
                    self._figure_index += 1
                except (StopIteration, FileNotFoundError, OSError, IOError):
                    gene_results_data["tissue_path"] = ""
                    gene_results_data["tissue_summary"] = "None Results"
                    gene_results_data["tissue_legend"] = ""
            case "gene_expression_cultivars":
                try:
                    target_file = next(out_path.rglob("*cultivars.png")).name
                    cultivar_img = f"{gene_id}/{target_file}"
                    gene_results_data["cultivar_path"] = cultivar_img
                    target_file = next(
                        out_path.rglob("*cultivars.summary")
                    ).name
                    with open(
                        out_path / f"{target_file}", "r", encoding="utf-8"
                    ) as summary_file:
                        summary = summary_file.read()
                        summary = summary.replace(
                            "Figure 1", f"Figure {self._figure_index}"
                        )
                        gene_results_data["cultivar_summary"] = summary
                    target_file = next(
                        out_path.rglob("*cultivars.legend")
                    ).name
                    with open(
                        out_path / f"{target_file}", "r", encoding="utf-8"
                    ) as legend_file:
                        legend = legend_file.read()
                        legend = legend.replace(
                            "Figure 1", f"Figure {self._figure_index}"
                        )
                        gene_results_data["cultivar_legend"] = legend
                    self._figure_index += 1
                except (StopIteration, FileNotFoundError, OSError, IOError):
                    gene_results_data["cultivar_path"] = ""
                    gene_results_data["cultivar_summary"] = "None Results"
                    gene_results_data["cultivar_legend"] = ""
            case "gene_expression_treatments":
                try:
                    target_file = next(out_path.rglob("*treatments.png")).name
                    treatment_img = f"{gene_id}/{target_file}"
                    gene_results_data["treatment_path"] = treatment_img
                    target_file = next(
                        out_path.rglob("*treatments.summary")
                    ).name
                    with open(
                        out_path / f"{target_file}", "r", encoding="utf-8"
                    ) as summary_file:
                        summary = summary_file.read()
                        summary = summary.replace(
                            "Figure 1", f"Figure {self._figure_index}"
                        )
                        gene_results_data["treatment_summary"] = summary
                    target_file = next(
                        out_path.rglob("*treatments.legend")
                    ).name
                    with open(
                        out_path / f"{target_file}", "r", encoding="utf-8"
                    ) as legend_file:
                        legend = legend_file.read()
                        legend = legend.replace(
                            "Figure 1", f"Figure {self._figure_index}"
                        )
                        gene_results_data["treatment_legend"] = legend
                    self._figure_index += 1
                except (StopIteration, FileNotFoundError, OSError, IOError):
                    gene_results_data["treatment_path"] = ""
                    gene_results_data["treatment_summary"] = "None Results"
                    gene_results_data["treatment_legend"] = ""
            case "gene_expression_genotypes":
                try:
                    target_file = next(out_path.rglob("*genotypes.png")).name
                    genotype_img = f"{gene_id}/{target_file}"
                    gene_results_data["mutant_path"] = genotype_img
                    target_file = next(
                        out_path.rglob("*genotypes.summary")
                    ).name
                    with open(
                        out_path / f"{target_file}", "r", encoding="utf-8"
                    ) as summary_file:
                        summary = summary_file.read()
                        summary = summary.replace(
                            "Figure 1", f"Figure {self._figure_index}"
                        )
                        gene_results_data["mutant_summary"] = summary
                    target_file = next(
                        out_path.rglob("*genotypes.legend")
                    ).name
                    with open(
                        out_path / f"{target_file}", "r", encoding="utf-8"
                    ) as legend_file:
                        legend = legend_file.read()
                        legend = legend.replace(
                            "Figure 1", f"Figure {self._figure_index}"
                        )
                        gene_results_data["mutant_legend"] = legend
                    self._figure_index += 1
                except (StopIteration, FileNotFoundError, OSError, IOError):
                    gene_results_data["mutant_path"] = ""
                    gene_results_data["mutant_summary"] = "None Results"
                    gene_results_data["mutant_legend"] = ""
            case "single_cell_analysis":
                try:
                    target_file = next(out_path.rglob("*_umap.png")).name
                    sc_umap = f"{gene_id}/{target_file}"
                    gene_results_data["umap_path"] = sc_umap
                    target_file = next(
                        out_path.rglob("*_violin_plot.png")
                    ).name
                    sc_violin = f"{gene_id}/{target_file}"
                    gene_results_data["violin_path"] = sc_violin
                    with open(
                        out_path / f"{gene_id}_single_cell.summary",
                        "r",
                        encoding="utf-8",
                    ) as summary_file:
                        summary = summary_file.read()
                        gene_results_data["single_cell_summary"] = summary
                    with open(
                        out_path / f"{gene_id}_single_cell.legend",
                        "r",
                        encoding="utf-8",
                    ) as legend_file:
                        legend = legend_file.read()
                        legend = legend.replace(
                            "Figure 1", f"Figure {self._figure_index}"
                        )
                        gene_results_data["single_cell_legend"] = legend
                    self._figure_index += 1
                except (StopIteration, FileNotFoundError, OSError, IOError):
                    gene_results_data["umap_path"] = ""
                    gene_results_data["violin_path"] = ""
                    gene_results_data["single_cell_summary"] = "None Results"
                    gene_results_data["single_cell_legend"] = ""
            case "haplotypes_analysis":
                try:
                    target_file = next(
                        out_path.rglob("*promoter_hap.png")
                    ).name
                    haplotype_img = f"{gene_id}/{target_file}"
                    gene_results_data["haplotype_path"] = haplotype_img
                    with open(
                        out_path / f"{gene_id}_haplotype.summary",
                        "r",
                        encoding="utf-8",
                    ) as summary_file:
                        summary = summary_file.read()
                        gene_results_data["haplotype_summary"] = summary
                    with open(
                        out_path / f"{gene_id}_haplotype.legend",
                        "r",
                        encoding="utf-8",
                    ) as legend_file:
                        legend = legend_file.read()
                        legend = legend.replace(
                            "Figure 1", f"Figure {self._figure_index}"
                        )
                        gene_results_data["haplotype_legend"] = legend
                    self._figure_index += 1
                except (StopIteration, FileNotFoundError, OSError, IOError):
                    gene_results_data["haplotype_path"] = ""
                    gene_results_data["haplotype_summary"] = "None Results"
                    gene_results_data["haplotype_legend"] = ""
            case "promoter_analysis":
                try:
                    target_file = next(
                        out_path.rglob("motif_all_logo.png")
                    ).name
                    motif_img = f"{gene_id}/{target_file}"
                    gene_results_data["motif_path"] = motif_img
                    with open(
                        out_path / f"{gene_id}_motif.summary",
                        "r",
                        encoding="utf-8",
                    ) as summary_file:
                        summary = summary_file.read()
                        summary = summary.replace(
                            "Figure 1", f"Figure {self._figure_index}"
                        )
                        gene_results_data["motif_summary"] = summary
                    with open(
                        out_path / f"{gene_id}_motif.legend",
                        "r",
                        encoding="utf-8",
                    ) as legend_file:
                        legend = legend_file.read()
                        legend = legend.replace(
                            "Figure 1", f"Figure {self._figure_index}"
                        )
                        gene_results_data["motif_legend"] = legend
                    self._figure_index += 1
                except (StopIteration, FileNotFoundError, OSError, IOError):
                    gene_results_data["motif_path"] = ""
                    gene_results_data["motif_summary"] = "None Results"
                    gene_results_data["motif_legend"] = ""
            case "smep_analysis":
                try:
                    target_file = next(out_path.rglob("*smep.png")).name
                    smep_img = f"{gene_id}/{target_file}"
                    gene_results_data["smep_path"] = smep_img
                    target_file = next(out_path.rglob("*smep.summary")).name
                    with open(
                        out_path / f"{target_file}", "r", encoding="utf-8"
                    ) as summary_file:
                        summary = summary_file.read()
                        summary = summary.replace(
                            "Figure 1", f"Figure {self._figure_index}"
                        )
                        gene_results_data["smep_summary"] = summary
                    target_file = next(out_path.rglob("*smep.legend")).name
                    with open(
                        out_path / f"{target_file}", "r", encoding="utf-8"
                    ) as legend_file:
                        legend = legend_file.read()
                        legend = legend.replace(
                            "Figure 1", f"Figure {self._figure_index}"
                        )
                        gene_results_data["smep_legend"] = legend
                    self._figure_index += 1
                except (StopIteration, FileNotFoundError, OSError, IOError):
                    gene_results_data["smep_path"] = ""
                    gene_results_data["smep_legend"] = ""
                    gene_results_data["smep_summary"] = ""
            case "smoc_analysis":
                try:
                    target_file = next(out_path.rglob("*smoc.png")).name
                    smep_img = f"{gene_id}/{target_file}"
                    gene_results_data["smoc_path"] = smep_img
                    target_file = next(out_path.rglob("*smoc.summary")).name
                    with open(
                        out_path / f"{target_file}", "r", encoding="utf-8"
                    ) as summary_file:
                        summary = summary_file.read()
                        summary = summary.replace(
                            "Figure 1", f"Figure {self._figure_index}"
                        )
                        gene_results_data["smoc_summary"] = summary
                    target_file = next(out_path.rglob("*smoc.legend")).name
                    with open(
                        out_path / f"{target_file}", "r", encoding="utf-8"
                    ) as legend_file:
                        legend = legend_file.read()
                        legend = legend.replace(
                            "Figure 1", f"Figure {self._figure_index}"
                        )
                        gene_results_data["smoc_legend"] = legend
                    self._figure_index += 1
                except (StopIteration, FileNotFoundError, OSError, IOError):
                    gene_results_data["smoc_path"] = ""
                    gene_results_data["smoc_legend"] = ""
                    gene_results_data["smoc_summary"] = ""
            case "protein_structure_analysis":
                try:
                    protein_structure_files = list(
                        out_path.glob("*_seed_101_sample_0.cif")
                    )
                    if len(protein_structure_files) == 0:
                        gene_results_data["protein_structures"] = (
                            "None Results"
                        )
                    else:
                        gene_results_data["protein_structures"] = ""
                        for structure_path in protein_structure_files:
                            structure_file = f"{gene_id}/{structure_path.name}"
                            structure_start = structure_path.name.split(
                                ".cif"
                            )[0]
                            gene_results_data[
                                "protein_structures"
                            ] += f"![3D Structure]({structure_file})\n"
                            with open(
                                out_path / f"{structure_start}.legend",
                                "r",
                                encoding="utf-8",
                            ) as legend_file:
                                legend = legend_file.read()
                                legend = legend.replace(
                                    "Table 1", f"Figure {self._figure_index}"
                                )
                                legend = legend.replace(
                                    "Figure 1", f"Figure {self._figure_index}"
                                )
                                gene_results_data[
                                    "protein_structures"
                                ] += f"{legend}\n"

                            with open(
                                out_path / f"{structure_start}.summary",
                                "r",
                                encoding="utf-8",
                            ) as summary_file:
                                summary = summary_file.read()
                                summary = summary.replace(
                                    "Figure 1", f"Figure {self._figure_index}"
                                )
                                gene_results_data[
                                    "protein_structures"
                                ] += f"{summary}\n"
                            self._figure_index += 1
                except (FileNotFoundError, OSError, IOError):
                    gene_results_data["protein_structures"] = "None Results"
            case _:
                pass
        return gene_results_data

    async def run_knowledge_agent(self, state: DeepGenomeState):
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
        gene_symbol_list = await self.gene_symbol(
            species_code=species_code, gene_id=gene_id
        )

        if not gene_symbol_list:
            gene_symbol_list = [gene_id]

        gene_symbol = gene_symbol_list[0]
        species_name = SPECIES_CODE_MAP.get(species_code, species_code)

        # Construct user query for KnowledgeAgent
        user_query = f"{gene_symbol}\n{species_name}?"

        # Call KnowledgeAgent to retrieve literature
        knowledge_results = await self.knowledge_agent.arun(
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

    async def run_gene_annotation_node(self, state: DeepGenomeState):
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
        gene_symbol_list = await self.gene_symbol(
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

    async def run_gene_summary_node(self, state: DeepGenomeState):
        """Generate gene summary based on retrieved information.

        Args:
            state: Current workflow state.

        Returns:
            Dict with summary_context.
        """
        _ = state
        print("Summary ...")
        return {"summary_context": "111"}

    async def run_data_agent(self, state: DeepGenomeState):
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
        sql = (
            "SELECT query_gene_id, query_species, homology_gene_id, "
            "homology_species "
            f"FROM homology_gene WHERE query_gene_id = '{gene_id}'"
        )
        payload = {"sql": sql, "returnType": "json"}
        gene_homology_response = requests.post(
            url=self.deep_genome_config.BI_URL,
            json=payload,
            headers=self._sql_headers,
            timeout=self.deep_genome_config.TIMEOUT,
        ).json()

        sql = (
            "SELECT query_gene_id, query_protein, interact_gene_id, "
            "interact_protein "
            "FROM protein_interaction_col "
            f"WHERE query_gene_id = '{gene_id}' OR "
            f"interact_gene_id = '{gene_id}'"
        )
        payload = {"sql": sql, "returnType": "json"}
        gene_interaction_response = requests.post(
            url=self.deep_genome_config.BI_URL,
            json=payload,
            headers=self._sql_headers,
            timeout=self.deep_genome_config.TIMEOUT,
        ).json()

        gene_orthologs_set, gene_paralogs_set = set(), set()
        for gene_homology in gene_homology_response["data"]:
            if gene_homology["homology_species"] == species_code:
                gene_paralogs_set.add(
                    (
                        gene_homology["homology_species"],
                        gene_homology["homology_gene_id"],
                    )
                )
            else:
                gene_orthologs_set.add(
                    (
                        gene_homology["homology_species"],
                        gene_homology["homology_gene_id"],
                    )
                )
        gene_orthologs_list = sorted(gene_orthologs_set)
        gene_paralogs_list = sorted(gene_paralogs_set)
        gene_interaction_set = set()
        for gene_interaction in gene_interaction_response["data"]:
            if gene_interaction["query_gene_id"] == gene_id:
                gene_interaction_set.add(
                    (species_code, gene_interaction["interact_gene_id"])
                )
            elif gene_interaction["interact_gene_id"] == gene_id:
                gene_interaction_set.add(
                    (species_code, gene_interaction["query_gene_id"])
                )
        gene_interaction_list = sorted(gene_interaction_set)
        # print({
        #     'orthologs_data': {'gene_list': gene_orthologs_list},
        #     'paralogs_data': {'gene_list': gene_paralogs_list},
        #     'interaction_data': {'gene_list': gene_interaction_list}
        #     }
        # )
        print("Gene list retrieval completed <=")
        return {
            "orthologs_data": {"gene_list": gene_orthologs_list},
            "paralogs_data": {"gene_list": gene_paralogs_list},
            "interaction_data": {"gene_list": gene_interaction_list},
        }

    async def prepare_analysis_tasks(self, state: DeepGenomeState):
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
        self,
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
        # 根据 analysis_type 获取对应的 prompt 和 data_list
        goal_path = ANALYSIS_GOAL_TEMPLATE_MAP.get(analysis_type)
        meta_path = ANALYSIS_META_TEMPLATE_MAP.get(analysis_type)
        if not goal_path or not meta_path:
            raise ValueError(f"Unknown analysis type: {analysis_type}")

        # 构建 goal_description
        goal_description = get_prompt(
            self.deep_genome_config.PROMPT_FILE,
            goal_path,
            {"gene_id": gene_id},
        )
        # 构建 meta
        meta = get_prompt(self.deep_genome_config.PROMPT_FILE, meta_path)
        # 构建 data_list
        data_list = get_data_list(
            self.deep_genome_config.DEEPGENOME_DATA, analysis_type, species
        )

        # 获取计算资源等级
        compute_resource = self._get_compute_resource(analysis_type)

        # 获取输出目录
        if not output_dir:
            access_key_id, secret_access_key = (
                self.sensitive_config.obs_credentials()
            )
            output_dir = create_output_dir(
                user_id=self.deep_genome_config.USER_ID or str(uuid1()),
                task=f"{analysis_type}_task",
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                obs_server=self.deep_genome_config.OBS_SERVER,
                bucket_name=self.deep_genome_config.BUCKET_NAME,
            )

        print(f"  → 使用 AnalystAgent 提交 {analysis_type} 任务...")

        # 使用 AnalystAgent 提交任务
        result = await self.analyst_agent.arun(
            query=None,
            goal_description=goal_description,
            preset_data_list=data_list,
            preset_plan=meta,  # meta 作为预定义计划传入
            output_dir=output_dir,
            compute_resource=compute_resource,
            is_auto_select=False,  # 已通过 data_list 预设数据
            is_polling=True,  # 等待任务完成
            thread_id=f"{gene_id}_{analysis_type}_{uuid1()}",
        )

        if result.get("task_status") == "FAILED_AT_AGENT_LEVEL":
            raise RuntimeError(
                f"AnalystAgent failed: {result.get('error_detail')}"
            )

        task_id = result.get("task_id")
        output_path = result.get("output_dir")

        print(f"  → {analysis_type} 任务完成 (task_id: {task_id})")

        # 下载结果 - 根据分析类型下载特定文件
        print(f"  → 下载 {analysis_type} 结果...")
        obs_output_path = (
            output_path.split("/obs/phytomni/")[-1]
            if "/obs/phytomni/" in output_path
            else output_path
        )
        target_file_feature = ANALYSIS_TARGET_FILE_FEATURE_MAP.get(
            analysis_type,
            DEFAULT_TARGET_FILE_FEATURE,
        )

        try:
            access_key_id, secret_access_key = (
                self.sensitive_config.obs_credentials()
            )
            deque(
                download_obs_out(
                    task_dir=gene_id,
                    obs_output_path=obs_output_path,
                    download_path=output_path,
                    access_key_id=access_key_id,
                    secret_access_key=secret_access_key,
                    obs_server=self.deep_genome_config.OBS_SERVER,
                    bucket_name=self.deep_genome_config.BUCKET_NAME,
                    target_file_feature=target_file_feature,
                    if_download_all=False,
                ),
                maxlen=0,
            )
        except OSError as e:
            print(f"  Warning: Failed to download results (continuing): {e}")

        return {
            "task_id": task_id,
            "output_path": output_path,
            "status": "completed",
        }

    def _get_compute_resource(self, analysis_type: str) -> str:
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

    async def gene_symbol(
        self,
        species_code: str,
        gene_id: str,
        semaphore: Optional[asyncio.Semaphore] = None,
    ) -> List[str]:
        """Retrieve gene symbols for a specific gene ID and species code.

        This function queries a database using the `nl2sql` service to find
        gene symbols associated with the provided `gene_id` and `species_code`.
        It parses the response from `nl2sql`, expecting a specific structure,
        and extracts gene symbols. It can handle symbols that are
        pipe-separated or comma-separated within a single field. An optional
        semaphore can limit concurrency.

        Args:
            species_code: The species code for the gene for which symbols are
                being retrieved.
            gene_id: The identifier of the gene for which symbols are being
                retrieved.
            workspace_id: Identifier for the workspace containing the data.
            subject_id: Identifier for the database subject or schema.
            dialog_id: Identifier for the current dialog or conversation.
            need_insight: Whether to generate insights based on query results.
            timeout: Request timeout in seconds for the underlying `nl2sql` API
                calls.
            retriable_codes: HTTP status codes that trigger a retry.
            max_retries: Maximum number of retry attempts.
            semaphore: An optional `asyncio.Semaphore` instance to limit the
                concurrency of `nl2sql` calls if this function is called
                multiple times concurrently.

        Returns:
            A list of gene symbols associated with the given `gene_id` and
            `species_code`. Returns an empty list if no symbols are found or
            if the data format from the `nl2sql` service is not as expected.

        Raises:
            McpError: If the underlying `nl2sql` call fails after all retry
            attempts.
        """

        async def get_gene_symbol() -> List[str]:
            return await _cached_gene_symbol_lookup(
                bi_url=self.deep_genome_config.BI_URL,
                sql_headers=self._sql_headers,
                species_code=species_code,
                gene_id=gene_id,
                timeout=self.deep_genome_config.TIMEOUT,
            )

        if semaphore is not None:
            async with semaphore:
                return await get_gene_symbol()
        else:
            return await get_gene_symbol()

    async def _gene_annotation(
        self,
        species_code: str,
        gene_id: str,
        semaphore: Optional[asyncio.Semaphore] = None,
    ):
        async def get_gene_annotation() -> Dict:
            return await _cached_gene_annotation_lookup(
                bi_url=self.deep_genome_config.BI_URL,
                sql_headers=self._sql_headers,
                species_code=species_code,
                gene_id=gene_id,
                timeout=self.deep_genome_config.TIMEOUT,
            )

        if semaphore is not None:
            async with semaphore:
                return await get_gene_annotation()
        else:
            return await get_gene_annotation()

    async def run_orthologs_node(self, state: DeepGenomeState):
        """Retrieve orthologous gene symbols for network analysis.

        This node fetches gene symbols for orthologous genes in parallel using
        a semaphore to limit concurrency.

        Args:
            state: Current workflow state containing orthologs_data.

        Returns:
            Dict with updated orthologs_data including gene_symbol mapping.
        """
        print("=> Retrieving orthologous genes...")
        semaphore = asyncio.Semaphore(self.deep_genome_config.MAX_CONCURRENCY)
        orthologs_species_gene_list = state["orthologs_data"]["gene_list"]
        orthologs_symbol_tasks = [
            self.gene_symbol(
                species_code=each_species_code,
                gene_id=each_gene_id,
                semaphore=semaphore,
            )
            for each_species_code, each_gene_id in orthologs_species_gene_list
        ]
        orthologs_gene_symbol_results = await asyncio.gather(
            *(orthologs_symbol_tasks), return_exceptions=True
        )
        species_orthologs_gene_symbol_dict = {
            species_gene: res
            for species_gene, res in zip(
                orthologs_species_gene_list, orthologs_gene_symbol_results
            )
            if res and not isinstance(res, Exception)
        }
        print("Orthologous genes retrieval completed <=")
        return {
            "orthologs_data": {
                "gene_list": orthologs_species_gene_list,
                "gene_symbol": species_orthologs_gene_symbol_dict,
            }
        }

    async def run_paralogs_node(self, state: DeepGenomeState):
        """Retrieve paralogous gene symbols for network analysis.

        This node fetches gene symbols for paralogous genes in parallel using
        a semaphore.

        Args:
            state: Current workflow state containing paralogs_data.

        Returns:
            Dict with updated paralogs_data including gene_symbol mapping.
        """
        print("=> Retrieving paralogous genes...")
        semaphore = asyncio.Semaphore(self.deep_genome_config.MAX_CONCURRENCY)
        paralogs_species_gene_list = state["paralogs_data"]["gene_list"]
        paralogs_symbol_tasks = [
            self.gene_symbol(
                species_code=each_species_code,
                gene_id=each_gene_id,
                semaphore=semaphore,
            )
            for each_species_code, each_gene_id in paralogs_species_gene_list
        ]
        paralogs_gene_symbol_results = await asyncio.gather(
            *(paralogs_symbol_tasks), return_exceptions=True
        )
        species_paralogs_gene_symbol_dict = {
            species_gene: res
            for species_gene, res in zip(
                paralogs_species_gene_list, paralogs_gene_symbol_results
            )
            if res and not isinstance(res, Exception)
        }
        print("Paralogous genes retrieval completed <=")
        return {
            "paralogs_data": {
                "gene_list": paralogs_species_gene_list,
                "gene_symbol": species_paralogs_gene_symbol_dict,
            }
        }

    async def run_interaction_node(self, state: DeepGenomeState):
        """Retrieve interacting gene symbols for network analysis.

        This node fetches gene symbols for protein interaction partners in
        parallel using a semaphore to limit concurrency.

        Args:
            state: Current workflow state containing interaction_data.

        Returns:
            Dict with updated interaction_data including gene_symbol mapping.
        """
        print("=> Retrieving interacting genes...")
        semaphore = asyncio.Semaphore(self.deep_genome_config.MAX_CONCURRENCY)
        interaction_species_gene_list = state["interaction_data"]["gene_list"]
        interaction_genes = interaction_species_gene_list
        interaction_symbol_tasks = [
            self.gene_symbol(
                species_code=each_species_code,
                gene_id=each_gene_id,
                semaphore=semaphore,
            )
            for each_species_code, each_gene_id in interaction_genes
        ]
        interaction_gene_symbol_results = await asyncio.gather(
            *(interaction_symbol_tasks), return_exceptions=True
        )
        species_interaction_gene_symbol_dict = {
            species_gene: res
            for species_gene, res in zip(
                interaction_species_gene_list, interaction_gene_symbol_results
            )
            if res and not isinstance(res, Exception)
        }
        print("Interacting genes retrieval completed <=")
        return {
            "interaction_data": {
                "gene_list": interaction_species_gene_list,
                "gene_symbol": species_interaction_gene_symbol_dict,
            }
        }

    async def run_orthologs_annotation_node(self, state: DeepGenomeState):
        """Summarize orthologous gene network.

        This node fetches annotations for all orthologous genes and generates
        a formatted string summary using the network_to_string function.

        Args:
            state: Current workflow state containing orthologs_data.

        Returns:
            Dict with orthologs_summary and part1_completed_branches increment.
        """
        print("=> Summarizing orthologous gene network...")
        semaphore = asyncio.Semaphore(self.deep_genome_config.MAX_CONCURRENCY)
        orthologs_species_gene_list = state["orthologs_data"]["gene_list"]
        species_orthologs_gene_symbol_dict = state["orthologs_data"].get(
            "gene_symbol", {}
        )

        # Fetch annotations for all orthologs genes
        orthologs_anno_tasks = [
            self._gene_annotation(
                species_code=each_species_code,
                gene_id=each_gene_id,
                semaphore=semaphore,
            )
            for each_species_code, each_gene_id in orthologs_species_gene_list
        ]
        orthologs_gene_anno_results = await asyncio.gather(
            *(orthologs_anno_tasks), return_exceptions=True
        )
        species_orthologs_gene_anno_dict = {
            species_gene: res
            for species_gene, res in zip(
                orthologs_species_gene_list, orthologs_gene_anno_results
            )
            if res and not isinstance(res, Exception)
        }

        orthologs_string = network_to_string(
            orthologs_species_gene_list,
            species_orthologs_gene_symbol_dict,
            species_orthologs_gene_anno_dict,
            "Orthologous",
        )
        print("Orthologous gene network summary completed <=")
        return {
            "orthologs_summary": orthologs_string,
            "part1_completed_branches": 1,
        }

    async def run_paralogs_annotation_node(self, state: DeepGenomeState):
        """Summarize paralogous gene network.

        This node fetches annotations for all paralogous genes and generates
        a formatted string summary using the network_to_string function.

        Args:
            state: Current workflow state containing paralogs_data.

        Returns:
            Dict with paralogs_summary and part1_completed_branches increment.
        """
        print("=> Summarizing paralogous gene network...")
        semaphore = asyncio.Semaphore(self.deep_genome_config.MAX_CONCURRENCY)
        paralogs_species_gene_list = state["paralogs_data"]["gene_list"]
        species_paralogs_gene_symbol_dict = state["paralogs_data"].get(
            "gene_symbol", {}
        )

        # Fetch annotations for all paralogs genes
        paralogs_anno_tasks = [
            self._gene_annotation(
                species_code=each_species_code,
                gene_id=each_gene_id,
                semaphore=semaphore,
            )
            for each_species_code, each_gene_id in paralogs_species_gene_list
        ]
        paralogs_gene_anno_results = await asyncio.gather(
            *(paralogs_anno_tasks), return_exceptions=True
        )
        species_paralogs_gene_anno_dict = {
            species_gene: res
            for species_gene, res in zip(
                paralogs_species_gene_list, paralogs_gene_anno_results
            )
            if res and not isinstance(res, Exception)
        }

        paralogs_string = network_to_string(
            paralogs_species_gene_list,
            species_paralogs_gene_symbol_dict,
            species_paralogs_gene_anno_dict,
            "Paralogous",
        )
        print("Paralogous gene network summary completed <=")
        return {
            "paralogs_summary": paralogs_string,
            "part1_completed_branches": 1,
        }

    async def run_interaction_annotation_node(self, state: DeepGenomeState):
        """Summarize interacting gene network.

        This node fetches annotations for all interacting genes and generates
        a formatted string summary using the network_to_string function.

        Args:
            state: Current workflow state containing interaction_data.

        Returns:
            Dict with interaction_summary and part1 branch increment.
        """
        print("=> Summarizing interacting gene network...")
        semaphore = asyncio.Semaphore(self.deep_genome_config.MAX_CONCURRENCY)
        interaction_species_gene_list = state["interaction_data"]["gene_list"]
        interaction_genes = interaction_species_gene_list
        species_interaction_gene_symbol_dict = state["interaction_data"].get(
            "gene_symbol", {}
        )

        # Fetch annotations for all interaction genes
        interaction_anno_tasks = [
            self._gene_annotation(
                species_code=each_species_code,
                gene_id=each_gene_id,
                semaphore=semaphore,
            )
            for each_species_code, each_gene_id in interaction_genes
        ]
        interaction_gene_anno_results = await asyncio.gather(
            *(interaction_anno_tasks), return_exceptions=True
        )
        species_interaction_gene_anno_dict = {
            species_gene: res
            for species_gene, res in zip(
                interaction_species_gene_list, interaction_gene_anno_results
            )
            if res and not isinstance(res, Exception)
        }

        interaction_string = network_to_string(
            interaction_species_gene_list,
            species_interaction_gene_symbol_dict,
            species_interaction_gene_anno_dict,
            "Potential interacting",
        )
        print("Interacting gene network summary completed <=")
        return {
            "interaction_summary": interaction_string,
            "part1_completed_branches": 1,
        }

    async def run_part1_node(self, state: DeepGenomeState):
        """Barrier node for Part 1 - Gene Network Profile aggregation.

        This node waits for 4 parallel branches to complete, then generates
        an integrated gene function network report using LLM.

        Args:
            state: Current workflow state containing all network data.

        Returns:
            Dict with part1_report and experiment_completed_branches increment,
            or empty dict if barrier not yet satisfied.
        """
        # Barrier 1: Wait for 4 branches to complete
        print(
            "part1_completed_branches: ",
            state.get("part1_completed_branches", 0),
        )
        if state.get("part1_completed_branches", 0) < 4:
            return {}

        print(
            "\n[Merging] Part 1 Gene Network Profile data ready, "
            "generating integrated report..."
        )

        gene_id = state["gene_id"]
        species_code = state["species_code"]
        gene_annotation = state.get("gene_annotation", {})
        knowledge_context = state.get("knowledge_context", {})

        gene_string = gene_annotation.get("gene_string", "")
        description = gene_annotation.get("description", "")
        go_string = gene_annotation.get("go", "")
        interpro_string = gene_annotation.get("interpro", "")
        mapman_string = gene_annotation.get("mapman", "")
        retrieve_results = knowledge_context.get("literature", "")

        orthologs_summary = state.get("orthologs_summary", "")
        paralogs_summary = state.get("paralogs_summary", "")
        interaction_summary = state.get("interaction_summary", "")

        retrieve_context_list = []
        total_length = 0
        max_tokens = self.deep_genome_config.MAX_TOKENS
        for i, doc in enumerate(retrieve_results):
            header = f"[document {i+1} begin] {doc['title']}"
            content_field = (
                doc.get("big_content")
                if "big_content" in doc
                else doc.get("content", "")
            )
            body = (
                f"{doc['subtitle']}\n{content_field}"
                if doc.get("subtitle")
                else doc.get("content", "")
            )
            fragment = f"{header}\n{body} [document {i+1} end]"
            if total_length + len(fragment) <= max_tokens:
                retrieve_context_list.append(fragment)
                total_length += len(fragment)
            else:
                break
        retrieve_context = "\n\n".join(retrieve_context_list)

        prompt_vars = {
            "species": SPECIES_CODE_MAP[species_code],
            "gene_string": gene_string,
            "retrieve_results": retrieve_context,
            "description_string": description,
            "go_string": go_string,
            "interpro_string": interpro_string,
            "mapman_string": mapman_string,
            "orthologs_string": orthologs_summary,
            "paralogs_string": paralogs_summary,
            "interaction_string": interaction_summary,
        }

        user_query = get_prompt(
            self.deep_genome_config.PROMPT_FILE,
            "user/gene_function_network_anno",
            prompt_vars,
        )

        phyto_response = await phyto_chat(
            user_query=user_query,
            prompt_file=self.deep_genome_config.PROMPT_FILE,
            prompt_path=self.deep_genome_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            frequency_penalty=self.deep_genome_config.FREQUENCY_PENALTY,
            n=self.deep_genome_config.N,
            presence_penalty=self.deep_genome_config.PRESENCE_PENALTY,
            reasoning_effort=self.deep_genome_config.REASONING_EFFORT,
            response_format=self.deep_genome_config.RESPONSE_FORMAT,
            stream=self.deep_genome_config.STREAM,
            temperature=self.deep_genome_config.TEMPERATURE,
            top_p=self.deep_genome_config.TOP_P,
            user=self.deep_genome_config.USER,
            timeout=self.deep_genome_config.TIMEOUT,
            retriable_codes=self.deep_genome_config.RETRIABLE_CODES,
            max_retries=self.deep_genome_config.MAX_RETRIES,
        )

        if (
            phyto_response
            and "choices" in phyto_response
            and len(phyto_response["choices"]) > 0
            and "message" in phyto_response["choices"][0]
        ):
            part1_report = phyto_response["choices"][0]["message"].get(
                "content", ""
            )
        else:
            part1_report = f"Gene {gene_id} basic profile generation failed"

        return {
            "part1_report": part1_report,
            "experiment_completed_branches": 1,
        }

    async def run_report_synthesizer(self, state: DeepGenomeState):
        """Generate the report after all analysis branches finish."""
        # 🛡️ Barrier: 等待所有 analyst_node 完成后才执行汇总
        completed = state.get("analysis_completed_branches", 0)
        total_expected = len(state.get("analysis_tasks", []))

        # Test mode: skip synthesize_node and use mock data directly
        if state.get("skip_synthesize", False):
            print(
                "  [Test Mode] Skipping synthesize_node, using "
                "pre-prepared mock data"
            )
            return {"experiment_completed_branches": 1}

        if total_expected > 0 and completed < total_expected:
            print(
                "  [Barrier] Waiting for analysis completion: "
                f"{completed}/{total_expected}"
            )
            return {}

        print(
            "  [Barrier] All analysis completed "
            f"({completed}/{total_expected}), starting synthesis..."
        )

        gene_results_data = state.get("analyst_summaries", {})
        gene_results = get_prompt(
            self.deep_genome_config.PROMPT_FILE,
            "template/gene_function_result",
            gene_results_data,
        )
        obj_replace_dict = {
            "tree_path": "![Tree Image]()",
            "tissue_path": "![Tissue Image]()",
            "cultivar_path": "![Cultivar Image]()",
            "treatment_path": "![Treatment Image]()",
            "mutant_path": "![Genotype Image]()",
            "umap_path": "![Single_cell Umap Image]()",
            "violin_path": "![Single_cell Violin Image]()",
            "haplotype_path": "![Haplotype Image]()",
            "fst_path": "![fst Image]()",
            "motif_path": "![Motif Image]()",
            "smep_path": "![SMEP Image]()",
            "smoc_path": "![SMOC Image]()",
            "promoter_path": "![Promoter Design]()",
            "protein_path": "![Protein Design]()",
        }
        for obj_key, replace_content in obj_replace_dict.items():
            try:
                if gene_results_data[obj_key] == "":
                    gene_results = gene_results.replace(replace_content, "")
            except KeyError:
                continue
        results_path = (
            f"{self.deep_genome_config.DEEPGENOME_OUT}/"
            f"{state['gene_id']}_results.md"
        )
        with open(results_path, "w", encoding="utf-8") as fo:
            fo.write(gene_results)
        # print(gene_results)
        return {
            "synthesize_report": gene_results,
            "experiment_completed_branches": 1,
        }

    async def run_report_experiment(self, state: DeepGenomeState):
        """Barrier node for experiment recommendations generation.

        This is the "Ultimate Convergence" barrier. It waits for both
        part1_node and synthesize_node, then generates recommended
        experiments using LLM. It also retrieves detailed protocols.

        Args:
            state: Current workflow state containing part1_report and
                synthesize_report.

        Returns:
            Dict with experiment_report, part12_combined, and
            report_triggered flag, or empty dict if blocked.
        """
        # Barrier 3: Ultimate convergence - wait for part1 and synthesize
        if state.get("experiment_completed_branches", 0) < 2:
            return {}

        # Prevent duplicate execution
        if state.get("report_triggered", False):
            return {}

        print(
            "\n[Ultimate Convergence] Basic profile + Deep analysis merged! "
            "Designing recommended experiments..."
        )

        species_code = state["species_code"]
        gene_annotation = state.get("gene_annotation", {})
        gene_string = gene_annotation.get("gene_string", "")

        # 构建 part12_str (基础画像 + 计算画像)
        part1_str = state.get("part1_report", "")
        gene_results_data = state.get("synthesize_report", "")
        if gene_results_data:
            part2_str = str(gene_results_data)
        else:
            part2_str = ""
        part12_str = f"## Gene Profiles\n\n{part1_str}\n\n{part2_str}\n\n"

        # 生成推荐实验
        experiment_response = await phyto_chat(
            user_query=get_prompt(
                self.deep_genome_config.PROMPT_FILE,
                "user/gene_function_experiment",
                {
                    "gene_string": gene_string,
                    "species_string": SPECIES_CODE_MAP[species_code],
                    "content": part12_str,
                },
            ),
            prompt_file=self.deep_genome_config.PROMPT_FILE,
            prompt_path=self.deep_genome_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            frequency_penalty=self.deep_genome_config.FREQUENCY_PENALTY,
            n=self.deep_genome_config.N,
            presence_penalty=self.deep_genome_config.PRESENCE_PENALTY,
            reasoning_effort=self.deep_genome_config.REASONING_EFFORT,
            response_format=self.deep_genome_config.RESPONSE_FORMAT,
            stream=self.deep_genome_config.STREAM,
            temperature=self.deep_genome_config.TEMPERATURE,
            top_p=self.deep_genome_config.TOP_P,
            user=self.deep_genome_config.USER,
            timeout=self.deep_genome_config.TIMEOUT,
            retriable_codes=self.deep_genome_config.RETRIABLE_CODES,
            max_retries=self.deep_genome_config.MAX_RETRIES,
        )

        function_experiment = ""
        if (
            experiment_response
            and "choices" in experiment_response
            and len(experiment_response["choices"]) > 0
            and "message" in experiment_response["choices"][0]
        ):
            function_experiment = experiment_response["choices"][0][
                "message"
            ].get("content", "")

        # 解析 JSON 获取实验列表
        experiment_list = []
        start_index = function_experiment.find("[")
        end_index = function_experiment.rfind("]") + 1
        if start_index != -1 and end_index > start_index:
            try:
                json_part = function_experiment[start_index:end_index]
                experiment_list = loads(json_part)
            except (ValueError, TypeError):
                experiment_list = []

        # 对每个实验使用 KnowledgeAgent 获取详细 protocol
        protocol_sections = []
        for ei, experiment in enumerate(experiment_list):
            protocol_response = await self.knowledge_agent.arun(
                user_query=experiment,
                repo_id_dict={"44ad28b5-5c3b-4a02-8e8c-7fb4903424cb": 128},
                is_generate=True,
                is_follow_up=False,
            )

            protocol_content = ""
            if (
                protocol_response
                and "choices" in protocol_response
                and len(protocol_response["choices"]) > 0
                and "message" in protocol_response["choices"][0]
            ):
                protocol_content = protocol_response["choices"][0][
                    "message"
                ].get("content", "")

            protocol_sections.append(
                f"## {ei+1}. Step-by-Step {experiment} Protocol\n\n"
                f"{protocol_content}\n"
            )

        experiments_str = "".join(protocol_sections)

        return {
            "experiment_report": experiments_str,
            "part12_combined": part12_str,
            "report_triggered": True,
        }

    async def run_report_protocol(self, state: DeepGenomeState):
        """Generate experimental protocol summary.

        This node generates a summary of experimental protocols based on the
        recommended experiments and analysis sections.

        Args:
            state: Current workflow state containing part12_combined and
                experiment_report.

        Returns:
            Dict with protocol_report.
        """
        print("-> Generating experimental protocol summary...")

        part12_str = state.get("part12_combined") or ""
        experiment_report = state.get("experiment_report", "")

        protocol_response = await phyto_chat(
            user_query=get_prompt(
                self.deep_genome_config.PROMPT_FILE,
                "user/gene_function_protocol",
                {
                    "analysis_sections": part12_str,
                    "protocol_sections": experiment_report,
                },
            ),
            prompt_file=self.deep_genome_config.PROMPT_FILE,
            prompt_path=self.deep_genome_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            frequency_penalty=self.deep_genome_config.FREQUENCY_PENALTY,
            n=self.deep_genome_config.N,
            presence_penalty=self.deep_genome_config.PRESENCE_PENALTY,
            reasoning_effort=self.deep_genome_config.REASONING_EFFORT,
            response_format=self.deep_genome_config.RESPONSE_FORMAT,
            stream=self.deep_genome_config.STREAM,
            temperature=self.deep_genome_config.TEMPERATURE,
            top_p=self.deep_genome_config.TOP_P,
            user=self.deep_genome_config.USER,
            timeout=self.deep_genome_config.TIMEOUT,
            retriable_codes=self.deep_genome_config.RETRIABLE_CODES,
            max_retries=self.deep_genome_config.MAX_RETRIES,
        )

        protocol_content = ""
        if (
            protocol_response
            and "choices" in protocol_response
            and len(protocol_response["choices"]) > 0
            and "message" in protocol_response["choices"][0]
        ):
            protocol_content = protocol_response["choices"][0]["message"].get(
                "content", ""
            )

        return {"protocol_report": protocol_content}

    async def run_report_introduction(self, state: DeepGenomeState):
        """Generate report introduction section.

        This node generates the introduction section of the gene function
        report, with context based on gene annotation and analysis.

        Args:
            state: Current workflow state containing gene_annotation,
                part12_combined, etc.

        Returns:
            Dict with introduction_report, or empty dict if already triggered.
        """
        # Prevent duplicate execution
        if state.get("report_triggered", False):
            return {}

        print("-> Generating introduction...")

        species_code = state["species_code"]
        gene_annotation = state.get("gene_annotation", {})
        gene_string = gene_annotation.get("gene_string", "")

        use_analyst = state.get("config_params", {}).get(
            "use_analyst_agent", True
        )

        # 构建内容字符串
        part12_str = state.get("part12_combined") or ""
        if use_analyst:
            protocol_report = state.get("protocol_report", "")
            experiment_report = state.get("experiment_report", "")
            part123_str = (
                f"{part12_str}\n\n"
                f"## Recommended experiments\n\n{protocol_report}\n\n"
                f"{experiment_report}\n\n"
            )
            content = part123_str
        else:
            content = part12_str

        introduction_response = await phyto_chat(
            user_query=get_prompt(
                self.deep_genome_config.PROMPT_FILE,
                "user/gene_function_introduction",
                {
                    "gene_string": gene_string,
                    "species_string": SPECIES_CODE_MAP[species_code],
                    "content": content,
                },
            ),
            prompt_file=self.deep_genome_config.PROMPT_FILE,
            prompt_path=self.deep_genome_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            frequency_penalty=self.deep_genome_config.FREQUENCY_PENALTY,
            n=self.deep_genome_config.N,
            presence_penalty=self.deep_genome_config.PRESENCE_PENALTY,
            reasoning_effort=self.deep_genome_config.REASONING_EFFORT,
            response_format=self.deep_genome_config.RESPONSE_FORMAT,
            stream=self.deep_genome_config.STREAM,
            temperature=self.deep_genome_config.TEMPERATURE,
            top_p=self.deep_genome_config.TOP_P,
            user=self.deep_genome_config.USER,
            timeout=self.deep_genome_config.TIMEOUT,
            retriable_codes=self.deep_genome_config.RETRIABLE_CODES,
            max_retries=self.deep_genome_config.MAX_RETRIES,
        )

        introduction_content = ""
        if (
            introduction_response
            and "choices" in introduction_response
            and len(introduction_response["choices"]) > 0
            and "message" in introduction_response["choices"][0]
        ):
            introduction_content = introduction_response["choices"][0][
                "message"
            ].get("content", "")

        return {"introduction_report": introduction_content}

    async def run_report_discussion(self, state: DeepGenomeState):
        """Generate report discussion section.

        This node generates the discussion section of the gene function
        report, interpreting results and providing insights.

        Args:
            state: Current workflow state containing gene_annotation and all
                report sections.

        Returns:
            Dict with discussion_report.
        """
        print("-> Generating discussion...")

        gene_id = state["gene_id"]
        species_code = state["species_code"]
        gene_annotation = state.get("gene_annotation", {})
        gene_string = gene_annotation.get("gene_string", "")

        use_analyst = state.get("config_params", {}).get(
            "use_analyst_agent", True
        )

        # Build content string
        part12_str = state.get("part12_combined") or ""
        if use_analyst:
            protocol_report = state.get("protocol_report", "")
            experiment_report = state.get("experiment_report", "")
            introduction_report = state.get("introduction_report", "")
            part0123_str = (
                f"# Deep Genome Analysis of {gene_id}\n\n"
                f"{introduction_report}\n\n{part12_str}\n\n"
                f"## Recommended experiments\n\n{protocol_report}\n\n"
                f"{experiment_report}\n\n"
            )
            content = part0123_str
        else:
            content = part12_str

        discussion_response = await phyto_chat(
            user_query=get_prompt(
                self.deep_genome_config.PROMPT_FILE,
                "user/gene_function_discussion",
                {
                    "gene_string": gene_string,
                    "species_string": SPECIES_CODE_MAP[species_code],
                    "content": content,
                },
            ),
            prompt_file=self.deep_genome_config.PROMPT_FILE,
            prompt_path=self.deep_genome_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            frequency_penalty=self.deep_genome_config.FREQUENCY_PENALTY,
            n=self.deep_genome_config.N,
            presence_penalty=self.deep_genome_config.PRESENCE_PENALTY,
            reasoning_effort=self.deep_genome_config.REASONING_EFFORT,
            response_format=self.deep_genome_config.RESPONSE_FORMAT,
            stream=self.deep_genome_config.STREAM,
            temperature=self.deep_genome_config.TEMPERATURE,
            top_p=self.deep_genome_config.TOP_P,
            user=self.deep_genome_config.USER,
            timeout=self.deep_genome_config.TIMEOUT,
            retriable_codes=self.deep_genome_config.RETRIABLE_CODES,
            max_retries=self.deep_genome_config.MAX_RETRIES,
        )

        discussion_content = ""
        if (
            discussion_response
            and "choices" in discussion_response
            and len(discussion_response["choices"]) > 0
            and "message" in discussion_response["choices"][0]
        ):
            discussion_content = discussion_response["choices"][0][
                "message"
            ].get("content", "")

        return {"discussion_report": discussion_content}

    async def run_report_summary(self, state: DeepGenomeState):
        """Generate report conclusion and future outlook section.

        This node generates the summary and conclusion section of the gene
        function report, synthesizing findings and suggesting future research
        directions.

        Args:
            state: Current workflow state containing gene_annotation and all
                report sections.

        Returns:
            Dict with summary_report.
        """
        print("-> Generating conclusion and future outlook...")

        gene_id = state["gene_id"]
        species_code = state["species_code"]
        gene_annotation = state.get("gene_annotation", {})
        gene_string = gene_annotation.get("gene_string", "")

        use_analyst = state.get("config_params", {}).get(
            "use_analyst_agent", True
        )

        # 构建内容字符串
        part12_str = state.get("part12_combined") or ""
        discussion_report = state.get("discussion_report", "")
        if use_analyst:
            protocol_report = state.get("protocol_report", "")
            experiment_report = state.get("experiment_report", "")
            introduction_report = state.get("introduction_report", "")
            part014_str = (
                f"# Deep Genome Analysis of {gene_id}\n\n"
                f"{introduction_report}\n\n{part12_str}\n\n"
                f"## Recommended experiments\n\n{protocol_report}\n\n"
                f"{experiment_report}\n\n## Discussion\n\n"
                f"{discussion_report}\n\n"
            )
            content = part014_str
        else:
            part014_str = (
                f"{part12_str}\n\n## Discussion\n\n{discussion_report}\n\n"
            )
            content = part014_str

        summary_response = await phyto_chat(
            user_query=get_prompt(
                self.deep_genome_config.PROMPT_FILE,
                "user/gene_function_summary",
                {
                    "gene_string": gene_string,
                    "species_string": SPECIES_CODE_MAP[species_code],
                    "content": content,
                },
            ),
            prompt_file=self.deep_genome_config.PROMPT_FILE,
            prompt_path=self.deep_genome_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            frequency_penalty=self.deep_genome_config.FREQUENCY_PENALTY,
            n=self.deep_genome_config.N,
            presence_penalty=self.deep_genome_config.PRESENCE_PENALTY,
            reasoning_effort=self.deep_genome_config.REASONING_EFFORT,
            response_format=self.deep_genome_config.RESPONSE_FORMAT,
            stream=self.deep_genome_config.STREAM,
            temperature=self.deep_genome_config.TEMPERATURE,
            top_p=self.deep_genome_config.TOP_P,
            user=self.deep_genome_config.USER,
            timeout=self.deep_genome_config.TIMEOUT,
            retriable_codes=self.deep_genome_config.RETRIABLE_CODES,
            max_retries=self.deep_genome_config.MAX_RETRIES,
        )

        summary_content = ""
        if (
            summary_response
            and "choices" in summary_response
            and len(summary_response["choices"]) > 0
            and "message" in summary_response["choices"][0]
        ):
            summary_content = summary_response["choices"][0]["message"].get(
                "content", ""
            )

        return {"summary_report": summary_content}

    async def run_follow_up_node(self, state: DeepGenomeState):
        """Generate follow-up research questions.

        This node analyzes the complete gene function report and generates
        suggested follow-up research questions using LLM.

        Args:
            state: Current workflow state containing all report sections.

        Returns:
            Dict with final_report and follow_up_questions list.
        """
        print("-> Generating follow-up research questions...")

        gene_id = state["gene_id"]
        use_analyst = state.get("config_params", {}).get(
            "use_analyst_agent", True
        )

        # Assemble complete final report content
        part12_str = state.get("part12_combined") or ""
        introduction_report = state.get("introduction_report", "")
        discussion_report = state.get("discussion_report", "")
        summary_report = state.get("summary_report", "")
        experiment_report = state.get("experiment_report", "")
        protocol_report = state.get("protocol_report", "")

        if use_analyst:
            part0145_str = (
                f"# Deep Genome Analysis of {gene_id}\n\n"
                f"{introduction_report}\n\n"
                f"{part12_str}\n\n"
                f"## Recommended experiments\n\n"
                f"{protocol_report}\n\n"
                f"{experiment_report}\n\n"
                f"## Discussion\n\n"
                f"{discussion_report}\n\n"
                f"## Conclusion and Future Outlook\n\n"
                f"{summary_report}\n\n"
            )
        else:
            part0145_str = (
                f"# Deep Genome Analysis of {gene_id}\n\n"
                f"{part12_str}\n\n"
                f"## Discussion\n\n"
                f"{discussion_report}\n\n"
                f"## Conclusion and Future Outlook\n\n"
                f"{summary_report}\n\n"
            )

        # 生成 follow-up questions
        follow_up_response = await phyto_chat(
            user_query=get_prompt(
                self.deep_genome_config.PROMPT_FILE,
                "system/follow_up_questions",
                {
                    "user_query": f"Analyze the gene {gene_id}",
                    "system_response": part0145_str,
                },
            ),
            prompt_file=self.deep_genome_config.PROMPT_FILE,
            prompt_path=self.deep_genome_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            frequency_penalty=self.deep_genome_config.FREQUENCY_PENALTY,
            n=self.deep_genome_config.N,
            presence_penalty=self.deep_genome_config.PRESENCE_PENALTY,
            reasoning_effort=self.deep_genome_config.REASONING_EFFORT,
            response_format=self.deep_genome_config.RESPONSE_FORMAT,
            stream=self.deep_genome_config.STREAM,
            temperature=self.deep_genome_config.TEMPERATURE,
            top_p=self.deep_genome_config.TOP_P,
            user=self.deep_genome_config.USER,
            timeout=self.deep_genome_config.TIMEOUT,
            retriable_codes=self.deep_genome_config.RETRIABLE_CODES,
            max_retries=self.deep_genome_config.MAX_RETRIES,
        )

        follow_up_list = parse_follow_up_questions(
            message_content(follow_up_response)
        )
        return {
            "final_report": part0145_str,
            "follow_up_questions": follow_up_list,
        }


async def gene_function(
    species_code: str,
    gene_id: str,
    user_id: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Compatibility wrapper around the LangGraph deep genome agent."""
    deep_genome_config = copy_config_with_overrides(
        DEEP_GENOME_CONFIG,
        kwargs,
        DEEP_GENOME_CONFIG_FIELD_MAP,
        fixed_updates={"USER_ID": user_id},
    )
    sensitive_config = copy_sensitive_config_with_overrides(
        SENSITIVE_CONFIG,
        kwargs,
        field_map=ANALYST_SENSITIVE_FIELD_MAP,
        secret_field_map=DEEP_GENOME_SECRET_FIELD_MAP,
    )

    agent = get_cached_agent(
        "DeepGenomeAgents",
        lambda: DeepGenomeAgents(
            data_agent=DataAgent(
                data_config=deep_genome_config,
                sensitive_config=sensitive_config,
            ),
            knowledge_agent=KnowledgeAgent(
                knowledge_config=deep_genome_config,
                sensitive_config=sensitive_config,
            ),
            analyst_agent=AnalystAgent(
                analyst_config=deep_genome_config,
                sensitive_config=sensitive_config,
            ),
            deep_genome_config=deep_genome_config,
            sensitive_config=sensitive_config,
        ),
        agent_fingerprint_values(
            deep_genome_config=deep_genome_config,
            sensitive_config=sensitive_config,
        ),
    )
    return await agent.arun(
        species_code=species_code,
        gene_id=gene_id,
        config_params=kwargs.get("config_params"),
        thread_id=kwargs.get("thread_id"),
    )
