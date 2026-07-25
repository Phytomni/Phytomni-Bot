# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""LangGraph agents for plant gene network analysis workflows.

This module exposes `GeneNetworkState`, `GeneNetworkAgents`, and
`network_analysis`. It prepares trait-associated network tasks, dispatches
them through AnalystAgent, and returns submitted task metadata.
"""

import logging
import operator
from collections.abc import Mapping
from typing import (
    Annotated,
    Any,
    Literal,
)

from langgraph.checkpoint.base import BaseCheckpointSaver

from ...common.prompts import get_prompt
from ...config.defaults import GeneNetworkConfig
from ...config.settings import SensitiveConfig, get_sensitive_config
from ...graphs.analyst_dispatch_adapters import submit_analyst_via_subgraph
from ...runtime.langgraph_runner import ensure_checkpointer
from ...runtime.locale import SupportedLocale
from ...runtime.request_context import bind_accepted_task_ids
from ...runtime.submission_outcome import (
    AcceptedSubmission,
    RejectedSubmission,
    SubmissionOutcome,
    classify_submissions,
)
from ..analyst.agent import (
    ANALYST_CONFIG_FIELD_MAP,
    AnalystAgent,
)
from ..shared.analysis import (
    AnalysisAgentCacheSpec,
    AnalysisStateSpec,
    capture_dispatched_analysis,
    get_configured_analysis_agent,
    route_analysis_tasks,
    run_analysis_graph,
)
from ..shared.analysis_storage import get_data_list
from ..shared.options import resolve_agent_locale
from ..shared.parallel_dispatch import (
    ParallelDispatchSpec,
    ParallelDispatchState,
    build_parallel_dispatch_graph,
)
from ..shared.remote_analysis import (
    REMOTE_SUBMISSION_ERRORS,
    RemoteAnalysisSubmissionError,
    accepted_submission,
    rejected_submission,
)

logger = logging.getLogger(__name__)

GENE_NETWORK_CONFIG = GeneNetworkConfig()
GENE_NETWORK_CONFIG_FIELD_MAP = {
    **ANALYST_CONFIG_FIELD_MAP,
    "deepgenome_data": "DEEPGENOME_DATA",
}
GENE_NETWORK_TEMPLATE_PATHS = {
    "gene_network_analysis": (
        "user/gene_network_analysis",
        "user/gene_network_analysis_meta",
    )
}


class GeneNetworkState(ParallelDispatchState):
    """State schema for the gene network analysis workflow.

    Inherits the shared parallel-dispatch bookkeeping fields
    (``analysis_type``, ``task_index``, ``task_ids``,
    ``completed_count``, ``error``, ``failures``) from
    ``ParallelDispatchState`` and
    adds the gene-network-specific fields below.

    Attributes:
        species_code: Three-letter species code (e.g., "osa", "ath").
        to_id: Trait Ontology identifier for the target phenotype,
            formatted like "TO:0000207".
        user_id: User identifier.
        batch: Whether this is batch processing.
        output_dir: Output directory path for results.
        network_tasks: List of network analysis tasks to be executed.
        task_index: Current task index in parallel execution via Send API.
        task_ids: Mapping of task names to their corresponding task IDs.
        completed_count: Counter tracking the number of completed tasks.
        error: Error message if any task failed during execution.
    """

    species_code: str  # Three-letter species code (e.g. "osa")
    to_id: str  # Trait Ontology id formatted like "TO:0000207"
    locale: SupportedLocale
    user_id: str  # User identifier
    batch: bool  # Whether this is batch processing
    output_dir: str | None
    network_task: Annotated[dict[str, Any], operator.or_]  # submit results
    network_tasks: list[dict[str, Any]]  # List of network analysis tasks
    submission_rejections: Annotated[list[dict[str, str]], operator.add]


def _project_network_submission_update(
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Remove a rejection sentinel and retain its safe rejection record."""
    result = updates.get("network_task")
    if not isinstance(result, Mapping):
        return updates
    rejected = result.get("_submission_rejected")
    if not isinstance(rejected, Mapping):
        return updates
    goal = rejected.get("goal")
    code = rejected.get("code")
    updates["network_task"] = {}
    if isinstance(goal, str) and isinstance(code, str):
        updates["submission_rejections"] = [{"goal": goal, "code": code}]
    return updates


