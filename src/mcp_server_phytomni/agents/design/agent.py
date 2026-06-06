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

import logging
import operator
from typing import (
    Annotated,
    Any,
    Dict,
    List,
    Literal,
    NamedTuple,
    Optional,
)

from langgraph.checkpoint.memory import MemorySaver

from ...common.prompts import get_prompt
from ...config.defaults import DigitalDesignConfig
from ...config.settings import SensitiveConfig, get_sensitive_config
from ...graphs.analyst_dispatch_adapters import submit_analyst_via_subgraph
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

DIGITAL_DESIGN_CONFIG = DigitalDesignConfig()
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


class DigitalDesignState(ParallelDispatchState):
    """State schema for the digital design workflow.

    Inherits the shared parallel-dispatch bookkeeping fields
    (``analysis_type``, ``task_index``, ``task_ids``,
    ``completed_count``, ``error``, ``failures``) from
    ``ParallelDispatchState`` and adds the protein/promoter
    design-specific fields below.

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


class DigitalDesignAgents:
    """LangGraph-based agent for protein and promoter digital design.

    This agent provides a workflow for computational protein design and
    promoter analysis using LangGraph's parallel execution capabilities. It
    leverages the AnalystAgent to submit and manage design tasks.

    Attributes:
        checkpointer: LangGraph checkpointer for state persistence.
        analyst_agent: AnalystAgent instance for task execution.
        digital_design_config: Digital design configuration.
        sensitive_config: Sensitive configuration settings.
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
        sensitive_config: Optional[SensitiveConfig] = None,
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
        self.sensitive_config = sensitive_config or get_sensitive_config()
        self.analyst_agent = analyst_agent or AnalystAgent(
            analyst_config=digital_design_config,
            sensitive_config=self.sensitive_config,
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
        request = {
            "analysis_type": analysis_type,
            "target_id": gene_id,
            "output_dir": output_dir,
            "prompt_parts": (goal_description, meta, data_list),
            "compute_resource": self._get_compute_resource(analysis_type),
        }
        if self.digital_design_config.USE_ANALYST_SUBGRAPH:
            return await submit_analyst_via_subgraph(
                self.analyst_agent,
                self.digital_design_config,
                self.sensitive_config,
                request,
                is_polling=False,
            )
        return await submit_analyst_analysis(
            self.analyst_agent,
            self.digital_design_config,
            self.sensitive_config,
            request,
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

        logger.info(
            "[Design-%s] Executing: %s for %s",
            task_index,
            analysis_type,
            gene_id,
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
            ("design_task_result", "error", "failures"),
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
        get_sensitive_config(),
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


class _DesignAnalysisSpec(NamedTuple):
    """Static spec bundle for one module-level design analysis wrapper.

    Bundling the four ``analysis_type`` / ``goal_path`` / ``meta_path``
    / ``compute_resource`` fields keeps ``_submit_design_analysis``
    within the pylint ``too-many-arguments`` budget while preserving
    a single shared dispatch helper for both ``protein_structure_for_gene``
    and ``promoter_design_for_gene``.
    """

    analysis_type: str
    goal_path: str
    meta_path: str
    compute_resource: Literal["small", "medium", "large"]


async def _submit_design_analysis(
    species: str,
    gene_id: str,
    spec: _DesignAnalysisSpec,
    output_dir: Optional[str],
    *,
    is_polling: bool,
) -> dict[str, Any]:
    """Build prompt parts and dispatch one analyst submission.

    Shared helper for ``protein_structure_for_gene`` and
    ``promoter_design_for_gene``. Mirrors the
    ``DigitalDesignAgents._dispatch_and_wait_analysis`` shape used
    by the parallel-dispatch graph, but at module level so deep_genome
    can route its single-gene analysis branches here without
    constructing a full design graph.
    """
    sensitive = get_sensitive_config()
    goal_description = get_prompt(
        DIGITAL_DESIGN_CONFIG.PROMPT_FILE,
        spec.goal_path,
        {"gene_id": gene_id},
    )
    meta = get_prompt(DIGITAL_DESIGN_CONFIG.PROMPT_FILE, spec.meta_path)
    data_list = get_data_list(
        DIGITAL_DESIGN_CONFIG.DEEPGENOME_DATA,
        spec.analysis_type,
        species,
    )
    request = {
        "analysis_type": spec.analysis_type,
        "target_id": gene_id,
        "output_dir": output_dir,
        "prompt_parts": (goal_description, meta, data_list),
        "compute_resource": spec.compute_resource,
    }
    return await submit_analyst_via_subgraph(
        AnalystAgent(
            analyst_config=DIGITAL_DESIGN_CONFIG,
            sensitive_config=sensitive,
        ),
        DIGITAL_DESIGN_CONFIG,
        sensitive,
        request,
        is_polling=is_polling,
    )


async def protein_structure_for_gene(
    species: str,
    gene_id: str,
    output_dir: Optional[str] = None,
    *,
    is_polling: bool = True,
) -> dict[str, Any]:
    """Submit a protein_structure_analysis task via the analyst subgraph.

    Producer-side counterpart to deep_genome's
    ``protein_structure_analysis`` dispatch branch. Returns the
    ``submit_analyst_via_subgraph`` projection so deep_genome's
    commit-2 rerouting is a straight callee swap.

    Args:
        species: Source species name used to select prepared data.
        gene_id: Target gene identifier for the structure prompt.
        output_dir: Optional pre-allocated OBS output directory.
        is_polling: Whether the analyst graph should block until the
            submitted task reaches a terminal state. Defaults to
            ``True`` to preserve deep_genome's polling semantics.

    Returns:
        Projected dispatch dict containing ``task_id`` / ``output_dir``
        / ``plan`` / ``tool_usages`` / ``task_status``.
    """
    return await _submit_design_analysis(
        species=species,
        gene_id=gene_id,
        spec=_DesignAnalysisSpec(
            analysis_type="protein_structure_analysis",
            goal_path="user/structure_analysis",
            meta_path="user/structure_analysis_meta",
            compute_resource="medium",
        ),
        output_dir=output_dir,
        is_polling=is_polling,
    )


async def promoter_design_for_gene(
    species: str,
    gene_id: str,
    output_dir: Optional[str] = None,
    *,
    is_polling: bool = True,
) -> dict[str, Any]:
    """Submit a promoter_analysis task via the analyst subgraph.

    Producer-side counterpart to deep_genome's ``promoter_analysis``
    dispatch branch. Returns the ``submit_analyst_via_subgraph``
    projection so deep_genome's commit-2 rerouting is a straight
    callee swap.

    Args:
        species: Source species name used to select prepared data.
        gene_id: Target gene identifier for the promoter prompt.
        output_dir: Optional pre-allocated OBS output directory.
        is_polling: Whether the analyst graph should block until the
            submitted task reaches a terminal state. Defaults to
            ``True`` to preserve deep_genome's polling semantics.

    Returns:
        Projected dispatch dict containing ``task_id`` / ``output_dir``
        / ``plan`` / ``tool_usages`` / ``task_status``.
    """
    return await _submit_design_analysis(
        species=species,
        gene_id=gene_id,
        spec=_DesignAnalysisSpec(
            analysis_type="promoter_analysis",
            goal_path="user/promoter_analysis",
            meta_path="user/promoter_analysis_meta",
            compute_resource="small",
        ),
        output_dir=output_dir,
        is_polling=is_polling,
    )
