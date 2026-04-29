# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""LangGraph-based in silico research agents for conducting computational
research based on scientific literature.

This module provides functions that extract research goals from scientific
papers and execute comprehensive computational research workflows using
LangGraph's parallel execution capabilities.
"""

from json import loads
from typing import Dict, List, Any, Optional, TypedDict
from uuid import uuid1

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from .chat_agents import phyto_chat
from .utils import get_prompt, download_list_convert
from .analyst_agents import AnalystAgent, create_output_dir
from .config.defaults import InSilicoResearchConfig
from .config.settings import SensitiveConfig

isrc = InSilicoResearchConfig()
sc = SensitiveConfig().load()


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
    research_tasks: List[Dict[str, Any]]  # List of research tasks
    task_index: Optional[int]  # Current task index
    task_ids: Dict[str, str]  # Mapping of task names to task IDs
    completed_count: int  # Counter for completed tasks
    error: Optional[str]


class InSilicoResearchAgents:
    """LangGraph-based agent for in silico research from scientific literature.

    This agent provides a workflow for extracting research goals from scientific
    papers and executing comprehensive computational research workflows using
    LangGraph's parallel execution capabilities.

    Attributes:
        checkpointer: LangGraph checkpointer for state persistence.
        analyst_agent: AnalystAgent instance for task execution.
        isrc: In silico research configuration.
        sc: Sensitive configuration settings.
        app: Compiled LangGraph application.
    """

    def __init__(
        self,
        checkpointer=MemorySaver(),
        analyst_agent: AnalystAgent = None,
        in_silico_config=isrc,
        sensitive_config=sc,
    ):
        """Initialize the InSilicoResearchAgents.

        Args:
            checkpointer: LangGraph MemorySaver for state persistence.
            analyst_agent: Optional AnalystAgent instance. If None, creates a new one.
            in_silico_config: In silico research configuration object.
            sensitive_config: Sensitive configuration for credentials.
        """
        self.checkpointer = checkpointer
        self.analyst_agent = analyst_agent or AnalystAgent()
        self.isrc = in_silico_config
        self.sc = sensitive_config
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
        """Dispatch research tasks in parallel using Send API."""
        tasks = state.get("research_tasks", [])
        return [
            Send("research_node", {"task_index": i, **task})
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
        total_length = 0
        if obs_file_list:
            upload_str_list = await download_list_convert(
                obs_file_list=obs_file_list,
                server_dir=self.isrc.TEMP_DIR,
                access_key_id=self.sc.AccessKeyID.get_secret_value(),
                secret_access_key=self.sc.SecretAccessKey.get_secret_value(),
                obs_server=self.isrc.OBS_SERVER,
                bucket_name=self.isrc.BUCKET_NAME,
                part_size=self.isrc.PART_SIZT,
                task_num=self.isrc.TASK_NUM,
                max_retries=self.isrc.MAX_RETRIES,
                max_concurrency=self.isrc.MAX_CONCURRENCY,
                max_workers=self.isrc.MAX_WORKERS,
            )
            upload_results = []
            for i, doc in enumerate(upload_str_list):
                fragment = (
                    f"[user upload file {i+1} begin]\n"
                    f"{doc}\n[user upload file {i+1} end]"
                )
                if total_length + len(fragment) <= self.isrc.MAX_TOKENS:
                    upload_results.append(fragment)
                    total_length += len(fragment)
                else:
                    break
            upload_context = "\n\n".join(upload_results)
            user_query = get_prompt(
                self.isrc.PROMPT_FILE,
                "user/in_silico_research_goals_file",
                {"upload_context": upload_context, "paper_text": user_query},
            )
        else:
            user_query = get_prompt(
                self.isrc.PROMPT_FILE,
                "user/in_silico_research_goals",
                {"paper_text": user_query},
            )

        phyto_response = await phyto_chat(
            user_query=user_query,
            prompt_file=self.isrc.PROMPT_FILE,
            prompt_path=self.isrc.PROMPT_PATH,
            api_key=self.sc.API_KEY.get_secret_value(),
            base_url=self.sc.BASE_URL,
            model=self.sc.MODEL_ID,
            frequency_penalty=self.isrc.FREQUENCY_PENALTY,
            n=self.isrc.N,
            presence_penalty=self.isrc.PRESENCE_PENALTY,
            reasoning_effort=self.isrc.REASONING_EFFORT,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "type": "array",
                    "description": "A list of research objectives derived from the paper.",
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
            stream=self.isrc.STREAM,
            temperature=self.isrc.TEMPERATURE,
            top_p=self.isrc.TOP_P,
            user=self.isrc.USER,
            timeout=self.isrc.TIMEOUT,
            retriable_codes=self.isrc.RETRIABLE_CODES,
            max_retries=self.isrc.MAX_RETRIES,
        )
        if phyto_response is None:
            return []
        return loads(phyto_response["choices"][0]["message"]["content"])

    async def _submit_research_task(
        self,
        goal_description: str,
        context: str,
        data_list: Dict[str, str],
        output_dir: str,
        task_name: str,
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
        print(f"  → Submitting research task via AnalystAgent: {task_name}")

        # 使用 AnalystAgent 提交任务
        result = await self.analyst_agent.arun(
            query=None,
            goal_description=goal_description,
            preset_data_list=data_list,
            preset_plan=context,  # Pass context as predefined plan
            output_dir=output_dir,
            compute_resource="medium",
            is_auto_select=False,
            is_polling=False,
            thread_id=f"{task_name}_{uuid1()}",
        )

        if result.get("task_status") == "FAILED_AT_AGENT_LEVEL":
            raise RuntimeError(
                f"AnalystAgent failed: {result.get('error_detail')}"
            )

        task_id = result.get("task_id")
        print(f"  → {task_name} task completed (task_id: {task_id})")

        return {"task_id": task_id, "output_dir": result.get("output_dir")}

    async def extract_goals_node(self, state: InSilicoResearchState) -> dict:
        """Extract research goals from scientific paper text.

        This node is the entry point of the workflow, analyzing the paper
        content to identify and extract research objectives.

        Args:
            state: Current workflow state containing paper_text and obs_file_list.

        Returns:
            Dict with extracted goals list and error status.
        """
        paper_text = state["paper_text"]
        obs_file_list = state.get("obs_file_list", [])

        print("  → Extracting research goals from paper...")
        try:
            goals = await self._extract_goals(paper_text, obs_file_list)
            print(f"  → Extracted {len(goals)} research goals")
            return {"goals": goals, "error": None}
        except Exception as e:
            print(f"  → Goal extraction failed: {str(e)}")
            return {"goals": [], "error": str(e)}

    async def prepare_tasks(self, state: InSilicoResearchState) -> dict:
        """Prepare the list of research tasks from extracted goals.

        Args:
            state: Current workflow state containing extracted goals.

        Returns:
            Dict with research_tasks, output_dir, task_ids, and completed_count.
        """
        goals = state.get("goals", [])
        output_dir = state.get("output_dir") or create_output_dir(
            user_id=state.get("user_id") or str(uuid1()),
            task="in_silico_research_task",
            access_key_id=self.sc.AccessKeyID.get_secret_value(),
            secret_access_key=self.sc.SecretAccessKey.get_secret_value(),
            obs_server=self.isrc.OBS_SERVER,
            bucket_name=self.isrc.BUCKET_NAME,
        )

        tasks = [
            {
                "goal_description": goal["goal"],
                "context": goal["context"],
                "task_name": f"research_goal_{i}",
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

        This node is called dynamically for each task in the research_tasks list.

        Args:
            state: Current workflow state containing task details.

        Returns:
            Dict with task_ids, completed_count, and optional error.
        """
        task_index = state.get("task_index")
        goal_description = state.get("goal_description")
        context = state.get("context")
        task_name = state.get("task_name")
        data_list = state.get("data_list", {})
        output_dir = state.get("output_dir")

        print(f"[Research-{task_index}] 🚀 Executing: {task_name}")

        try:
            result = await self._submit_research_task(
                goal_description=goal_description,
                context=context,
                data_list=data_list,
                output_dir=output_dir,
                task_name=task_name,
            )
            existing_task_ids = state.get("task_ids", {})
            existing_task_ids[task_name] = result.get("task_id")
            return {"task_ids": existing_task_ids, "completed_count": 1}
        except Exception as e:
            return {
                "task_ids": state.get("task_ids", {}),
                "completed_count": 1,
                "error": str(e),
            }

    async def arun(
        self,
        paper_text: str,
        data_list: Dict[str, str],
        user_id: Optional[str] = None,
        obs_file_list: List[str] = [],
        output_dir: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Async entry function - conduct in silico research and return task_ids.

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
        if thread_id is None:
            thread_id = str(uuid1())

        initial_state = {
            "paper_text": paper_text,
            "data_list": data_list,
            "user_id": user_id,
            "obs_file_list": obs_file_list,
            "output_dir": output_dir,
            "goals": [],
            "research_tasks": [],
            "task_ids": {},
            "completed_count": 0,
            "error": None,
        }

        config = {"configurable": {"thread_id": thread_id}}
        result = await self.app.ainvoke(initial_state, config)
        return {
            "task_ids": result.get("task_ids"),
            "goals": result.get("goals"),
            "error": result.get("error"),
        }


async def in_silico_research(
    user_query: str,
    data_list: Dict[str, str],
    user_id: Optional[str] = None,
    obs_file_list: Optional[List[str]] = None,
    output_dir: Optional[str] = None,
    **_: Any,
) -> Dict[str, Any]:
    """Compatibility wrapper around the LangGraph in-silico research agent."""
    agent = InSilicoResearchAgents()
    return await agent.arun(
        paper_text=user_query,
        data_list=data_list,
        user_id=user_id,
        obs_file_list=obs_file_list or [],
        output_dir=output_dir,
    )