def _network_submission_outcome(
    result: Mapping[str, Any],
) -> SubmissionOutcome:
    """Classify the one Network submission represented by a result."""
    accepted: list[AcceptedSubmission] = []
    task = result.get("network_task")
    if isinstance(task, Mapping):
        task_id = task.get("task_id")
        if isinstance(task_id, str) and task_id.strip():
            accepted.append(accepted_submission(task))

    rejected: list[RejectedSubmission] = []
    state = result.get("phytomni_state")
    raw_rejections = (
        state.get("submission_rejections")
        if isinstance(state, Mapping)
        else None
    )
    if isinstance(raw_rejections, list):
        for item in raw_rejections:
            if not isinstance(item, Mapping):
                continue
            goal = item.get("goal")
            code = item.get("code")
            if isinstance(goal, str) and isinstance(code, str):
                rejected.append(RejectedSubmission(goal=goal, code=code))
    if not accepted and not rejected:
        target = (
            state.get("to_id")
            if isinstance(state, Mapping)
            else result.get("to_id")
        )
        rejected.append(
            RejectedSubmission(
                goal=(
                    target.strip()
                    if isinstance(target, str) and target.strip()
                    else "gene_network_analysis"
                ),
                code="missing_task_id",
            )
        )
    return classify_submissions(accepted=accepted, rejected=rejected)


class GeneNetworkAgents:
    """LangGraph-based agent for gene network analysis.

    This agent provides a workflow for analyzing gene networks in plant
    genomics, focusing on identifying relationships between genes and their
    regulatory networks. It leverages computational workflows to examine gene
    interactions, co-expression patterns, and functional associations.

    Attributes:
        checkpointer: LangGraph checkpointer for state persistence.
        analyst_agent: AnalystAgent instance for task execution.
        gene_network_config: Gene network configuration.
        sensitive_config: Sensitive configuration settings.
        app: Compiled LangGraph application.

    Example:
        >>> agents = GeneNetworkAgents()
        >>> result = await agents.arun(
        ...     species_code="osa",
        ...     to_id="TO:0000621"
        ... )
    """

    def __init__(
        self,
        checkpointer: BaseCheckpointSaver | None = None,
        analyst_agent: AnalystAgent | None = None,
        gene_network_config=GENE_NETWORK_CONFIG,
        sensitive_config: SensitiveConfig | None = None,
    ):
        """Initialize the GeneNetworkAgents.

        Args:
            checkpointer: LangGraph MemorySaver for state persistence.
            analyst_agent: Optional AnalystAgent instance. Creates one if
                omitted.
            gene_network_config: Gene network configuration object.
            sensitive_config: Sensitive configuration for credentials.
        """
        self.checkpointer = ensure_checkpointer(checkpointer)
        self.gene_network_config = gene_network_config
        self.sensitive_config = sensitive_config or get_sensitive_config()
        self.analyst_agent = analyst_agent or AnalystAgent(
            analyst_config=gene_network_config,
            sensitive_config=self.sensitive_config,
        )
        self.app = self._build_graph()

    def _build_graph(self):
        """Build the LangGraph workflow for gene network analysis tasks."""
        return build_parallel_dispatch_graph(
            ParallelDispatchSpec(
                state_class=GeneNetworkState,
                prepare_node=self.prepare_tasks,
                work_node=self.run_network_node,
                route_fn=self.route_network_tasks,
                work_node_name="network_node",
            ),
            checkpointer=self.checkpointer,
        )

    def route_network_tasks(self, state: GeneNetworkState):
        """Dispatch network analysis tasks in parallel using Send API.

        Args:
            state: Current gene network workflow state.

        Returns:
            LangGraph Send commands for each configured network task.
        """
        return route_analysis_tasks(
            "network_node",
            "to_id",
            "network_tasks",
            state,
        )

    async def _dispatch_and_wait_analysis(
        self,
        analysis_type: str,
        species_code: str,
        to_id: str,
        output_dir: str | None = None,
    ) -> dict:
        """Submit network analysis task and wait for completion.

        Args:
            analysis_type: Type of network analysis.
            species_code: Three-letter species code (e.g., "osa").
            to_id: Target gene identifier.
            output_dir: Optional output directory path.

        Returns:
            Dict containing task_id and output_dir.
        """
        goal_description, meta, data_list = self._analysis_prompt_parts(
            analysis_type,
            species_code,
            to_id,
        )
        request = {
            "analysis_type": analysis_type,
            "target_id": to_id,
            "output_dir": output_dir,
            "prompt_parts": (goal_description, meta, data_list),
            "compute_resource": self._get_compute_resource(analysis_type),
        }
        result = await submit_analyst_via_subgraph(
            self.analyst_agent,
            self.gene_network_config,
            self.sensitive_config,
            request,
            is_polling=False,
        )
        accepted_submission(result)
        return result

    def _analysis_prompt_parts(
        self,
        analysis_type: str,
        species_code: str,
        to_id: str,
    ) -> tuple[str, str, Any]:
        """Return goal, meta, and data list for one network analysis."""
        paths = GENE_NETWORK_TEMPLATE_PATHS.get(analysis_type)
        if paths is None:
            raise ValueError(f"Unknown analysis type: {analysis_type}")
        goal_path, meta_path = paths
        goal_description = get_prompt(
            self.gene_network_config.PROMPT_FILE,
            goal_path,
            {"to_id": to_id},
        )
        meta = get_prompt(self.gene_network_config.PROMPT_FILE, meta_path)
        data_list = get_data_list(
            self.gene_network_config.DEEPGENOME_DATA,
            analysis_type,
            species_code,
        )
        return goal_description, meta, data_list

    def _get_compute_resource(
        self, _analysis_type: str
    ) -> Literal["small", "medium", "large"]:
        """Determine compute resource level based on analysis type."""
        return "small"

    async def prepare_tasks(self, state: GeneNetworkState) -> dict:
        """Prepare the list of network analysis tasks.

        Args:
            state: Current gene network workflow state.

        Returns:
            State update with network tasks and counters initialized.
        """
        _ = state
        tasks = [{"analysis_type": "gene_network_analysis"}]
        return {"network_tasks": tasks, "task_ids": {}, "completed_count": 0}

    async def run_network_node(self, state: GeneNetworkState) -> dict:
        """Execute a single network analysis task dispatched via Send API.

        This node is called dynamically for each network task.

        Args:
            state: Current state for one dispatched network task.

        Returns:
            State update with submitted task metadata or error details.
        """
        task_index = state.get("task_index")
        species_code = state["species_code"]
        to_id = state["to_id"]
        analysis_type = state["analysis_type"]

        logger.info(
            "[Network-%s] Executing: %s for %s",
            task_index,
            analysis_type,
            to_id,
        )
        logger.debug("Species code: %s", species_code)

        async def _dispatch(
            a_type: str,
            species: str,
            target: str,
            out_dir: str | None,
        ) -> dict[str, Any]:
            try:
                return await self._dispatch_and_wait_analysis(
                    a_type,
                    species,
                    target,
                    out_dir,
                )
            except REMOTE_SUBMISSION_ERRORS as exc:
                return {
                    "_submission_rejected": {
                        "goal": target,
                        "code": rejected_submission(target, exc).code,
                    }
                }

        updates = await capture_dispatched_analysis(
            state,
            analysis_type,
            "to_id",
            _dispatch,
            ("network_task", None),
            captured_exceptions=(),
        )
        return _project_network_submission_update(updates)

    async def arun(
        self,
        species_code: str,
        to_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Submit a gene network analysis task and return task_id.

        Args:
            species_code: Three-letter species code (e.g., "osa", "ath").
            to_id: Trait Ontology identifier for the target phenotype,
                formatted like "TO:0000207".
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
                "to_id": to_id,
                "locale": resolve_agent_locale(kwargs.get("locale")),
            },
            kwargs,
            ("network_task", "error", "failures"),
            AnalysisStateSpec(
                tasks_key="network_tasks",
                result_inits={
                    "network_task": {},
                    "submission_rejections": [],
                },
            ),
        )
        outcome = _network_submission_outcome(result)
        if outcome.kind == "rejected":
            raise RemoteAnalysisSubmissionError("no remote task was accepted")
        bind_accepted_task_ids(outcome.task_ids)
        return {
            **result,
            "task_ids": list(outcome.task_ids),
            "submission_warnings": list(outcome.warnings),
        }


