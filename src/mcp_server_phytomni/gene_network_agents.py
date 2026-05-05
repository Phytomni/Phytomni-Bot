# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: maoyc_0316@163.com
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""LangGraph-based gene network agents for plant bioinformatics research.

This module provides specialized agents for analyzing gene networks in plant
genomics, focusing on identifying and characterizing relationships between
genes and their regulatory networks. It leverages computational analysis
workflows to examine gene interactions, co-expression patterns, and functional
associations.

Key functionalities include:
- Network topology analysis and visualization
- Gene interaction prediction and validation
- Co-expression network construction
- Functional module identification
- Integration with plant-specific databases and resources
"""

from typing import Dict, List, Any, Literal, Optional, TypedDict
from uuid import uuid1

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from .utils import get_prompt
from .analyst_agents import AnalystAgent, get_data_list, create_output_dir
from .config.defaults import GeneNetworkConfig
from .config.settings import SensitiveConfig
from .langgraph_runner import ainvoke_graph, ensure_checkpointer

gnc = GeneNetworkConfig()
sc = SensitiveConfig.load()


class GeneNetworkState(TypedDict):
    """State schema for the gene network analysis workflow.

    This TypedDict defines the state structure used throughout the gene network
    analysis workflow, tracking species information, gene identifiers, task
    management, and result aggregation for parallel network analysis
    operations.

    Attributes:
        species: Species name (e.g., "rice", "arabidopsis").
        to_id: Target gene identifier for network analysis.
        user_id: User identifier.
        batch: Whether this is batch processing.
        output_dir: Output directory path for results.
        network_tasks: List of network analysis tasks to be executed.
        task_index: Current task index in parallel execution via Send API.
        task_ids: Mapping of task names to their corresponding task IDs.
        completed_count: Counter tracking the number of completed tasks.
        error: Error message if any task failed during execution.
    """

    species: str  # Species name (e.g., "rice", "arabidopsis")
    to_id: str  # Target gene identifier for network analysis
    user_id: str  # User identifier
    batch: bool  # Whether this is batch processing
    network_task: Dict[str, Any]  # submit results
    network_tasks: List[Dict[str, Any]]  # List of network analysis tasks
    analysis_type: str
    task_index: Optional[int]  # Current task index in parallel execution
    task_ids: Dict[str, str]  # Mapping of task names to task IDs
    completed_count: int  # Counter for completed tasks
    error: Optional[str]  # Error message if any task failed


class GeneNetworkAgents:
    """LangGraph-based agent for gene network analysis.

    This agent provides a workflow for analyzing gene networks in plant
    genomics, focusing on identifying relationships between genes and their
    regulatory networks. It leverages computational workflows to examine gene
    interactions, co-expression patterns, and functional associations.

    Attributes:
        checkpointer: LangGraph checkpointer for state persistence.
        analyst_agent: AnalystAgent instance for task execution.
        gnc: Gene network configuration.
        sc: Sensitive configuration settings.
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
        gene_network_config=gnc,
        sensitive_config=sc,
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
        self.analyst_agent = analyst_agent or AnalystAgent()
        self.gnc = gene_network_config
        self.sc = sensitive_config
        self.app = self._build_graph()

    def _build_graph(self):
        """Build the LangGraph workflow for gene network analysis tasks."""
        workflow = StateGraph(GeneNetworkState)

        workflow.add_node("prepare_tasks_node", self.prepare_tasks)
        workflow.add_node("network_node", self.run_network_node)

        workflow.add_edge(START, "prepare_tasks_node")

        # Use Send API for dynamic task dispatch
        workflow.add_conditional_edges(
            "prepare_tasks_node", self.route_network_tasks, ["network_node"]
        )
        workflow.add_edge("network_node", END)

        return workflow.compile(checkpointer=self.checkpointer)

    def route_network_tasks(self, state: GeneNetworkState):
        """Dispatch network analysis tasks in parallel using Send API."""
        tasks = state.get("network_tasks", [])
        return [
            Send(
                "network_node",
                {
                    "task_index": i,
                    "species": state["species"],
                    "to_id": state["to_id"],
                    **task,
                },
            )
            for i, task in enumerate(tasks)
        ]

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
        goal_template_map = {
            "gene_network_analysis": "user/gene_network_analysis"
        }
        meta_template_map = {
            "gene_network_analysis": "user/gene_network_analysis_meta"
        }

        goal_path = goal_template_map.get(analysis_type)
        meta_path = meta_template_map.get(analysis_type)
        if not goal_path:
            raise ValueError(f"Unknown analysis type: {analysis_type}")

        # Build goal_description
        goal_description = get_prompt(
            self.gnc.PROMPT_FILE, goal_path, {"to_id": to_id}
        )
        # Build meta prompt
        meta = get_prompt(self.gnc.PROMPT_FILE, meta_path)
        # Build data_list
        data_list = get_data_list(
            self.gnc.DEEPGENOME_DATA, analysis_type, species
        )

        # Determine compute resource level
        compute_resource = self._get_compute_resource(analysis_type)

        # Get output directory
        if not output_dir:
            output_dir = create_output_dir(
                user_id=self.gnc.USER_ID or str(uuid1()),
                task=f"{analysis_type}_task",
                access_key_id=self.sc.AccessKeyID.get_secret_value(),
                secret_access_key=self.sc.SecretAccessKey.get_secret_value(),
                obs_server=self.gnc.OBS_SERVER,
                bucket_name=self.gnc.BUCKET_NAME,
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
            thread_id=f"{to_id}_{analysis_type}_{uuid1()}",
        )

        task_id = result.get("task_id")
        print(f"=>{analysis_type} task completed (task_id: {task_id})")

        # return {"task_id": task_id, "output_dir": result.get("output_dir")}
        return {"network_task": result}

    def _get_compute_resource(
        self, analysis_type: str
    ) -> Literal["small", "medium", "large"]:
        """Determine compute resource level based on analysis type."""
        return "small"

    async def prepare_tasks(self, state: GeneNetworkState) -> dict:
        """Prepare the list of network analysis tasks."""
        tasks = [{"analysis_type": "gene_network_analysis"}]
        return {"network_tasks": tasks, "task_ids": {}, "completed_count": 0}

    async def run_network_node(self, state: GeneNetworkState) -> dict:
        """Execute a single network analysis task dispatched via Send API.

        This node is called dynamically for each network task.
        """
        task_index = state.get("task_index")
        species = state["species"]
        to_id = state["to_id"]
        analysis_type = state["analysis_type"]

        print(
            f"[Network-{task_index}] 🚀 Executing: {analysis_type} for {to_id}"
        )
        print(species)
        try:
            result = await self._dispatch_and_wait_analysis(
                analysis_type=analysis_type, species=species, to_id=to_id
            )
            # Update task_id for the corresponding task
            task_key = analysis_type.replace("_analysis", "")
            existing_task_ids: Dict[str, str] = state.get("task_ids", {})
            raw_task_result = result.get("network_task", {})
            task_result: Dict[str, Any] = (
                raw_task_result if isinstance(raw_task_result, dict) else {}
            )
            task_id = task_result.get("task_id")
            if task_id is not None:
                existing_task_ids[task_key] = str(task_id)
            return {
                "task_ids": existing_task_ids,
                "completed_count": 1,
                "network_task": task_result,
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
        to_id: str,
        user_id: Optional[str] = None,
        batch: bool = False,
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit a gene network analysis task and return task_id.

        Args:
            species: Species name (e.g., "rice", "arabidopsis").
            to_id: Target gene identifier for network analysis.
            user_id: Optional user identifier.
            batch: Whether this is batch processing.
            thread_id: Optional thread ID for checkpointer.

        Returns:
            Dict with task_ids on success, or error on failure.
        """
        initial_state: Dict[str, Any] = {
            "species": species,
            "to_id": to_id,
            "user_id": user_id,
            "batch": batch,
            "network_task": {},
            "network_tasks": [],
            "task_ids": {},
            "completed_count": 0,
            "error": None,
        }

        result = await ainvoke_graph(
            self.app, initial_state, thread_id=thread_id
        )
        return {
            "network_task": result.get("network_task"),
            "error": result.get("error"),
        }


async def network_analysis(
    species: str,
    to_id: str,
    user_id: Optional[str] = None,
    batch: bool = False,
    **_: Any,
) -> Dict[str, Any]:
    """Compatibility wrapper around the LangGraph gene network agent."""
    agent = GeneNetworkAgents()
    return await agent.arun(
        species=species,
        to_id=to_id,
        user_id=user_id,
        batch=batch,
    )
