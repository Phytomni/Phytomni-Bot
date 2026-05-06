# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""This module provides LangGraph-based workflow for protein design
and computational structural analysis.

It includes functions that leverage computational biology and bioinformatics
tools to analyze protein structures, predict protein properties, and perform
digital design workflows for protein engineering applications.
"""

import operator
from typing import (
    Annotated,
    Dict,
    List,
    Any,
    Literal,
    Optional,
    TypedDict,
)
from uuid import uuid1

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from .agent_registry import agent_fingerprint_values, get_cached_agent
from .utils import get_prompt
from .analyst_agents import ANALYST_CONFIG_FIELD_MAP
from .analyst_agents import ANALYST_SECRET_FIELD_MAP
from .analyst_agents import ANALYST_SENSITIVE_FIELD_MAP
from .analyst_agents import AnalystAgent, get_data_list, create_output_dir
from .config.defaults import DigitalDesignConfig
from .config.overrides import (
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from .config.settings import SensitiveConfig
from .langgraph_runner import ainvoke_graph, ensure_checkpointer

DIGITAL_DESIGN_CONFIG = DigitalDesignConfig()
SENSITIVE_CONFIG = SensitiveConfig.load()
DIGITAL_DESIGN_CONFIG_FIELD_MAP = {
    **ANALYST_CONFIG_FIELD_MAP,
    "deepgenome_data": "DEEPGENOME_DATA",
}


class DigitalDesignState(TypedDict):
    """State schema for the digital design workflow.

    This TypedDict defines the state structure used throughout the protein
    and promoter digital design workflow, tracking species information, gene
    identifiers, task management, and result aggregation for parallel design
    task execution.

    Attributes:
        species: Species name (e.g., "Arabidopsis_thaliana").
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
    error: Optional[str]


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
        workflow = StateGraph(DigitalDesignState)

        workflow.add_node("prepare_tasks_node", self.prepare_tasks)
        workflow.add_node("design_node", self.run_design_node)

        workflow.add_edge(START, "prepare_tasks_node")

        # Use Send API for dynamic task dispatch
        workflow.add_conditional_edges(
            "prepare_tasks_node", self.route_design_tasks, ["design_node"]
        )
        workflow.add_edge("design_node", END)

        return workflow.compile(checkpointer=self.checkpointer)

    def route_design_tasks(self, state: DigitalDesignState):
        """Dispatch design tasks in parallel using Send API."""
        tasks = state.get("design_tasks", [])
        return [
            Send(
                "design_node",
                {
                    "task_index": i,
                    "species": state["species"],
                    "gene_id": state["gene_id"],
                    "output_dir": state.get("output_dir"),
                    **task,
                },
            )
            for i, task in enumerate(tasks)
        ]

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
        goal_template_map = {
            "protein_design_analysis": "user/protein_design_analysis",
            "promoter_design_analysis": "user/promoter_design_analysis",
        }
        meta_template_map = {
            "protein_design_analysis": "user/protein_design_analysis_meta",
            "promoter_design_analysis": "user/promoter_design_analysis_meta",
        }

        goal_path = goal_template_map.get(analysis_type)
        meta_path = meta_template_map.get(analysis_type)
        if not goal_path:
            raise ValueError(f"Unknown analysis type: {analysis_type}")

        # Build goal_description
        goal_description = get_prompt(
            self.digital_design_config.PROMPT_FILE,
            goal_path,
            {"gene_id": gene_id},
        )
        # Build meta prompt
        meta = get_prompt(self.digital_design_config.PROMPT_FILE, meta_path)
        # Build data_list
        data_list = get_data_list(
            self.digital_design_config.DEEPGENOME_DATA, analysis_type, species
        )

        # Determine compute resource level
        compute_resource = self._get_compute_resource(analysis_type)

        # Get output directory
        if not output_dir:
            access_key_id, secret_access_key = (
                self.sensitive_config.obs_credentials()
            )
            output_dir = create_output_dir(
                user_id=self.digital_design_config.USER_ID or str(uuid1()),
                task=f"{analysis_type}_task",
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                obs_server=self.digital_design_config.OBS_SERVER,
                bucket_name=self.digital_design_config.BUCKET_NAME,
            )

        print(f"  → Submitting {analysis_type} task via AnalystAgent...")

        # Submit task using AnalystAgent
        result = await self.analyst_agent.arun(
            query=None,
            goal_description=goal_description,
            preset_data_list=data_list,
            preset_plan=meta,  # Pass meta as predefined plan
            output_dir=output_dir,
            compute_resource=compute_resource,
            is_auto_select=False,  # Data already preset via data_list
            is_polling=False,  # Wait for task completion
            thread_id=f"{gene_id}_{analysis_type}_{uuid1()}",
        )

        task_id = result.get("task_id")
        print(f"=>{analysis_type} task completed (task_id: {task_id})")

        return {"submit_result": result}

    def _get_compute_resource(
        self, analysis_type: str
    ) -> Literal["small", "medium", "large"]:
        """Determine compute resource level based on analysis type."""
        medium_compute_types = {"protein_design_analysis"}
        if analysis_type in medium_compute_types:
            return "medium"
        return "small"

    async def prepare_tasks(self, state: DigitalDesignState) -> dict:
        """Prepare the list of design tasks."""
        tasks = [
            {"analysis_type": "protein_design_analysis"},
            {"analysis_type": "promoter_design_analysis"},
        ]
        return {"design_tasks": tasks, "task_ids": {}, "completed_count": 0}

    async def run_design_node(self, state: DigitalDesignState) -> dict:
        """Execute a single design task dispatched via Send API.

        This node is called dynamically for each task in the design_tasks list.
        """
        task_index = state.get("task_index")
        species = state["species"]
        gene_id = state["gene_id"]
        analysis_type = state["analysis_type"]

        print(
            f"[Design-{task_index}] 🚀 Executing: {analysis_type} for {gene_id}"
        )
        design_task_result = state.get("design_task_result", [])

        try:
            result = await self._dispatch_and_wait_analysis(
                analysis_type=analysis_type,
                species=species,
                gene_id=gene_id,
                output_dir=state.get("output_dir"),
            )
            raw_submit_result = result.get("submit_result", {})
            submit_result: Dict[str, Any] = (
                raw_submit_result
                if isinstance(raw_submit_result, dict)
                else {}
            )
            design_task_result.append(submit_result)
            # Update task_id for the corresponding task
            task_key = analysis_type.replace("_analysis", "")
            existing_task_ids = state.get("task_ids", {})
            task_id = submit_result.get("task_id")
            if task_id is not None:
                existing_task_ids[task_key] = str(task_id)
            return {
                "design_task_result": design_task_result,
                "task_ids": existing_task_ids,
                "completed_count": 1,
            }
        except Exception as e:
            return {
                "task_ids": state.get("task_ids", {}),
                "completed_count": 1,
                "error": str(e),
            }

    async def arun(
        self,
        species: str,
        gene_id: str,
        user_id: Optional[str] = None,
        batch: bool = False,
        output_dir: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit protein design tasks and return task_ids.

        Args:
            species: Species name (e.g., "Arabidopsis_thaliana").
            gene_id: Gene identifier.
            user_id: Optional user identifier.
            batch: Whether this is batch processing.
            thread_id: Optional thread ID for checkpointer.

        Returns:
            Dict with task_ids on success, or error on failure.
        """
        initial_state: Dict[str, Any] = {
            "species": species,
            "gene_id": gene_id,
            "user_id": user_id,
            "batch": batch,
            "output_dir": output_dir,
            "design_task_result": [],
            "design_tasks": [],
            "task_ids": {},
            "completed_count": 0,
            "error": None,
        }

        result = await ainvoke_graph(
            self.app, initial_state, thread_id=thread_id
        )
        return {
            "design_task_result": result.get("design_task_result"),
            "error": result.get("error"),
        }


async def design_module(
    species: str,
    gene_id: str,
    user_id: Optional[str] = None,
    batch: bool = True,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Compatibility wrapper around the LangGraph digital design agent."""
    digital_design_config = copy_config_with_overrides(
        DIGITAL_DESIGN_CONFIG,
        kwargs,
        DIGITAL_DESIGN_CONFIG_FIELD_MAP,
        fixed_updates={"USER_ID": user_id},
    )
    sensitive_config = copy_sensitive_config_with_overrides(
        SENSITIVE_CONFIG,
        kwargs,
        field_map=ANALYST_SENSITIVE_FIELD_MAP,
        secret_field_map=ANALYST_SECRET_FIELD_MAP,
    )
    agent = get_cached_agent(
        "DigitalDesignAgents",
        lambda: DigitalDesignAgents(
            digital_design_config=digital_design_config,
            sensitive_config=sensitive_config,
        ),
        agent_fingerprint_values(
            digital_design_config=digital_design_config,
            sensitive_config=sensitive_config,
        ),
    )
    return await agent.arun(
        species=species,
        gene_id=gene_id,
        user_id=user_id,
        batch=batch,
        output_dir=kwargs.get("output_dir"),
    )
