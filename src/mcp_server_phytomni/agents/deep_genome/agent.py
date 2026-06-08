# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Deep genome agents for gene profile, annotation, and synthesis.

Exports DeepGenomeAgents, workflow state and dependency types, cache
management helpers, network formatting re-exports, and the gene_function
compatibility wrapper used by MCP handlers.
"""

import asyncio
import contextlib
import logging
import operator
import sqlite3
from typing import Annotated, Any, Dict, List, NamedTuple, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from ...config.defaults import DeepGenomeConfig
from ...config.overrides import (
    CHAT_COMPLETION_CONFIG_FIELD_MAP,
    NL2SQL_CONFIG_FIELD_MAP,
    RETRIEVAL_CONFIG_FIELD_MAP,
    RETRY_CONFIG_FIELD_MAP,
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from ...config.settings import get_sensitive_config
from ...runtime.agent_registry import (
    agent_fingerprint_values,
    get_cached_agent,
)
from ...runtime.langgraph_runner import ainvoke_graph, ensure_checkpointer
from ...runtime.task_manager import TaskManager, resolve_tasks_db_path
from ...storage.path_policy import IdFactory
from ..analyst.agent import (
    ANALYST_CONFIG_FIELD_MAP,
    ANALYST_SECRET_FIELD_MAP,
    ANALYST_SENSITIVE_FIELD_MAP,
    AnalystAgent,
)
from ..brief_gene.core import BriefGeneAgent
from ..knowledge.agent import KnowledgeAgent
from ..shared.knowledge_subgraph import build_knowledge_app
from .brief_gene_mount import DeepGenomeBriefGeneMountMixin
from .dispatch import DeepGenomeDispatchMixin
from .formatting import network_to_string
from .profile import (
    DeepGenomeProfileMixin,
    _cached_gene_annotation_lookup,
    _cached_gene_symbol_lookup,
    clear_gene_lookup_caches,
)
from .report import DeepGenomeReportMixin

logger = logging.getLogger(__name__)

DEEP_GENOME_CONFIG = DeepGenomeConfig()
_manager_cache: Dict[str, Any] = {}
__all__ = [
    "DeepGenomeAgents",
    "clear_gene_lookup_caches",
    "gene_function",
    "network_to_string",
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
    """Merge two branch result dictionaries for LangGraph reducers.

    Args:
        left: Current reducer dictionary.
        right: Incoming branch result dictionary.

    Returns:
        Merged dictionary where incoming values override existing keys.
    """
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
    task_submit_sleep: int
    analysis_tasks: List[Dict[str, Any]]
    raw_analyst_data: Annotated[Dict[str, Any], update_dict]
    analyst_summaries: Annotated[Dict, update_dict]
    synthesize_report: Optional[str]
    experiment_report: Optional[str]
    protocol_report: Optional[str]
    introduction_report: Optional[str]
    discussion_report: Optional[str]
    summary_report: Optional[str]
    follow_up_questions: Optional[List[str]]
    # M11 — brief_gene now owns the preamble (X3b A architecture).
    # The mount IO projection (deep_genome/brief_gene_mount.py)
    # writes these four section markdowns + introduction_report
    # verbatim from brief_gene's BriefGeneOutput. _summary_source_content
    # in report.py consumes them directly; the M5-era ``brief_response``
    # prefix path is removed in the same commit.
    section1_markdown: str
    section2_markdown: str
    section3_markdown: str
    section4_markdown: str
    part1_completed_branches: Annotated[int, operator.add]
    analysis_completed_branches: Annotated[int, operator.add]
    experiment_completed_branches: Annotated[int, operator.add]
    report_triggered: bool
    target_gene: str
    species: str
    analysis_type: str
    part12_combined: Optional[str]
    task_id: Optional[str]
    output_dir: Optional[str]
    report_dir: Optional[str]
    error: Optional[str]


class DeepGenomeAgentDeps(NamedTuple):
    """External agents used by the DeepGenome workflow.

    Attributes:
        knowledge_agent: KnowledgeAgent used for literature retrieval.
        analyst_agent: AnalystAgent used for deep analysis task dispatch.
        brief_gene_app: Compiled BriefGeneAgent subgraph for the
            brief_gene mount node. ``None`` (default) defers
            construction to the consuming code path; ``DeepGenomeAgents``
            populates it via ``_replace`` in ``__init__`` so the mount
            factory closes over a real ``CompiledStateGraph`` for xray
            expansion.
        knowledge_app: Compiled KnowledgeAgent subgraph for the
            ``USE_KNOWLEDGE_SUBGRAPH``-on path. ``None`` when the flag
            is off so the report mixin's
            ``_dispatch_knowledge_retrieve`` helper falls back to
            ``knowledge_agent.arun``.
    """

    knowledge_agent: KnowledgeAgent
    analyst_agent: AnalystAgent
    brief_gene_app: Any = None
    knowledge_app: Any = None


class DeepGenomeAgents(
    DeepGenomeBriefGeneMountMixin,
    DeepGenomeDispatchMixin,
    DeepGenomeProfileMixin,
    DeepGenomeReportMixin,
):
    """LangGraph-based agent for comprehensive gene function analysis.

    This agent provides a sophisticated workflow for analyzing gene function
    and related biological processes in plant genomes. It orchestrates multiple
    specialized agents including knowledge_agent and analyst_agent to perform
    parallel gene network and deep computational analysis.

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
        knowledge_agent: Knowledge agent for literature retrieval.
        analyst_agent: Analyst agent for submitting and managing tasks.
        checkpointer: LangGraph MemorySaver for state persistence.
        deep_genome_config: Deep genome configuration object.
        sensitive_config: Sensitive configuration settings.
        app: Compiled LangGraph application.

    Example:
        >>> agents = DeepGenomeAgents(
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
        knowledge_agent,
        analyst_agent,
        **kwargs: Any,
    ):
        """Initialize the DeepGenomeAgents.

        Args:
            knowledge_agent: Knowledge agent for literature retrieval.
            analyst_agent: Analyst agent for submitting and managing tasks.
            checkpointer: LangGraph MemorySaver for state persistence.
            deep_genome_config: Deep genome configuration object.
            sensitive_config: Sensitive configuration for credentials.
        """
        self._agents = DeepGenomeAgentDeps(
            knowledge_agent=knowledge_agent,
            analyst_agent=analyst_agent,
        )
        self.checkpointer = ensure_checkpointer(kwargs.get("checkpointer"))
        self.deep_genome_config = kwargs.get(
            "deep_genome_config", DEEP_GENOME_CONFIG
        )
        self.sensitive_config = (
            kwargs.get("sensitive_config") or get_sensitive_config()
        )
        self._figure_index = 1
        self._sql_headers = {
            "Content-Type": "application/json",
            "token": self.sensitive_config.BI_TOKEN.get_secret_value(),
        }
        # Build a per-instance compiled BriefGeneAgent subgraph so the
        # brief_gene_mount node (registered in ``_build_graph``)
        # closes over a real ``CompiledStateGraph`` and LangGraph's
        # ``find_subgraph_pregel`` walker can discover it for xray
        # expansion. The knowledge_agent instance is reused so the
        # func_cache layer dedups any redundant retrieve calls.
        # Stash the compiled app on ``_agents`` (DeepGenomeAgentDeps
        # NamedTuple) so the brief_gene_app sits alongside the other
        # deep_genome dependencies rather than adding another instance
        # attribute (pylint ``too-many-instance-attributes`` ceiling).
        # Per-instance compiled KnowledgeAgent subgraph for the
        # ``USE_KNOWLEDGE_SUBGRAPH``-on path. Mirrors the analyst /
        # brief_gene / review pattern: when the flag is True, the
        # report mixin's ``_dispatch_knowledge_retrieve`` helper
        # routes through this app's ``ainvoke`` instead of the
        # legacy ``knowledge_agent.arun``. ``None`` when the flag is
        # off so attempting to use the app on the wrong branch is a
        # loud ``NoneType`` error rather than a silent fallback.
        # Stashed on ``_agents`` (DeepGenomeAgentDeps NamedTuple)
        # alongside ``brief_gene_app`` so the per-instance
        # attribute count stays under pylint's
        # ``too-many-instance-attributes`` ceiling.
        knowledge_app = (
            build_knowledge_app(
                knowledge_config=self.deep_genome_config,
                sensitive_config=self.sensitive_config,
            )
            if self.deep_genome_config.USE_KNOWLEDGE_SUBGRAPH
            else None
        )
        self._agents = self._agents._replace(
            brief_gene_app=BriefGeneAgent(
                knowledge_agent=self._agents.knowledge_agent,
            ).app,
            knowledge_app=knowledge_app,
        )
        self.app = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(DeepGenomeState)

        # M11 — X3b A architecture topology completion. brief_gene's
        # mount node (``knowledge_node`` slot) substitutes for the
        # entire preamble pipeline (data_node + orthologs / paralogs /
        # interaction + their annotation sub-summaries + part1_node
        # aggregator + deep_genome's own ``_run_report_introduction``).
        # The mount IO projection writes ``gene_annotation`` +
        # ``knowledge_context`` + ``orthologs_data`` + ``paralogs_data``
        # + ``interaction_data`` + ``section1-4_markdown`` +
        # ``introduction_report`` + ``experiment_completed_branches: 1``
        # (the +1 the legacy ``part1_node`` used to write so the
        # experiment_node 2-source barrier still fires once
        # ``synthesize_node`` adds the analyst-side +1).
        workflow.add_node(
            "knowledge_node",
            self.make_brief_gene_mount_node(self._agents.brief_gene_app),
        )

        workflow.add_node("prepare_tasks_node", self._prepare_analysis_tasks)
        workflow.add_node("synthesize_node", self._run_report_synthesizer)
        workflow.add_node("analyst_node", self._run_analyst_node)

        workflow.add_node("experiment_node", self._run_report_experiment)
        workflow.add_node("protocol_node", self._run_report_protocol)
        workflow.add_node("discussion_node", self._run_report_discussion)
        workflow.add_node("summary_node", self._run_report_summary)
        workflow.add_node("follow_up_node", self._run_follow_up_node)

        workflow.add_conditional_edges(
            START,
            self._route_start,
            ["knowledge_node", "prepare_tasks_node"],
        )
        # brief_gene mount writes all the preamble fields plus the
        # experiment_completed_branches +1 contribution, so the
        # post-mount path goes directly to experiment_node (which
        # waits for the synthesize_node contribution too).
        workflow.add_edge("knowledge_node", "experiment_node")

        workflow.add_conditional_edges(
            "prepare_tasks_node", self._route_analyst_tasks, ["analyst_node"]
        )
        workflow.add_conditional_edges(
            "analyst_node", self._route_after_analyst, [END]
        )
        workflow.add_edge("prepare_tasks_node", "synthesize_node")
        # The synthesize barrier no longer routes to introduction_node
        # (deleted — brief_gene mount provides introduction_report
        # directly); the remaining targets are the synthesize-node
        # self-loop while waiting and experiment_node when the
        # analyst-side data is ready.
        workflow.add_conditional_edges(
            "synthesize_node",
            self._route_synthesize_barrier,
            ["synthesize_node", "experiment_node", END],
        )

        workflow.add_conditional_edges(
            "experiment_node",
            self._route_experiment_barrier,
            ["experiment_node", "protocol_node"],
        )
        # protocol → discussion → summary → follow_up (introduction_node
        # removed; introduction_report comes from mount and is read
        # directly by _summary_source_content for downstream prompts).
        workflow.add_edge("protocol_node", "discussion_node")
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
        """Submit a deep gene analysis run; return task identity immediately.

        DeepGenome is wired into the submit-style chokepoint
        (``runtime.submit_recorder.records_submission("deep_genome")``)
        and the HTTP run-aggregate path (``api/app.py``). To match that
        contract, ``arun`` mints an umbrella ``task_id`` synchronously,
        derives a placeholder ``output_dir`` under
        ``deep_genome_config.DEEPGENOME_OUT``, spawns the LangGraph
        workflow on the running event loop via
        ``asyncio.create_task`` (best-effort: a process exit before
        terminal loses the workflow), and returns the submit envelope
        so the caller can poll the umbrella row through
        ``GetTaskStatus`` / ``GET /v1/runs/{id}``.

        Args:
            species_code: Three-letter species code (e.g., 'osa', 'ath').
            gene_id: Target gene identifier.
            **kwargs: Optional ``config_params``, ``thread_id``,
                ``test_mode``, ``mock_analyst_data``.

        Returns:
            Submit envelope ``{"task_id", "output_dir",
            "compute_resource"}``. The chokepoint persists the
            umbrella row from this dict; the background coroutine
            updates that row with ``"succeeded"`` / ``"failed"`` when
            the LangGraph workflow returns.
        """
        config_params = kwargs.get("config_params") or {}

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
            logger.info("[TEST MODE] Using pre-prepared analyst data")
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
            logger.debug(
                "skip_synthesize: %s", initial_state["skip_synthesize"]
            )

        umbrella_id = IdFactory().new_id("task", "deep_genome")
        output_root = (
            self.deep_genome_config.DEEPGENOME_OUT
            or self.deep_genome_config.OUTPUT_DIR
            or "/tmp"
        )
        umbrella_output_dir = f"{output_root.rstrip('/')}/{umbrella_id}"
        initial_state["task_id"] = umbrella_id
        initial_state["output_dir"] = umbrella_output_dir

        workflow_task = asyncio.create_task(
            ainvoke_graph(
                self.app,
                initial_state,
                thread_id=kwargs.get("thread_id"),
            )
        )
        workflow_task.add_done_callback(
            lambda task: self._finalize_workflow(
                task,
                umbrella_id=umbrella_id,
                output_dir=umbrella_output_dir,
            )
        )

        return {
            "task_id": umbrella_id,
            "output_dir": umbrella_output_dir,
            "compute_resource": "deep-genome",
        }

    def _finalize_workflow(
        self,
        task: "asyncio.Task[Any]",
        *,
        umbrella_id: str,
        output_dir: str,
    ) -> None:
        """Stamp the terminal task status after the background workflow ends.

        Runs as the ``add_done_callback`` for the LangGraph workflow
        task spawned from ``arun``. Reading the exception through
        ``task.exception()`` (which returns the exception object instead
        of raising) is what lets us record *any* failure category
        without writing ``except Exception`` — LangGraph, LLM, storage,
        and state-shape errors all flow through the same single
        ``Task.exception()`` accessor, so the previous broad-except sink
        becomes a typed exception handle here.

        The success/failure decision is made *before* the terminal
        registry write so a SQLite hiccup on the success path cannot
        flip a succeeded run to ``"failed"`` (the prior nested-except
        shape would do this — the outer except caught the SQLite
        exception from the success-side ``update_task`` and then wrote
        a misleading ``"failed"``). ``contextlib.suppress`` wraps the
        terminal write for the realistic registry error types
        (``sqlite3.Error`` for WAL / lock failures, ``OSError`` for
        full-disk / FS unavailable). Cancellation surfaces as
        ``asyncio.CancelledError`` on the task and is recorded as
        ``"failed"`` so a polling client never hangs.

        Args:
            task: Completed LangGraph workflow task whose
                ``exception()`` decides the terminal row status.
            umbrella_id: Synthetic task id stamped by ``arun``.
            output_dir: Placeholder path the terminal update keeps in
                sync with the caller-visible response.
        """
        if task.cancelled():
            status = "failed"
            logger.warning(
                "DeepGenome background workflow cancelled for %s",
                umbrella_id,
            )
        elif (exc := task.exception()) is not None:
            status = "failed"
            logger.error(
                "DeepGenome background workflow failed for %s: %s",
                umbrella_id,
                exc,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
        else:
            status = "succeeded"

        with contextlib.suppress(sqlite3.Error, OSError):
            TaskManager(resolve_tasks_db_path()).update_task(
                umbrella_id, status, "", output_dir
            )


async def gene_function(
    species_code: str,
    gene_id: str,
    user_id: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run the LangGraph deep genome analysis workflow.

    Args:
        species_code: Three-letter species code for the target gene.
        gene_id: Target gene identifier to analyze.
        user_id: Optional user id fixed into runtime path configuration.
        **kwargs: Optional config, credential, retrieval, BI, OBS, task,
            cache, thread_id, and config_params overrides.

    Returns:
        Final DeepGenome workflow state containing reports, task outputs, and
        follow-up analysis fields.
    """
    deep_genome_config = copy_config_with_overrides(
        DEEP_GENOME_CONFIG,
        kwargs,
        DEEP_GENOME_CONFIG_FIELD_MAP,
        fixed_updates={"USER_ID": user_id},
    )
    sensitive_config = copy_sensitive_config_with_overrides(
        get_sensitive_config(),
        kwargs,
        field_map=ANALYST_SENSITIVE_FIELD_MAP,
        secret_field_map=DEEP_GENOME_SECRET_FIELD_MAP,
    )

    agent = get_cached_agent(
        "DeepGenomeAgents",
        lambda: DeepGenomeAgents(
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
