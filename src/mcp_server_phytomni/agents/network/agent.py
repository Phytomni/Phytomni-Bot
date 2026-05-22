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
from typing import (
    Any,
    Dict,
    List,
    Literal,
    Optional,
)

from langgraph.checkpoint.memory import MemorySaver

from ...common.prompts import get_prompt
from ...config.defaults import GeneNetworkConfig
from ...config.settings import SensitiveConfig
from ...runtime.langgraph_runner import ensure_checkpointer
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
    submit_analyst_analysis,
)
from ..shared.analysis_storage import get_data_list
from ..shared.parallel_dispatch import (
    ParallelDispatchSpec,
    ParallelDispatchState,
    build_parallel_dispatch_graph,
)

logger = logging.getLogger(__name__)

GENE_NETWORK_CONFIG = GeneNetworkConfig()
SENSITIVE_CONFIG = SensitiveConfig.load()
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
    ``completed_count``, ``error``) from ``ParallelDispatchState`` and
    adds the gene-network-specific fields below.

    Attributes:
        species: Latin species name in lowercase with spaces (e.g.,
            "oryza sativa", "arabidopsis thaliana").
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

    species: str  # Latin name lowercase with spaces (e.g. "oryza sativa")
    to_id: str  # Trait Ontology id formatted like "TO:0000207"
    user_id: str  # User identifier
    batch: bool  # Whether this is batch processing
    output_dir: Optional[str]
    network_task: Dict[str, Any]  # submit results
    network_tasks: List[Dict[str, Any]]  # List of network analysis tasks


class GeneNetworkAgents:
    """LangGraph-based agent for gene network analysis.

    This agent provides a workflow for analyzing gene networks in plant
    genomics, focusing on identifying relationships between genes and their
    regulatory networks. It leverages computational workflows to examine gene
    interactions, co-expression patterns, and functional associations.

    Attributes:
        checkpointer: LangGraph checkpointer for state persistence.
        analyst_agent: AnalystAgent instance for task execution.
        GENE_NETWORK_CONFIG: Gene network configuration.
        SENSITIVE_CONFIG: Sensitive configuration settings.
        app: Compiled LangGraph application.

    Example:
        >>> agents = GeneNetworkAgents()
        >>> result = await agents.arun(
        ...     species="osa",
        ...     to_id="TO:0000621"
        ... )
    """

    def __init__(
        self,
        checkpointer: Optional[MemorySaver] = None,
        analyst_agent: Optional[AnalystAgent] = None,
        gene_network_config=GENE_NETWORK_CONFIG,
        sensitive_config=SENSITIVE_CONFIG,
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
        self.sensitive_config = sensitive_config
        self.analyst_agent = analyst_agent or AnalystAgent(
            analyst_config=gene_network_config,
            sensitive_config=sensitive_config,
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
        species: str,
        to_id: str,
        output_dir: Optional[str] = None,
    ) -> dict:
        """Submit network analysis task and wait for completion.

        Args:
            analysis_type: Type of network analysis.
            species: Species name.
            to_id: Target gene identifier.
            output_dir: Optional output directory path.

        Returns:
            Dict containing task_id and output_dir.
        """
        goal_description, meta, data_list = self._analysis_prompt_parts(
            analysis_type,
            species,
            to_id,
        )

        return await submit_analyst_analysis(
            self.analyst_agent,
            self.gene_network_config,
            self.sensitive_config,
            {
                "analysis_type": analysis_type,
                "target_id": to_id,
                "output_dir": output_dir,
                "prompt_parts": (goal_description, meta, data_list),
                "compute_resource": self._get_compute_resource(analysis_type),
            },
        )

    def _analysis_prompt_parts(
        self,
        analysis_type: str,
        species: str,
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
            species,
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
        species = state["species"]
        to_id = state["to_id"]
        analysis_type = state["analysis_type"]

        logger.info(
            "[Network-%s] Executing: %s for %s",
            task_index,
            analysis_type,
            to_id,
        )
        logger.debug("Species: %s", species)

        return await capture_dispatched_analysis(
            state,
            analysis_type,
            "to_id",
            self._dispatch_and_wait_analysis,
            ("network_task", None),
        )

    async def arun(
        self,
        species: str,
        to_id: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Submit a gene network analysis task and return task_id.

        Args:
            species: Latin species name in lowercase with spaces (e.g.,
                "oryza sativa", "arabidopsis thaliana").
            to_id: Trait Ontology identifier for the target phenotype,
                formatted like "TO:0000207".
            user_id: Optional user identifier.
            batch: Whether this is batch processing.
            thread_id: Optional thread ID for checkpointer.

        Returns:
            Dict with task_ids on success, or error on failure.
        """
        return await run_analysis_graph(
            self.app,
            {"species": species, "to_id": to_id},
            kwargs,
            ("network_task", "error"),
            AnalysisStateSpec(
                tasks_key="network_tasks",
                result_inits={"network_task": {}},
            ),
        )


async def network_analysis(
    species: str,
    to_id: str,
    user_id: Optional[str] = None,
    batch: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Compatibility wrapper around the LangGraph gene network agent.

    Args:
        species: Target species name.
        to_id: Trait Ontology identifier for the target phenotype.
        user_id: Optional user identifier for output paths.
        batch: Whether to reuse provided output directories.
        **kwargs: Keyword-compatible analysis and sensitive overrides.

    Returns:
        Gene network task submission result.
    """
    agent = get_configured_analysis_agent(
        AnalysisAgentCacheSpec(
            "GeneNetworkAgents",
            "gene_network_config",
            GENE_NETWORK_CONFIG,
            GENE_NETWORK_CONFIG_FIELD_MAP,
            user_id,
        ),
        kwargs,
        SENSITIVE_CONFIG,
        lambda config, sensitive: GeneNetworkAgents(
            gene_network_config=config,
            sensitive_config=sensitive,
        ),
    )
    return await agent.arun(
        species=species,
        to_id=to_id,
        user_id=user_id,
        batch=batch,
        output_dir=kwargs.get("output_dir"),
    )
