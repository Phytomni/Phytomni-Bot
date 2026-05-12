# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""LangGraph-based in silico research agents for computational workflows.

Classes: ResearchTaskContext, InSilicoResearchState, InSilicoResearchAgents.
Functions: in_silico_research, extract_goals_node, prepare_tasks,
    run_research_node.
"""

from dataclasses import dataclass
from json import loads
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from ...common.prompts import get_prompt
from ...config.defaults import InSilicoResearchConfig
from ...config.overrides import (
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from ...config.settings import SensitiveConfig
from ...runtime.agent_registry import (
    agent_fingerprint_values,
    get_cached_agent,
)
from ...runtime.langgraph_runner import (
    capture_workflow_boundary,
    ensure_checkpointer,
)
from ...storage.downloads import download_upload_context
from ...storage.path_policy import RunIdentity
from ..analyst.agent import (
    ANALYST_CONFIG_FIELD_MAP,
    ANALYST_SECRET_FIELD_MAP,
    ANALYST_SENSITIVE_FIELD_MAP,
    AnalystAgent,
)
from ..chat.service import phyto_chat
from ..shared.analysis import (
    AnalysisStateSpec,
    capture_analysis_result,
    run_analysis_graph,
)
from ..shared.analysis_storage import create_output_dir

IN_SILICO_CONFIG = InSilicoResearchConfig()
SENSITIVE_CONFIG = SensitiveConfig.load()


@dataclass(frozen=True)
class ResearchTaskContext:
    """Resolved context for submitting one in-silico research task.

    Attributes:
        goal_description: Research objective submitted to AnalystAgent.
        context: Supporting plan or context for the task.
        data_list: Input datasets for the research task.
        output_dir: Output directory for generated results.
        task_name: Stable task label derived from the goal index.
        thread_id: LangGraph thread ID for the child AnalystAgent run.
    """

    goal_description: str
    context: str
    data_list: Dict[str, str]
    output_dir: str
    task_name: str
    thread_id: str


class InSilicoResearchState(TypedDict):
    """State schema for the in silico research workflow.

    This TypedDict defines the state structure used throughout the in silico
    research workflow, tracking paper content, data sources, extracted research
    goals, task management, and result aggregation for parallel research
    task execution.

    Attributes:
        paper_text: Scientific paper text to analyze for research goals.
        data_list: Dictionary of data sources for research.
        user_id: User identifier.
        obs_file_list: List of OBS file paths to include as context.
        output_dir: Output directory path for results.
        goals: List of extracted research objectives from the paper.
        research_tasks: List of research tasks to be executed.
        task_index: Current task index in parallel execution via Send API.
        task_ids: Mapping of task names to their corresponding task IDs.
        completed_count: Counter tracking the number of completed tasks.
        error: Error message if any task failed during execution.
    """

    paper_text: str
    data_list: Dict[str, str]
    user_id: str
    obs_file_list: List[str]
    output_dir: Optional[str]
    goals: List[Dict[str, str]]  # List of extracted research objectives
    research_tasks: List[Dict[str, str]]  # List of research tasks
    goal_description: str
    context: str
    task_name: str
    thread_id: str
    task_index: Optional[int]  # Current task index
    task_ids: Dict[str, str]  # Mapping of task names to task IDs
    completed_count: int  # Counter for completed tasks
    error: Optional[str]


class InSilicoResearchAgents:
    """LangGraph-based agent for in silico research from scientific literature.

    This agent provides a workflow for extracting research goals from
    scientific papers and executing computational research workflows using
    LangGraph's parallel execution capabilities.

    Attributes:
        checkpointer: LangGraph checkpointer for state persistence.
        analyst_agent: AnalystAgent instance for task execution.
        IN_SILICO_CONFIG: In silico research configuration.
        SENSITIVE_CONFIG: Sensitive configuration settings.
        app: Compiled LangGraph application.
    """

    def __init__(
        self,
        checkpointer: Optional[MemorySaver] = None,
        analyst_agent: Optional[AnalystAgent] = None,
        in_silico_config=IN_SILICO_CONFIG,
        sensitive_config=SENSITIVE_CONFIG,
    ):
        """Initialize the InSilicoResearchAgents.

        Args:
            checkpointer: LangGraph MemorySaver for state persistence.
            analyst_agent: Optional AnalystAgent instance. Creates one if
                omitted.
            in_silico_config: In silico research configuration object.
            sensitive_config: Sensitive configuration for credentials.
        """
        self.checkpointer = ensure_checkpointer(checkpointer)
        self.in_silico_config = in_silico_config
        self.sensitive_config = sensitive_config
        self.analyst_agent = analyst_agent or AnalystAgent(
            analyst_config=in_silico_config,
            sensitive_config=sensitive_config,
        )
        self.app = self._build_graph()

    def _build_graph(self):
        """Build the LangGraph workflow for in silico research tasks."""
        workflow = StateGraph(InSilicoResearchState)

        workflow.add_node("extract_goals_node", self.extract_goals_node)
        workflow.add_node("prepare_tasks_node", self.prepare_tasks)
        workflow.add_node("research_node", self.run_research_node)

        workflow.add_edge(START, "extract_goals_node")
        workflow.add_edge("extract_goals_node", "prepare_tasks_node")

        # Use Send API for dynamic task dispatch
        workflow.add_conditional_edges(
            "prepare_tasks_node", self.route_research_tasks, ["research_node"]
        )
        workflow.add_edge("research_node", END)

        return workflow.compile(checkpointer=self.checkpointer)

    def route_research_tasks(self, state: InSilicoResearchState):
        """Dispatch research tasks in parallel using Send API.

        Args:
            state: Current in-silico research workflow state.

        Returns:
            LangGraph Send commands for each extracted research task.
        """
        tasks = state.get("research_tasks", [])
        return [
            Send(
                "research_node",
                {
                    "task_index": i,
                    "data_list": state.get("data_list", {}),
                    "output_dir": state.get("output_dir"),
                    **task,
                },
            )
            for i, task in enumerate(tasks)
        ]

    async def _extract_goals(
        self, user_query: str, obs_file_list: List[str]
    ) -> List[Dict[str, str]]:
        """Extract research goals from scientific paper text.

        Args:
            user_query: The paper text or research query.
            obs_file_list: List of OBS file paths to include as context.

        Returns:
            List of research goal dictionaries with 'goal' and 'context' keys.
        """
        if obs_file_list:
            upload_context, _ = await download_upload_context(
                obs_file_list,
                self.in_silico_config,
                self.sensitive_config,
            )
            user_query = get_prompt(
                self.in_silico_config.PROMPT_FILE,
                "user/in_silico_research_goals_file",
                {"upload_context": upload_context, "paper_text": user_query},
            )
        else:
            user_query = get_prompt(
                self.in_silico_config.PROMPT_FILE,
                "user/in_silico_research_goals",
                {"paper_text": user_query},
            )

        phyto_response = await phyto_chat(
            user_query=user_query,
            prompt_file=self.in_silico_config.PROMPT_FILE,
            prompt_path=self.in_silico_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            frequency_penalty=self.in_silico_config.FREQUENCY_PENALTY,
            n=self.in_silico_config.N,
            presence_penalty=self.in_silico_config.PRESENCE_PENALTY,
            reasoning_effort=self.in_silico_config.REASONING_EFFORT,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "type": "array",
                    "description": (
                        "A list of research objectives derived from the paper."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "goal": {"type": "string"},
                            "context": {"type": "string"},
                        },
                        "required": ["goal", "context"],
                    },
                },
            },
            stream=self.in_silico_config.STREAM,
            temperature=self.in_silico_config.TEMPERATURE,
            top_p=self.in_silico_config.TOP_P,
            user=self.in_silico_config.USER,
            timeout=self.in_silico_config.TIMEOUT,
            retriable_codes=self.in_silico_config.RETRIABLE_CODES,
            max_retries=self.in_silico_config.MAX_RETRIES,
        )
        if phyto_response is None:
            return []
        return loads(phyto_response["choices"][0]["message"]["content"])

    async def _submit_research_task(
        self,
        task: ResearchTaskContext,
    ) -> dict:
        """Submit research task using AnalystAgent and wait for completion.

        Args:
            goal_description: Description of the research goal.
            context: Context information for the research.
            data_list: Dictionary of data sources for research.
            output_dir: Output directory path for results.
            task_name: Name identifier for the task.

        Returns:
            Dict containing task_id and output_dir.
        """
        print(
            f"  → Submitting research task via AnalystAgent: {task.task_name}"
        )

        result = await self.analyst_agent.arun(
            query=None,
            goal_description=task.goal_description,
            preset_data_list=task.data_list,
            preset_plan=task.context,  # Pass context as predefined plan
            output_dir=task.output_dir,
            compute_resource="medium",
            is_auto_select=False,
            is_polling=False,
            thread_id=task.thread_id,
        )

        if result.get("task_status") == "FAILED_AT_AGENT_LEVEL":
            raise RuntimeError(
                f"AnalystAgent failed: {result.get('error_detail')}"
            )

        task_id = result.get("task_id")
        print(f"  → {task.task_name} task completed (task_id: {task_id})")

        return {"task_id": task_id, "output_dir": result.get("output_dir")}

    async def extract_goals_node(self, state: InSilicoResearchState) -> dict:
        """Extract research goals from scientific paper text.

        This node is the entry point of the workflow, analyzing the paper
        content to identify and extract research objectives.

        Args:
            state: Current workflow state containing paper_text and
                obs_file_list.

        Returns:
            Dict with extracted goals list and error status.
        """
        paper_text = state["paper_text"]
        obs_file_list = state.get("obs_file_list", [])

        print("  → Extracting research goals from paper...")

        async def extract_goals() -> dict[str, Any]:
            """Extract goals and return the success state.

            Returns:
                State update containing extracted goals and no error.
            """
            goals = await self._extract_goals(paper_text, obs_file_list)
            print(f"  → Extracted {len(goals)} research goals")
            return {"goals": goals, "error": None}

        def failure_state(exc: Exception) -> dict[str, Any]:
            """Store goal extraction failures in workflow state.

            Args:
                exc: Exception raised during goal extraction.

            Returns:
                Failure state update with an empty goals list.
            """
            print(f"  → Goal extraction failed: {str(exc)}")
            return {"goals": [], "error": str(exc)}

        return await capture_workflow_boundary(extract_goals, failure_state)

    async def prepare_tasks(self, state: InSilicoResearchState) -> dict:
        """Prepare the list of research tasks from extracted goals.

        Args:
            state: Current workflow state containing extracted goals.

        Returns:
            Dict with research_tasks, output_dir, task_ids, and
            completed_count.
        """
        goals = state.get("goals", [])
        run_identity = RunIdentity.create(
            user_id=state.get("user_id"),
            scope="in_silico_research_task",
        )
        access_key_id, secret_access_key = (
            self.sensitive_config.obs_credentials()
        )
        output_dir = state.get("output_dir") or create_output_dir(
            user_id=run_identity.user_id,
            task="in_silico_research_task",
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=self.in_silico_config.OBS_SERVER,
            bucket_name=self.in_silico_config.BUCKET_NAME,
            run_identity=run_identity,
        )

        tasks = [
            {
                "goal_description": goal["goal"],
                "context": goal["context"],
                "task_name": f"research_goal_{i}",
                "thread_id": run_identity.scoped_id("thread", i),
            }
            for i, goal in enumerate(goals)
        ]

        return {
            "research_tasks": tasks,
            "output_dir": output_dir,
            "task_ids": {},
            "completed_count": 0,
        }

    async def run_research_node(self, state: InSilicoResearchState) -> dict:
        """Execute a single research task dispatched via Send API.

        This node is called dynamically for each research task.

        Args:
            state: Current workflow state containing task details.

        Returns:
            Dict with task_ids, completed_count, and optional error.
        """
        task_index = state.get("task_index")
        task_name = state["task_name"]
        output_dir = state.get("output_dir")
        if output_dir is None:
            raise ValueError("output_dir is required for research tasks")

        print(f"[Research-{task_index}] 🚀 Executing: {task_name}")

        async def submit_call() -> dict[str, Any]:
            """Submit one research task and return its raw result.

            Returns:
                AnalystAgent payload with ``task_id`` and ``output_dir``.
            """
            return await self._submit_research_task(
                ResearchTaskContext(
                    goal_description=state["goal_description"],
                    context=state["context"],
                    data_list=state.get("data_list", {}),
                    output_dir=output_dir,
                    task_name=task_name,
                    thread_id=state.get("thread_id", task_name),
                )
            )

        return await capture_analysis_result(
            state,
            analysis_type=task_name,
            submit_call=submit_call,
            result_key=None,
        )

    async def arun(
        self,
        paper_text: str,
        data_list: Dict[str, str],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Conduct in silico research and return task_ids.

        Args:
            paper_text: Scientific paper text to analyze.
            data_list: Dictionary of data sources for research.
            user_id: Optional user identifier.
            obs_file_list: List of OBS files to include as context.
            output_dir: Optional output directory path.
            thread_id: Optional thread ID for checkpointer.

        Returns:
            Dict with task_ids mapping research goals to task IDs.
        """
        return await run_analysis_graph(
            self.app,
            {
                "paper_text": paper_text,
                "data_list": data_list,
                "obs_file_list": kwargs.get("obs_file_list") or [],
            },
            kwargs,
            ("task_ids", "goals", "error"),
            AnalysisStateSpec(
                tasks_key="research_tasks",
                result_inits={"goals": []},
            ),
        )


