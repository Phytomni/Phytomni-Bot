# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Bioinformatics workflow agents for task planning and execution."""

import asyncio
import time
from typing import Any, Dict, List, Literal, Optional, TypedDict

from httpx import AsyncClient, Timeout
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, StateGraph
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from .analyst_graph_nodes import AnalystGraphMixin
from .analyst_storage import (
    ObsAccessOptions,
    ObsDownloadOptions,
    create_output_dir,
    delete_analyst_agents_data,
    download_obs_out,
    get_data_list,
    upload_analyst_agents_data,
)
from .auth.iam import get_token
from .config.defaults import AnalystConfig
from .config.overrides import (
    CHAT_COMPLETION_CONFIG_FIELD_MAP,
    OBS_TRANSFER_CONFIG_FIELD_MAP,
    RETRIEVAL_CONFIG_FIELD_MAP,
    RETRY_CONFIG_FIELD_MAP,
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from .config.settings import SensitiveConfig
from .runtime.agent_registry import agent_fingerprint_values, get_cached_agent
from .runtime.langgraph_runner import (
    ainvoke_graph,
    capture_workflow_boundary,
    ensure_checkpointer,
)
from .storage.path_policy import RunIdentity
from .utils import (
    JsonPostRequest,
    JsonPostRetry,
    request_response_with_retries,
)

ANALYST_CONFIG = AnalystConfig()
SENSITIVE_CONFIG = SensitiveConfig.load()
DEFAULT_ACCESS_KEY_ID, DEFAULT_SECRET_ACCESS_KEY = (
    SENSITIVE_CONFIG.obs_credentials()
)

__all__ = [
    "AnalystAgent",
    "ObsAccessOptions",
    "ObsDownloadOptions",
    "create_output_dir",
    "delete_analyst_agents_data",
    "download_obs_out",
    "get_data_list",
    "retrieve_plan_submit",
    "submit",
    "task_delete",
    "task_log",
    "task_status",
    "upload_analyst_agents_data",
    "wait_for_completion",
]

ANALYST_CONFIG_FIELD_MAP = {
    "analysis_url": "ANALYSIS_URL",
    "region": "ANALYSIS_REGION",
    "resource_dict": "RESOURCE",
    "app_id_dict": "APP_ID",
    "task_name": "TASK_NAME",
    "execute_code": "EXECUTE_CODE",
    "output_dir": "OUTPUT_DIR",
    **RETRIEVAL_CONFIG_FIELD_MAP,
    **CHAT_COMPLETION_CONFIG_FIELD_MAP,
    **OBS_TRANSFER_CONFIG_FIELD_MAP,
    **RETRY_CONFIG_FIELD_MAP,
    "max_poll": "MAX_POLL",
}
ANALYST_SENSITIVE_FIELD_MAP = {
    "base_url": "BASE_URL",
    "model": "MODEL_ID",
    "model_url": "CODER_URL",
    "model_name": "CODER_MODEL",
}
ANALYST_SECRET_FIELD_MAP = {
    "api_key": "API_KEY",
    "coder_api_key": "CODER_API_KEY",
    "access_key_id": "ACCESS_KEY_ID",
    "secret_access_key": "SECRET_ACCESS_KEY",
}


class AnalystAgentsState(TypedDict):
    """State schema for the AnalystAgent LangGraph workflow.

    This TypedDict defines the shared state that flows through each node
    in the AnalystAgent graph. Each node reads from and writes to this
    state as the graph processes a bioinformatics analysis request.

    Attributes:
        query: The original user input query.
        goal_description: The decomposed research goal/objective.
        obs_file_list: List of OBS files uploaded by the user.
        data_list: Dictionary mapping data file paths to their descriptions.
        output_dir: The output directory path for analysis results.
        compute_resource: The compute resource level (small, medium, large).
        job_name: The name of the compute job.
        method_context: Context retrieved from literature/SOPs for plan
            generation.
        plan: The analysis plan/workflow (may be empty initially).
        plan_feedback: Feedback from the critic node for plan revision.
        plan_retries: Number of plan generation retries.
        extracted_tools: List of tools extracted from the plan.
        tool_usages: Retrieved usage instructions for the extracted tools.
        task_id: The unique identifier of the submitted task.
        task_status: The current task status.
        is_polling: Whether to poll for task status updates.
        is_auto_select: Whether to automatically select relevant data files.
    """

    query: str
    goal_description: str
    obs_file_list: List
    data_list: Dict[str, str]
    output_dir: str
    compute_resource: str
    job_name: str
    method_context: Dict[str, str]
    plan: str
    plan_feedback: Optional[str]
    plan_retries: int
    extracted_tools: List
    tool_usages: str
    task_id: str
    task_status: str
    is_polling: bool
    is_auto_select: bool


class AnalystAgent(AnalystGraphMixin):
    """A LangGraph-based agent for bioinformatics workflows.

    This agent orchestrates a complex workflow that decomposes user queries,
    selects appropriate data sources, retrieves relevant bioinformatics
    methods and literature, generates analysis plans, extracts required
    tools, and submits computational tasks for execution.

    The workflow graph consists of nine main nodes:
        1. parse_query_node: Decomposes the query into goal, data_list,
           and plan.
        2. data_select_node: Selects data files from the available database.
        3. method_retrieve_node: Retrieves methods, SOPs, and literature.
        4. plan_node: Generates or revises the analysis plan.
        5. check_node: Validates the plan using a critic mechanism.
        6. tool_extract_node: Extracts required tools from the plan.
        7. tool_retrieve_node: Retrieves usage instructions for tools.
        8. submit_node: Submits the task to the computation platform.
        9. pooling_node: Polls task status until completion.

    Args:
        checkpointer: A LangGraph checkpointer for state persistence.
                      Defaults to a fresh MemorySaver instance.
        analyst_config: Configuration for the analyst agent.
                        Defaults to the global ANALYST_CONFIG instance.
        sensitive_config: Configuration for sensitive data (e.g., API keys).
                          Defaults to the global SENSITIVE_CONFIG instance.

    Attributes:
        checkpointer: The checkpointer for state persistence.
        ANALYST_CONFIG: The analyst configuration instance.
        SENSITIVE_CONFIG: The sensitive configuration instance.
        app: The compiled LangGraph application.
    """

    def __init__(
        self,
        checkpointer: Optional[MemorySaver] = None,
        analyst_config=ANALYST_CONFIG,
        sensitive_config=SENSITIVE_CONFIG,
    ):
        """Initialize the AnalystAgent and build the graph."""
        self.checkpointer = ensure_checkpointer(checkpointer)
        self.analyst_config = analyst_config
        self.sensitive_config = sensitive_config
        self.app = self._build_graph()

    def _build_graph(self):
        """Build and compile the LangGraph StateGraph workflow.

        This method constructs the workflow graph by adding nodes,
        defining edges, and setting up conditional routing for the
        analysis pipeline.

        Returns:
            A compiled StateGraph with checkpointer support.
        """
        workflow = StateGraph(AnalystAgentsState)
        workflow.add_node("parse_query_node", self.parse_query_node)
        workflow.add_node("data_select_node", self.data_select_node)
        workflow.add_node("method_retrieve_node", self.method_retrieve_node)
        workflow.add_node("plan_node", self.plan_node)
        workflow.add_node("check_node", self.check_node)
        workflow.add_node("tool_extract_node", self.tool_extract_node)
        workflow.add_node("tool_retrieve_node", self.tool_retrieve_node)
        workflow.add_node("submit_node", self.submit_node)
        workflow.add_node("pooling_node", self.pooling_node)
        workflow.add_edge(START, "parse_query_node")
        workflow.add_conditional_edges(
            "parse_query_node", self.route_after_extract
        )
        workflow.add_conditional_edges(
            "data_select_node", self.route_after_data_select
        )
        workflow.add_edge("method_retrieve_node", "plan_node")
        workflow.add_edge("plan_node", "check_node")
        workflow.add_conditional_edges("check_node", self.route_after_check)
        workflow.add_edge("tool_extract_node", "tool_retrieve_node")
        workflow.add_edge("tool_retrieve_node", "submit_node")
        workflow.add_conditional_edges("submit_node", self.route_after_submit)
        workflow.add_conditional_edges(
            "pooling_node", self.route_after_pooling
        )

        return workflow.compile(checkpointer=self.checkpointer)

    async def pooling_node(self, state: AnalystAgentsState):
        """Poll task status until completion.

        This node waits for the configured poll interval and then queries
        the analysis platform for the current task status. It returns the
        status which is used by the router to determine whether to continue
        polling or end the workflow.

        Args:
            state: The current workflow state containing task_id.

        Returns:
            A dictionary containing the current task_status.

        Raises:
            McpError: If the task status request fails.
        """
        await asyncio.sleep(self.analyst_config.POLL_INTERVAL)
        task_id = state["task_id"]
        try:
            status_data = await task_status(
                task_id,
                analysis_url=self.analyst_config.ANALYSIS_URL,
                region=self.analyst_config.ANALYSIS_REGION,
                timeout=self.analyst_config.TIMEOUT,
                retriable_codes=self.analyst_config.RETRIABLE_CODES,
                max_retries=self.analyst_config.MAX_RETRIES,
            )
            current_status = status_data.get("status")
            return {"task_status": current_status}
        except Exception as exc:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Task status request failed: {str(exc)}",
                )
            ) from exc

    def route_after_extract(
        self, state: AnalystAgentsState
    ) -> Literal[
        "data_select_node", "method_retrieve_node", "tool_extract_node"
    ]:
        """Route after the parse_query node based on configuration.

        This method determines the next node based on auto_select flag and
        whether a plan was provided in the query.

        Args:
            state: The current workflow state.

        Returns:
            "data_select_node" if auto_select is enabled,
            "tool_extract_node" if a plan was provided,
            otherwise "method_retrieve_node".
        """
        if state.get("is_auto_select"):
            return "data_select_node"
        if state.get("plan"):
            return "tool_extract_node"
        return "method_retrieve_node"

    def route_after_data_select(
        self, state: AnalystAgentsState
    ) -> Literal["method_retrieve_node", "tool_extract_node"]:
        """Route after the data_select node based on plan availability.

        Args:
            state: The current workflow state.

        Returns:
            "tool_extract_node" if a plan exists, otherwise
            "method_retrieve_node".
        """
        if state.get("plan"):
            return "tool_extract_node"
        return "method_retrieve_node"

    def route_after_plan(
        self, state: AnalystAgentsState
    ) -> Literal["method_retrieve_node", "tool_extract_node"]:
        """Route after the plan node based on plan availability.

        Args:
            state: The current workflow state.

        Returns:
            "tool_extract_node" if a plan exists, otherwise
            "method_retrieve_node".
        """
        if state.get("plan"):
            return "tool_extract_node"
        return "method_retrieve_node"

    def route_after_check(
        self, state: AnalystAgentsState
    ) -> Literal["plan_node", "tool_extract_node"]:
        """Route after the check node based on plan validation.

        If the plan was approved or max retries were reached, proceed to
        tool extraction. Otherwise, return to plan_node for revision.

        Args:
            state: The current workflow state.

        Returns:
            "tool_extract_node" if approved, otherwise "plan_node".
        """
        feedback = state.get("plan_feedback")

        if feedback == "APPROVED":
            return "tool_extract_node"
        return "plan_node"

    def route_after_submit(
        self, state: AnalystAgentsState
    ) -> Literal["pooling_node", "__end__"]:
        """Route after submit based on polling preference.

        Args:
            state: The current workflow state.

        Returns:
            "pooling_node" if polling is enabled, otherwise "__end__".
        """
        if state.get("is_polling"):
            return "pooling_node"
        return "__end__"

    def route_after_pooling(
        self, state: AnalystAgentsState
    ) -> Literal["__end__", "pooling_node"]:
        """Route based on task completion status.

        Args:
            state: The current workflow state.

        Returns:
            "__end__" if task is in a terminal state (SUCCEEDED, FAILED,
            CANCELLED), otherwise "pooling_node" to continue polling.
        """
        status = state.get("task_status")
        if status in ["SUCCEEDED", "FAILED", "CANCELLED"]:
            return "__end__"
        return "pooling_node"

    async def arun(
        self,
        query: Optional[str],
        **kwargs: Any,
    ) -> dict:
        """Execute the AnalystAgent workflow.

        This is the main entry point for invoking the agent. It initializes
        the state with the user's query and configuration, then runs the
        LangGraph workflow to process the bioinformatics analysis request.

        Args:
            query: The user's natural language query for the analysis.
            goal_description: Optional pre-decomposed research goal.
            user: The user identifier.
            user_id: The user ID.
            is_create_dir: Whether to create an output directory.
            output_dir: The output directory path.
            execute_code: Whether to execute code during analysis.
            compute_resource: The compute resource level.
            timeout: Request timeout in seconds.
            max_retries: Maximum number of retries for failed requests.
            reasoning_effort: Reasoning effort level for the LLM.
            frequency_penalty: Frequency penalty for LLM sampling.
            presence_penalty: Presence penalty for LLM sampling.
            n: Number of completions to generate.
            stream: Whether to stream the response.
            temperature: Sampling temperature for the LLM.
            top_p: Top-p sampling parameter.
            prompt_file: Path to the prompt template file.
            preset_data_list: Pre-configured data file list.
            obs_file_list: List of OBS files uploaded by the user.
            preset_plan: Pre-configured analysis plan.
            thread_id: Optional thread ID for state persistence.
            is_auto_select: Whether to automatically select data files.
            is_polling: Whether to poll for task status.

        Returns:
            A dictionary containing task_id, output_dir, job_name, and
            compute_resource on success, or the initial state with
            task_status "FAILED_AT_AGENT_LEVEL" and error_detail on failure.
        """
        # Public wrappers bind these compatibility options into the cached
        # agent config. Direct arun callers may still pass them, so keep the
        # state-level overrides explicit without mutating shared config.
        user_id = kwargs.get("user_id", ANALYST_CONFIG.USER_ID)
        is_create_dir = kwargs.get("is_create_dir", ANALYST_CONFIG.CREATE_DIR)
        output_dir = kwargs.get("output_dir", ANALYST_CONFIG.OUTPUT_DIR)
        compute_resource = kwargs.get(
            "compute_resource",
            ANALYST_CONFIG.COMPUTE_RESOURCE,
        )
        compatibility_config = copy_config_with_overrides(
            self.analyst_config,
            {
                "user": kwargs.get("user", ANALYST_CONFIG.USER),
                "execute_code": kwargs.get(
                    "execute_code",
                    ANALYST_CONFIG.EXECUTE_CODE,
                ),
                "timeout": kwargs.get("timeout", ANALYST_CONFIG.TIMEOUT),
                "max_retries": kwargs.get(
                    "max_retries",
                    ANALYST_CONFIG.MAX_RETRIES,
                ),
                "reasoning_effort": kwargs.get(
                    "reasoning_effort",
                    ANALYST_CONFIG.REASONING_EFFORT,
                ),
                "frequency_penalty": kwargs.get(
                    "frequency_penalty",
                    ANALYST_CONFIG.FREQUENCY_PENALTY,
                ),
                "presence_penalty": kwargs.get(
                    "presence_penalty",
                    ANALYST_CONFIG.PRESENCE_PENALTY,
                ),
                "n": kwargs.get("n", ANALYST_CONFIG.N),
                "stream": kwargs.get("stream", ANALYST_CONFIG.STREAM),
                "temperature": kwargs.get(
                    "temperature",
                    ANALYST_CONFIG.TEMPERATURE,
                ),
                "top_p": kwargs.get("top_p", ANALYST_CONFIG.TOP_P),
                "prompt_file": kwargs.get(
                    "prompt_file",
                    ANALYST_CONFIG.PROMPT_FILE,
                ),
            },
            ANALYST_CONFIG_FIELD_MAP,
            fixed_updates={
                "USER_ID": user_id,
                "CREATE_DIR": is_create_dir,
                "OUTPUT_DIR": output_dir,
                "COMPUTE_RESOURCE": compute_resource,
            },
        )

        obs_file_list = kwargs.get("obs_file_list")
        if obs_file_list is None:
            obs_file_list = []
        else:
            obs_file_list = list(obs_file_list)

        initial_state = {
            "query": query,
            "goal_description": kwargs.get("goal_description"),
            "obs_file_list": obs_file_list,
            "data_list": kwargs.get("preset_data_list") or {},
            "output_dir": compatibility_config.OUTPUT_DIR,
            "compute_resource": compatibility_config.COMPUTE_RESOURCE,
            "method_context": None,
            "plan": kwargs.get("preset_plan"),
            "plan_feedback": None,
            "plan_retries": 0,
            "extracted_tools": [],
            "tool_usages": "",
            "job_name": None,
            "task_id": None,
            "task_status": None,
            "is_polling": kwargs.get("is_polling", True),
            "is_auto_select": kwargs.get("is_auto_select", True),
        }

        async def run_graph() -> dict[str, Any]:
            """Invoke the analyst graph and return public result fields."""
            final_state = await ainvoke_graph(
                self.app,
                initial_state,
                thread_id=kwargs.get("thread_id"),
            )
            return {
                "task_id": final_state["task_id"],
                "output_dir": final_state["output_dir"],
                "job_name": final_state["job_name"],
                "compute_resource": final_state["compute_resource"],
            }

        def failure_state(exc: Exception) -> dict[str, Any]:
            """Return graph failures as agent state."""
            return {
                **initial_state,
                "task_status": "FAILED_AT_AGENT_LEVEL",
                "error_detail": str(exc),
            }

        return await capture_workflow_boundary(run_graph, failure_state)


