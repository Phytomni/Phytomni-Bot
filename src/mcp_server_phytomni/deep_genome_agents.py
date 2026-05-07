# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Deep genome agents for gene profile, annotation, and workflow synthesis."""

import operator
from typing import Annotated, Any, Dict, List, NamedTuple, Optional, TypedDict

import requests
from langgraph.graph import END, START, StateGraph

from .agent_registry import agent_fingerprint_values, get_cached_agent
from .analyst_agents import (
    ANALYST_CONFIG_FIELD_MAP,
    ANALYST_SECRET_FIELD_MAP,
    ANALYST_SENSITIVE_FIELD_MAP,
    AnalystAgent,
)
from .config.defaults import DeepGenomeConfig
from .config.overrides import (
    CHAT_COMPLETION_CONFIG_FIELD_MAP,
    NL2SQL_CONFIG_FIELD_MAP,
    RETRIEVAL_CONFIG_FIELD_MAP,
    RETRY_CONFIG_FIELD_MAP,
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from .config.settings import SensitiveConfig
from .data_agents import DataAgent
from .deep_genome_dispatch import DeepGenomeDispatchMixin
from .deep_genome_formatting import network_to_string
from .deep_genome_profile import (
    DeepGenomeProfileMixin,
    _cached_gene_annotation_lookup,
    _cached_gene_symbol_lookup,
    clear_gene_lookup_caches,
)
from .deep_genome_report import DeepGenomeReportMixin
from .knowledge_agents import KnowledgeAgent
from .langgraph_runner import ainvoke_graph, ensure_checkpointer

DEEP_GENOME_CONFIG = DeepGenomeConfig()
SENSITIVE_CONFIG = SensitiveConfig.load()
_manager_cache: Dict[str, Any] = {}
__all__ = [
    "DeepGenomeAgents",
    "clear_gene_lookup_caches",
    "gene_function",
    "network_to_string",
    "requests",
    "_cached_gene_annotation_lookup",
    "_cached_gene_symbol_lookup",
]
DEEP_GENOME_CONFIG_FIELD_MAP = {
    **ANALYST_CONFIG_FIELD_MAP,
    "batch": "BATCH",
    "epic_type": "EPIC_TYPE",
    "create_task_url": "CREATE_TASK_URL",
    "update_task_url": "UPDATE_TASK_URL",
    **NL2SQL_CONFIG_FIELD_MAP,
    "prompt_file": "PROMPT_FILE",
    "deepgenome_data": "DEEPGENOME_DATA",
    "output_dir": "OUTPUT_DIR",
    **RETRIEVAL_CONFIG_FIELD_MAP,
    **CHAT_COMPLETION_CONFIG_FIELD_MAP,
    **RETRY_CONFIG_FIELD_MAP,
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


class DeepGenomeAgentDeps(NamedTuple):
    """External agents used by the DeepGenome workflow."""

    data_agent: DataAgent
    knowledge_agent: KnowledgeAgent
    analyst_agent: AnalystAgent


class DeepGenomeAgents(
    DeepGenomeDispatchMixin,
    DeepGenomeProfileMixin,
    DeepGenomeReportMixin,
):
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
        **kwargs: Any,
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
        self._agents = DeepGenomeAgentDeps(
            data_agent=data_agent,
            knowledge_agent=knowledge_agent,
            analyst_agent=analyst_agent,
        )
        self.checkpointer = ensure_checkpointer(kwargs.get("checkpointer"))
        self.deep_genome_config = kwargs.get(
            "deep_genome_config", DEEP_GENOME_CONFIG
        )
        self.sensitive_config = kwargs.get(
            "sensitive_config", SENSITIVE_CONFIG
        )
        self._figure_index = 1
        self._sql_headers = {
            "Content-Type": "application/json",
            "token": self.sensitive_config.BI_TOKEN.get_secret_value(),
        }
        self.app = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(DeepGenomeState)

        workflow.add_node("knowledge_node", self._run_knowledge_agent)
        workflow.add_node(
            "gene_annotation_node", self._run_gene_annotation_node
        )
        workflow.add_node("gene_summary_node", self._run_gene_summary_node)
        workflow.add_node("data_node", self._run_data_agent)
        workflow.add_node("orthologs_node", self._run_orthologs_node)
        workflow.add_node("paralogs_node", self._run_paralogs_node)
        workflow.add_node("interaction_node", self._run_interaction_node)
        workflow.add_node(
            "orthologs_annotation_node", self._run_orthologs_annotation_node
        )
        workflow.add_node(
            "paralogs_annotation_node", self._run_paralogs_annotation_node
        )
        workflow.add_node(
            "interaction_annotation_node",
            self._run_interaction_annotation_node,
        )
        workflow.add_node("part1_node", self._run_part1_node)

        workflow.add_node("prepare_tasks_node", self._prepare_analysis_tasks)
        workflow.add_node("synthesize_node", self._run_report_synthesizer)

        workflow.add_node("analyst_node", self._run_analyst_node)

        workflow.add_node("experiment_node", self._run_report_experiment)
        workflow.add_node("protocol_node", self._run_report_protocol)
        workflow.add_node("introduction_node", self._run_report_introduction)
        workflow.add_node("discussion_node", self._run_report_discussion)
        workflow.add_node("summary_node", self._run_report_summary)
        workflow.add_node("follow_up_node", self._run_follow_up_node)

        workflow.add_conditional_edges(
            START,
            self._route_start,
            ["knowledge_node", "data_node", "prepare_tasks_node"],
        )
        workflow.add_edge("knowledge_node", "gene_annotation_node")
        workflow.add_conditional_edges(
            "knowledge_node",
            self._route_after_knowledge,
            ["gene_summary_node", "gene_annotation_node"],
        )
        workflow.add_edge("gene_annotation_node", "part1_node")
        workflow.add_conditional_edges(
            "gene_summary_node",
            self._route_after_gene_summary,
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

        workflow.add_conditional_edges(
            "prepare_tasks_node", self._route_analyst_tasks, ["analyst_node"]
        )
        workflow.add_conditional_edges(
            "analyst_node", self._route_after_analyst, [END]
        )
        workflow.add_edge("prepare_tasks_node", "synthesize_node")
        workflow.add_conditional_edges(
            "synthesize_node",
            self._route_after_synthesize,
            ["experiment_node", "introduction_node"],
        )

        workflow.add_conditional_edges(
            "part1_node",
            self._route_after_part1,
            [
                "experiment_node",
                "introduction_node",
            ],
        )

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
        **kwargs: Any,
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
        config_params = kwargs.get("config_params")
        if config_params is None:
            config_params = {}

        initial_state: Dict[str, Any] = {
            "species_code": species_code,
            "gene_id": gene_id,
            "config_params": config_params,
            "part1_completed_branches": 0,
            "experiment_completed_branches": 0,
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

        mock_analyst_data = kwargs.get("mock_analyst_data")
        if kwargs.get("test_mode", False) and mock_analyst_data:
            print("[TEST MODE] Using pre-prepared analyst data")
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
                        "synthesize_report",
                        "This is a pre-generated synthesis report.",
                    ),
                    "experiment_completed_branches": 2,
                    "analysis_completed_branches": task_count,
                    "skip_synthesize": True,
                }
            )
            print(f"skip_synthesize: {initial_state['skip_synthesize']}")

        result = await ainvoke_graph(
            self.app,
            initial_state,
            thread_id=kwargs.get("thread_id"),
        )
        return result


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
