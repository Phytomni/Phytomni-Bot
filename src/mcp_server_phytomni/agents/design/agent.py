# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""LangGraph workflow for digital design task submission.

This module exposes `DigitalDesignState`, `DigitalDesignAgents`, and
`design_module`. It prepares protein and promoter design tasks, dispatches
them through AnalystAgent, and returns submitted task metadata.
"""

import operator
from typing import (
    Annotated,
    Any,
    Dict,
    List,
    Literal,
    Optional,
    TypedDict,
)

from langgraph.checkpoint.memory import MemorySaver

from ...common.prompts import get_prompt
from ...config.defaults import DigitalDesignConfig
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
    build_parallel_dispatch_graph,
    keep_last_error,
)

DIGITAL_DESIGN_CONFIG = DigitalDesignConfig()
SENSITIVE_CONFIG = SensitiveConfig.load()
DIGITAL_DESIGN_CONFIG_FIELD_MAP = {
    **ANALYST_CONFIG_FIELD_MAP,
    "deepgenome_data": "DEEPGENOME_DATA",
}
DIGITAL_DESIGN_TEMPLATE_PATHS = {
    "protein_design_analysis": (
        "user/protein_design_analysis",
        "user/protein_design_analysis_meta",
    ),
    "promoter_design_analysis": (
        "user/promoter_design_analysis",
        "user/promoter_design_analysis_meta",
    ),
}


class DigitalDesignState(TypedDict):
    """State schema for the digital design workflow.

    This TypedDict defines the state structure used throughout the protein
    and promoter digital design workflow, tracking species information, gene
    identifiers, task management, and result aggregation for parallel design
    task execution.

    Attributes:
        species: Latin species name in lowercase with spaces (e.g.,
            "arabidopsis thaliana", "oryza sativa").
        gene_id: Gene identifier for target protein or promoter.
        user_id: User identifier.
        batch: Whether this is batch processing.
        output_dir: Output directory path for results.
        design_tasks: List of design tasks to be executed.
        task_index: Current task index in parallel execution via Send API.
        task_ids: Mapping of task names to their corresponding task IDs.
        completed_count: Counter tracking the number of completed tasks.
        error: Error message if any task failed during execution.
    """

    species: str
    gene_id: str
    user_id: str
    batch: bool
    output_dir: Optional[str]
    design_task_result: Annotated[List[Dict[str, Any]], operator.add]
    design_tasks: List[Dict[str, Any]]  # List of design tasks
    analysis_type: str
    task_index: Optional[int]  # Current task index
    task_ids: Annotated[
        Dict[str, str], operator.or_
    ]  # Multiple task_ids: {"protein_design": "xxx", "other_task": "yyy"}
    completed_count: Annotated[int, operator.add]  # Completed task counter
    error: Annotated[Optional[str], keep_last_error]


class DigitalDesignAgents:
    """LangGraph-based agent for protein and promoter digital design.

    This agent provides a workflow for computational protein design and
    promoter analysis using LangGraph's parallel execution capabilities. It
    leverages the AnalystAgent to submit and manage design tasks.

    Attributes:
        checkpointer: LangGraph checkpointer for state persistence.
        analyst_agent: AnalystAgent instance for task execution.
        DIGITAL_DESIGN_CONFIG: Digital design configuration.
        SENSITIVE_CONFIG: Sensitive configuration settings.
        app: Compiled LangGraph application.

    Example:
        >>> agents = DigitalDesignAgents()
        >>> result = await agents.arun(
        ...     species="osa",
        ...     gene_id="Os01g0177400"
        ... )
    """

    def __init__(
        self,
        checkpointer: Optional[MemorySaver] = None,
        analyst_agent: Optional[AnalystAgent] = None,
        digital_design_config=DIGITAL_DESIGN_CONFIG,
        sensitive_config=SENSITIVE_CONFIG,
    ):
        """Initialize the DigitalDesignAgents.

        Args:
            checkpointer: LangGraph MemorySaver for state persistence.
            analyst_agent: Optional AnalystAgent instance. Creates one if
                omitted.
            digital_design_config: Digital design configuration object.
            sensitive_config: Sensitive configuration for credentials.
        """
        self.checkpointer = ensure_checkpointer(checkpointer)
        self.digital_design_config = digital_design_config
        self.sensitive_config = sensitive_config
        self.analyst_agent = analyst_agent or AnalystAgent(
            analyst_config=digital_design_config,
            sensitive_config=sensitive_config,
        )
        self.app = self._build_graph()

    def _build_graph(self):
        """Build the LangGraph workflow for digital design tasks."""
        return build_parallel_dispatch_graph(
            ParallelDispatchSpec(
                state_class=DigitalDesignState,
                prepare_node=self.prepare_tasks,
                work_node=self.run_design_node,
                route_fn=self.route_design_tasks,
                work_node_name="design_node",
            ),
            checkpointer=self.checkpointer,
        )

    def route_design_tasks(self, state: DigitalDesignState):
        """Dispatch design tasks in parallel using Send API.

        Args:
            state: Current digital design workflow state.

        Returns:
            LangGraph Send commands for each configured design task.
        """
        return route_analysis_tasks(
            "design_node",
            "gene_id",
            "design_tasks",
            state,
        )

    async def _dispatch_and_wait_analysis(
        self,
        analysis_type: str,
        species: str,
        gene_id: str,
        output_dir: Optional[str] = None,
    ) -> dict:
        """Submit task using AnalystAgent and wait for completion.

        Args:
            analysis_type: Type of design analysis.
            species: Species name.
            gene_id: Target gene identifier.
            output_dir: Optional output directory path.

        Returns:
            Dict containing task_id and output_dir.
        """
        goal_description, meta, data_list = self._analysis_prompt_parts(
            analysis_type,
            species,
            gene_id,
        )

        return await submit_analyst_analysis(
            self.analyst_agent,
            self.digital_design_config,
            self.sensitive_config,
            {
                "analysis_type": analysis_type,
                "target_id": gene_id,
                "output_dir": output_dir,
                "prompt_parts": (goal_description, meta, data_list),
                "compute_resource": self._get_compute_resource(analysis_type),
            },
        )

    def _analysis_prompt_parts(
        self,
        analysis_type: str,
        species: str,
        gene_id: str,
    ) -> tuple[str, str, Any]:
        """Return goal, meta, and data list for one design analysis."""
        paths = DIGITAL_DESIGN_TEMPLATE_PATHS.get(analysis_type)
        if paths is None:
            raise ValueError(f"Unknown analysis type: {analysis_type}")
        goal_path, meta_path = paths
        goal_description = get_prompt(
            self.digital_design_config.PROMPT_FILE,
            goal_path,
            {"gene_id": gene_id},
        )
        meta = get_prompt(self.digital_design_config.PROMPT_FILE, meta_path)
        data_list = get_data_list(
            self.digital_design_config.DEEPGENOME_DATA,
            analysis_type,
            species,
        )
        return goal_description, meta, data_list

    def _get_compute_resource(
        self, analysis_type: str
    ) -> Literal["small", "medium", "large"]:
        """Determine compute resource level based on analysis type."""
        medium_compute_types = {"protein_design_analysis"}
        if analysis_type in medium_compute_types:
            return "medium"
        return "small"

    async def prepare_tasks(self, state: DigitalDesignState) -> dict:
        """Prepare the list of design tasks.

        Args:
            state: Current digital design workflow state.

        Returns:
            State update with design tasks and counters initialized.
        """
        _ = state
        tasks = [
            {"analysis_type": "protein_design_analysis"},
            {"analysis_type": "promoter_design_analysis"},
        ]
        return {"design_tasks": tasks, "task_ids": {}, "completed_count": 0}

    async def run_design_node(self, state: DigitalDesignState) -> dict:
        """Execute a single design task dispatched via Send API.

        This node is called dynamically for each task in the design_tasks list.

        Args:
            state: Current state for one dispatched design task.

        Returns:
            State update with submitted task metadata or error details.
        """
        task_index = state.get("task_index")
        gene_id = state["gene_id"]
        analysis_type = state["analysis_type"]

        print(
            f"[Design-{task_index}] 🚀 Executing: {analysis_type} for {gene_id}"
        )

        return await capture_dispatched_analysis(
            state,
            analysis_type,
            "gene_id",
            self._dispatch_and_wait_analysis,
            ("design_task_result", "design_task_result"),
        )

    async def arun(
        self,
        species: str,
        gene_id: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Submit protein design tasks and return task_ids.

        Args:
            species: Latin species name in lowercase with spaces (e.g.,
                "arabidopsis thaliana", "oryza sativa").
            gene_id: Gene identifier.
            user_id: Optional user identifier.
            batch: Whether this is batch processing.
            thread_id: Optional thread ID for checkpointer.

        Returns:
            Dict with task_ids on success, or error on failure.
        """
        return await run_analysis_graph(
            self.app,
            {"species": species, "gene_id": gene_id},
            kwargs,
            ("design_task_result", "error"),
            AnalysisStateSpec(
                tasks_key="design_tasks",
                result_inits={"design_task_result": []},
            ),
        )


async def design_module(
    species: str,
    gene_id: str,
    user_id: Optional[str] = None,
    batch: bool = True,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Compatibility wrapper around the LangGraph digital design agent.

    Args:
        species: Target species name.
        gene_id: Target gene identifier.
        user_id: Optional user identifier for output paths.
        batch: Whether to reuse provided output directories.
        **kwargs: Keyword-compatible analysis and sensitive overrides.

    Returns:
        Digital design task submission result.
    """
    agent = get_configured_analysis_agent(
        AnalysisAgentCacheSpec(
            "DigitalDesignAgents",
            "digital_design_config",
            DIGITAL_DESIGN_CONFIG,
            DIGITAL_DESIGN_CONFIG_FIELD_MAP,
            user_id,
        ),
        kwargs,
        SENSITIVE_CONFIG,
        lambda config, sensitive: DigitalDesignAgents(
            digital_design_config=config,
            sensitive_config=sensitive,
        ),
    )
    return await agent.arun(
        species=species,
        gene_id=gene_id,
        user_id=user_id,
        batch=batch,
        output_dir=kwargs.get("output_dir"),
    )