def _analyst_config_with_overrides(
    user_id: str,
    is_create_dir: bool,
    output_dir: str,
    compute_resource: Literal["small", "medium", "large"],
    **kwargs: Any,
):
    """Build an AnalystConfig copy from compatibility wrapper arguments."""
    return copy_config_with_overrides(
        ANALYST_CONFIG,
        kwargs,
        ANALYST_CONFIG_FIELD_MAP,
        fixed_updates={
            "USER_ID": user_id,
            "CREATE_DIR": is_create_dir,
            "OUTPUT_DIR": output_dir,
            "COMPUTE_RESOURCE": compute_resource,
        },
    )


def _sensitive_config_with_overrides(**kwargs: Any):
    """Build a SensitiveConfig copy from compatibility wrapper arguments."""
    return copy_sensitive_config_with_overrides(
        SENSITIVE_CONFIG,
        kwargs,
        field_map=ANALYST_SENSITIVE_FIELD_MAP,
        secret_field_map=ANALYST_SECRET_FIELD_MAP,
    )


def _submit_user_and_thread_id(
    user_id: Any,
    scope: str,
    operation: str,
) -> tuple[str, str]:
    """Return compatible user IDs and run-scoped fallback thread IDs."""
    if user_id:
        resolved_user_id = str(user_id)
        return resolved_user_id, resolved_user_id

    run_identity = RunIdentity.create(user_id=None, scope=scope)
    return run_identity.user_id, run_identity.scoped_id("thread", operation)