async def network_analysis(
    species_code: str,
    to_id: str,
    user_id: str | None = None,
    batch: bool = False,
    *,
    locale: SupportedLocale | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compatibility wrapper around the LangGraph gene network agent.

    Args:
        species_code: Three-letter species code (e.g., "osa", "ath").
        to_id: Trait Ontology identifier for the target phenotype.
        user_id: Optional user identifier for output paths.
        batch: Whether to reuse provided output directories.
        **kwargs: Keyword-compatible analysis and sensitive overrides.

    Returns:
        Gene network task submission result.
    """
    effective_locale = resolve_agent_locale(locale)
    agent = get_configured_analysis_agent(
        AnalysisAgentCacheSpec(
            "GeneNetworkAgents",
            "gene_network_config",
            GENE_NETWORK_CONFIG,
            GENE_NETWORK_CONFIG_FIELD_MAP,
            user_id,
        ),
        kwargs,
        get_sensitive_config(),
        lambda config, sensitive: GeneNetworkAgents(
            gene_network_config=config,
            sensitive_config=sensitive,
        ),
    )
    return await agent.arun(
        species_code=species_code,
        to_id=to_id,
        user_id=user_id,
        batch=batch,
        output_dir=kwargs.get("output_dir"),
        locale=effective_locale,
    )