async def in_silico_research(
    user_query: str,
    data_list: Dict[str, str],
    user_id: Optional[str] = None,
    obs_file_list: Optional[List[str]] = None,
    output_dir: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Compatibility wrapper around the LangGraph in-silico research agent.

    Args:
        user_query: Paper text or research context to decompose.
        data_list: Input datasets available for submitted research tasks.
        user_id: Optional user identifier for output paths.
        obs_file_list: Optional OBS paths for uploaded paper/context files.
        output_dir: Optional explicit output directory.
        **kwargs: Keyword-compatible analysis and sensitive overrides.

    Returns:
        In-silico research goals, task IDs, and any workflow error.
    """
    in_silico_config = copy_config_with_overrides(
        IN_SILICO_CONFIG,
        kwargs,
        ANALYST_CONFIG_FIELD_MAP,
        fixed_updates={
            "USER_ID": user_id,
            "OUTPUT_DIR": output_dir,
        },
    )
    sensitive_config = copy_sensitive_config_with_overrides(
        SENSITIVE_CONFIG,
        kwargs,
        field_map=ANALYST_SENSITIVE_FIELD_MAP,
        secret_field_map=ANALYST_SECRET_FIELD_MAP,
    )
    agent = get_cached_agent(
        "InSilicoResearchAgents",
        lambda: InSilicoResearchAgents(
            in_silico_config=in_silico_config,
            sensitive_config=sensitive_config,
        ),
        agent_fingerprint_values(
            in_silico_config=in_silico_config,
            sensitive_config=sensitive_config,
        ),
    )
    return await agent.arun(
        paper_text=user_query,
        data_list=data_list,
        user_id=user_id,
        obs_file_list=obs_file_list or [],
        output_dir=output_dir,
    )