async def submit(
    goal_description: str,
    data_list: Any,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Compatibility wrapper around the LangGraph-based AnalystAgent."""
    user_id = kwargs.get("user_id", ANALYST_CONFIG.USER_ID)
    user_id, thread_id = _submit_user_and_thread_id(
        user_id,
        "analyst-submit",
        "submit",
    )
    is_create_dir = kwargs.get("is_create_dir", ANALYST_CONFIG.CREATE_DIR)
    output_dir = kwargs.get("output_dir", ANALYST_CONFIG.OUTPUT_DIR)
    meta = kwargs.get("meta", "")
    compute_resource = kwargs.get(
        "compute_resource",
        ANALYST_CONFIG.COMPUTE_RESOURCE,
    )
    enable_auto_select = kwargs.get("enable_auto_select", True)
    meta_meta = kwargs.get("meta_meta")
    analyst_config = _analyst_config_with_overrides(
        user_id=user_id,
        is_create_dir=is_create_dir,
        output_dir=output_dir,
        compute_resource=compute_resource,
        **kwargs,
    )
    sensitive_config = _sensitive_config_with_overrides(**kwargs)
    agent = get_cached_agent(
        "AnalystAgent.submit",
        lambda: AnalystAgent(
            analyst_config=analyst_config,
            sensitive_config=sensitive_config,
        ),
        agent_fingerprint_values(
            analyst_config=analyst_config,
            sensitive_config=sensitive_config,
        ),
    )
    return await agent.arun(
        query=goal_description,
        goal_description=goal_description,
        output_dir=output_dir,
        compute_resource=compute_resource,
        preset_data_list=data_list,
        preset_plan=meta + (meta_meta or ""),
        thread_id=thread_id,
        is_auto_select=enable_auto_select,
        is_polling=False,
    )


async def retrieve_plan_submit(
    goal_description: str,
    data_list: Dict[str, str],
    obs_file_list: Optional[List[str]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Compatibility wrapper around the LangGraph-based AnalystAgent."""
    user_id = kwargs.get("user_id", ANALYST_CONFIG.USER_ID)
    user_id, thread_id = _submit_user_and_thread_id(
        user_id,
        "analyst-retrieve-plan-submit",
        "retrieve-plan-submit",
    )
    is_create_dir = kwargs.get("is_create_dir", ANALYST_CONFIG.CREATE_DIR)
    output_dir = kwargs.get("output_dir", ANALYST_CONFIG.OUTPUT_DIR)
    compute_resource = kwargs.get(
        "compute_resource",
        ANALYST_CONFIG.COMPUTE_RESOURCE,
    )
    meta_meta = kwargs.get("meta_meta")
    analyst_config = _analyst_config_with_overrides(
        user_id=user_id,
        is_create_dir=is_create_dir,
        output_dir=output_dir,
        compute_resource=compute_resource,
        **kwargs,
    )
    sensitive_config = _sensitive_config_with_overrides(**kwargs)
    agent = get_cached_agent(
        "AnalystAgent.retrieve_plan_submit",
        lambda: AnalystAgent(
            analyst_config=analyst_config,
            sensitive_config=sensitive_config,
        ),
        agent_fingerprint_values(
            analyst_config=analyst_config,
            sensitive_config=sensitive_config,
        ),
    )
    result = await agent.arun(
        query=goal_description,
        goal_description=goal_description,
        output_dir=output_dir,
        compute_resource=compute_resource,
        preset_data_list=data_list,
        obs_file_list=obs_file_list or [],
        thread_id=thread_id,
        is_auto_select=True,
        is_polling=False,
    )
    if meta_meta:
        result["meta_meta"] = meta_meta
    return result


async def wait_for_completion(
    task_id: str,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Poll a submitted task until it reaches a terminal status."""
    analysis_url = kwargs.get("analysis_url", ANALYST_CONFIG.ANALYSIS_URL)
    region = kwargs.get("region", ANALYST_CONFIG.ANALYSIS_REGION)
    timeout = kwargs.get("timeout", ANALYST_CONFIG.TIMEOUT)
    retriable_codes = kwargs.get("retriable_codes")
    max_retries = kwargs.get("max_retries", ANALYST_CONFIG.MAX_RETRIES)
    poll_interval = kwargs.get("poll_interval", ANALYST_CONFIG.POLL_INTERVAL)
    max_poll = kwargs.get("max_poll", ANALYST_CONFIG.MAX_POLL)
    if retriable_codes is None:
        retriable_codes = list(ANALYST_CONFIG.RETRIABLE_CODES)
    else:
        retriable_codes = list(retriable_codes)
    start_time = time.time()
    while (time.time() - start_time) < max_poll:
        status_data = await task_status(
            task_id,
            analysis_url=analysis_url,
            region=region,
            timeout=timeout,
            retriable_codes=retriable_codes,
            max_retries=max_retries,
        )
        match status_data.get("status"):
            case "CANCELLED":
                raise McpError(
                    ErrorData(code=INTERNAL_ERROR, message="Task cancelled")
                )
            case "FAILED":
                raise McpError(
                    ErrorData(code=INTERNAL_ERROR, message="Task failed")
                )
            case "PENDING" | "RUNNING":
                await asyncio.sleep(poll_interval)
            case "SUCCEEDED":
                return status_data
            case _:
                raise McpError(
                    ErrorData(code=INTERNAL_ERROR, message="Task status error")
                )
    raise asyncio.TimeoutError(
        f"Exceeded max polling time {max_poll / 60} minutes"
    )


async def task_delete(
    task_id: str,
    **kwargs: Any,
) -> str:
    """
    Deletes a specified task from the analysis platform.

    This function sends a request to terminate and delete a task using its
    unique ID. It includes retry logic for transient network or server issues.

    Args:
        task_id: The unique identifier of the task to be deleted.
        analysis_url: The URL for the analysis submission API.
        region: The geographical region of the analysis service.
        timeout: The total request timeout in seconds for API calls.
        retriable_codes: A list of HTTP status codes that trigger a retry.
        max_retries: The maximum number of retry attempts for a failed request.

    Returns:
        A confirmation message indicating that the task was successfully
        deleted.

    Raises:
        McpError: If the task deletion fails after all retries.
    """
    analysis_url = kwargs.get("analysis_url", ANALYST_CONFIG.ANALYSIS_URL)
    region = kwargs.get("region", ANALYST_CONFIG.ANALYSIS_REGION)
    timeout = kwargs.get("timeout", ANALYST_CONFIG.TIMEOUT)
    retriable_codes = kwargs.get("retriable_codes")
    max_retries = kwargs.get("max_retries", ANALYST_CONFIG.MAX_RETRIES)
    if retriable_codes is None:
        retriable_codes = list(ANALYST_CONFIG.RETRIABLE_CODES)
    else:
        retriable_codes = list(retriable_codes)
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        response = await request_response_with_retries(
            client,
            JsonPostRequest(
                url=f"{analysis_url}/{task_id}/terminate",
                headers={
                    "Content-Type": "application/json",
                    "X-Auth-Token": await get_token(
                        timeout=timeout, region=region
                    ),
                },
                json_body={"force": True},
            ),
            JsonPostRetry(
                timeout=timeout,
                max_retries=max_retries,
                retriable_codes=retriable_codes,
                message="Failed to delete task",
            ),
        )
        if response is not None and response.status_code == 200:
            return f"Delete task {task_id} success."

    raise McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message="Failed to delete task after all retries",
        )
    )


async def task_status(
    task_id: str,
    **kwargs: Any,
) -> dict:
    """
    Checks the execution status of a specified task.

    This function queries the analysis platform for the current status of a
    task identified by its ID. It provides details such as whether the task is
    pending, running, completed, or failed.

    Args:
        task_id: The unique identifier of the task to check.
        analysis_url: The URL for the analysis submission API.
        region: The geographical region of the analysis service.
        timeout: The total request timeout in seconds for API calls.
        retriable_codes: A list of HTTP status codes that trigger a retry.
        max_retries: The maximum number of retry attempts for a failed request.

    Returns:
        A dictionary containing the task's status details, including its ID,
        current status, and potentially results or error information.

    Raises:
        McpError: If checking the task status fails after all retries.
    """
    analysis_url = kwargs.get("analysis_url", ANALYST_CONFIG.ANALYSIS_URL)
    region = kwargs.get("region", ANALYST_CONFIG.ANALYSIS_REGION)
    timeout = kwargs.get("timeout", ANALYST_CONFIG.TIMEOUT)
    retriable_codes = kwargs.get("retriable_codes")
    max_retries = kwargs.get("max_retries", ANALYST_CONFIG.MAX_RETRIES)
    if retriable_codes is None:
        retriable_codes = list(ANALYST_CONFIG.RETRIABLE_CODES)
    else:
        retriable_codes = list(retriable_codes)
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        response = await request_response_with_retries(
            client,
            JsonPostRequest(
                url=f"{analysis_url}/{task_id}",
                method="GET",
                headers={
                    "Content-Type": "application/json",
                    "X-Auth-Token": await get_token(
                        timeout=timeout, region=region
                    ),
                },
            ),
            JsonPostRetry(
                timeout=timeout,
                max_retries=max_retries,
                retriable_codes=retriable_codes,
                message=f"Check task {task_id} status failed",
            ),
        )
        if response is not None and response.status_code == 200:
            return response.json()

    raise McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message="Failed to check task status after all retries",
        )
    )


async def task_log(
    task_id: str,
    **kwargs: Any,
) -> dict:
    """
    Retrieves the execution log for a specified task.

    This function fetches the logs generated by a task during its execution,
    which can be useful for debugging or monitoring progress.

    Args:
        task_id: The unique identifier of the task.
        analysis_url: The URL for the analysis submission API.
        compute_resource: The level of compute resources used by the task.
        region: The geographical region of the analysis service.
        timeout: The total request timeout in seconds for API calls.
        retriable_codes: A list of HTTP status codes that trigger a retry.
        max_retries: The maximum number of retry attempts for a failed request.

    Returns:
        A dictionary containing the task's log data.

    Raises:
        McpError: If fetching the task log fails after all retries.
    """
    analysis_url = kwargs.get("analysis_url", ANALYST_CONFIG.ANALYSIS_URL)
    compute_resource = kwargs.get(
        "compute_resource", ANALYST_CONFIG.COMPUTE_RESOURCE
    )
    region = kwargs.get("region", ANALYST_CONFIG.ANALYSIS_REGION)
    timeout = kwargs.get("timeout", ANALYST_CONFIG.TIMEOUT)
    retriable_codes = kwargs.get("retriable_codes")
    max_retries = kwargs.get("max_retries", ANALYST_CONFIG.MAX_RETRIES)
    if retriable_codes is None:
        retriable_codes = list(ANALYST_CONFIG.RETRIABLE_CODES)
    else:
        retriable_codes = list(retriable_codes)
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        response = await request_response_with_retries(
            client,
            JsonPostRequest(
                url=(
                    f"{analysis_url}/{task_id}/logs"
                    f"?task_name=analyst-agents-{compute_resource}"
                ),
                method="GET",
                headers={
                    "Content-Type": "application/json",
                    "X-Auth-Token": await get_token(
                        timeout=timeout, region=region
                    ),
                },
            ),
            JsonPostRetry(
                timeout=timeout,
                max_retries=max_retries,
                retriable_codes=retriable_codes,
                message=f"Check task {task_id} log failed",
            ),
        )
        if response is not None and response.status_code == 200:
            return response.json()

    raise McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message="Failed to check task log after all retries",
        )
    )
