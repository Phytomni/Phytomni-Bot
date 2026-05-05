import asyncio
import datetime
import re
import json
import time
from pathlib import Path
from random import uniform
from traceback import format_exc
from typing import Any, List, Literal, Dict, Optional, TypedDict, cast
from uuid import uuid1
from httpx import AsyncClient, ConnectError, HTTPStatusError
from httpx import Timeout, TimeoutException
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INTERNAL_ERROR
from obs import GetObjectHeader, PutObjectHeader, ObsClient
from langgraph.graph import StateGraph, START
from langgraph.checkpoint.memory import MemorySaver
from .chat_agents import phyto_chat
from .config.defaults import AnalystConfig
from .config.overrides import (
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from .config.settings import SensitiveConfig
from .knowledge_agents import multi_retrieve, retrieve
from .utils import download_list_convert, get_prompt, get_token

ac = AnalystConfig()
sc = SensitiveConfig.load()

ANALYST_CONFIG_FIELD_MAP = {
    "analysis_url": "ANALYSIS_URL",
    "region": "ANALYSIS_REGION",
    "resource_dict": "RESOURCE",
    "app_id_dict": "APP_ID",
    "task_name": "TASK_NAME",
    "execute_code": "EXECUTE_CODE",
    "retrieve_url": "RETRIEVE_URL",
    "repo_id_dict": "REPO_ID_DICT",
    "page_num": "PAGE_NUM",
    "filter_string": "FILTER_STRING",
    "scope": "SCOPE",
    "extra_repo_ids": "EXTRA_REPO_IDS",
    "rerank_url": "RERANK_URL",
    "rerank_batch_size": "RERANK_BATCH_SIZE",
    "score_threshold": "SCORE_THRESHOLD",
    "top_n": "TOP_N",
    "prompt_file": "PROMPT_FILE",
    "prompt_path": "PROMPT_PATH",
    "frequency_penalty": "FREQUENCY_PENALTY",
    "max_tokens": "MAX_TOKENS",
    "n": "N",
    "presence_penalty": "PRESENCE_PENALTY",
    "reasoning_effort": "REASONING_EFFORT",
    "response_format": "RESPONSE_FORMAT",
    "stream": "STREAM",
    "temperature": "TEMPERATURE",
    "top_p": "TOP_P",
    "user": "USER",
    "server_dir": "TEMP_DIR",
    "obs_server": "OBS_SERVER",
    "bucket_name": "BUCKET_NAME",
    "part_size": "PART_SIZT",
    "task_num": "TASK_NUM",
    "max_concurrency": "MAX_CONCURRENCY",
    "max_workers": "MAX_WORKERS",
    "timeout": "TIMEOUT",
    "retriable_codes": "RETRIABLE_CODES",
    "max_retries": "MAX_RETRIES",
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
    "access_key_id": "AccessKeyID",
    "secret_access_key": "SecretAccessKey",
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


class AnalystAgent:
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
                      Defaults to MemorySaver().
        analyst_config: Configuration for the analyst agent.
                        Defaults to the global ac instance.
        sensitive_config: Configuration for sensitive data (e.g., API keys).
                          Defaults to the global sc instance.

    Attributes:
        checkpointer: The checkpointer for state persistence.
        ac: The analyst configuration instance.
        sc: The sensitive configuration instance.
        app: The compiled LangGraph application.
    """

    def __init__(
        self,
        checkpointer=MemorySaver(),
        analyst_config=ac,
        sensitive_config=sc,
    ):
        """Initialize the AnalystAgent and build the graph."""
        self.checkpointer = checkpointer
        self.ac = analyst_config
        self.sc = sensitive_config
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

    async def parse_query_node(self, state: AnalystAgentsState):
        """Decompose the user query into goal, data_list, and plan components.

        This node checks if the query has already been decomposed. If not,
        it uses an LLM to parse the user query into three components:
        - goal_description: The core research objective
        - data_list: Any mentioned or implied data sources
        - plan: Any explicitly stated analysis workflow

        Args:
            state: The current workflow state containing query.

        Returns:
            A dictionary containing goal_description, data_list, and plan.
        """
        if state["data_list"] and state["goal_description"]:
            return {
                "goal_description": state["goal_description"],
                "data_list": state["data_list"],
                "plan": state.get("plan", None),
            }
        else:
            parse_prompt = get_prompt(
                self.ac.PROMPT_FILE,
                "user/split_query",
                {"user_query": state["query"]},
            )
            phyto_response = await phyto_chat(
                user_query=parse_prompt,
                prompt_file=self.ac.PROMPT_FILE,
                prompt_path=self.ac.PROMPT_PATH,
                api_key=self.sc.API_KEY.get_secret_value(),
                base_url=self.sc.BASE_URL,
                model=self.sc.MODEL_ID,
                response_format={"type": "json_schema"},
                timeout=self.ac.TIMEOUT,
                retriable_codes=self.ac.RETRIABLE_CODES,
                max_retries=self.ac.MAX_RETRIES,
            )
            content = "{}"
            if (
                phyto_response
                and phyto_response.get("choices")
                and len(phyto_response["choices"]) > 0
                and phyto_response["choices"][0].get("message")
                and phyto_response["choices"][0]["message"].get("content")
            ):
                content = phyto_response["choices"][0]["message"]["content"]
            pattern = r"```json(.*?)```"
            match = re.search(pattern, content, re.DOTALL)
            if match:
                json_string = match.group(1).strip()
                result = json.loads(json_string)
            else:
                result = json.loads(content)
            return {
                "goal_description": (
                    result["goal_description"]
                    if result["goal_description"]
                    else None
                ),
                "data_list": (
                    json.loads(result["data_list"])
                    if result["data_list"]
                    else None
                ),
                "plan": result["plan"] if result["plan"] else "",
            }

    async def data_select_node(self, state: AnalystAgentsState):
        """Select appropriate data files from the available database.

        This node loads pre-prepared species data and uses an LLM to select
        relevant data files based on the research goal. It merges user-provided
        data with auto-selected data to create a comprehensive data list.

        Args:
            state: The current workflow state containing goal_description
                and data_list.

        Returns:
            A dictionary containing the updated data_list with selected files.

        Raises:
            McpError: If loading species data or parsing the LLM response
                fails.
        """
        try:
            with open(
                self.ac.PRE_PREPARED_DATA_PATH, "r", encoding="utf-8"
            ) as f:
                species_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=(
                        "Failed to load species data list from "
                        f"{self.ac.PRE_PREPARED_DATA_PATH}"
                    ),
                )
            ) from exc
        data_list = state["data_list"]
        user_data_summary = json.dumps(data_list)
        selection_prompt = get_prompt(
            self.ac.PROMPT_FILE,
            "user/data_selection",
            {
                "goal_description": state["goal_description"],
                "user_data_list": user_data_summary,
                "available_data_list": json.dumps(species_data),
            },
        )

        try:
            selection_response = await phyto_chat(
                user_query=selection_prompt,
                prompt_file=self.ac.PROMPT_FILE,
                prompt_path=self.ac.PROMPT_PATH,
                api_key=self.sc.API_KEY.get_secret_value(),
                base_url=self.sc.BASE_URL,
                model=self.sc.MODEL_ID,
                frequency_penalty=self.ac.FREQUENCY_PENALTY,
                n=self.ac.N,
                presence_penalty=self.ac.PRESENCE_PENALTY,
                reasoning_effort=self.ac.REASONING_EFFORT,
                response_format={"type": "json_schema"},
                stream=self.ac.STREAM,
                temperature=self.ac.TEMPERATURE,
                top_p=self.ac.TOP_P,
                user=self.ac.USER,
                timeout=self.ac.TIMEOUT,
                retriable_codes=self.ac.RETRIABLE_CODES,
                max_retries=self.ac.MAX_RETRIES,
            )
        except Exception as exc:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=(
                        "Failed to get data selection from language model: "
                        f"{str(exc)}"
                    ),
                )
            ) from exc

        selected_data = {}
        if (
            selection_response
            and selection_response.get("choices")
            and len(selection_response["choices"]) > 0
            and selection_response["choices"][0].get("message")
            and selection_response["choices"][0]["message"].get("content")
        ):
            try:
                content = selection_response["choices"][0]["message"][
                    "content"
                ]
                match = re.search(r"\{.*\}", content, re.DOTALL)
                if match:
                    content = match.group(0).strip()
                parsed_response = json.loads(content)
                if "selected_data" in parsed_response:
                    selected_data = parsed_response["selected_data"]
                else:
                    selected_data = parsed_response

            except (json.JSONDecodeError, ValueError) as exc:
                raise McpError(
                    ErrorData(
                        code=INTERNAL_ERROR,
                        message=(
                            "Failed to parse data selection response: "
                            f"{str(exc)}"
                        ),
                    )
                ) from exc

        final_data_list = {**data_list, **selected_data}
        print("===================AutoSelect Data===================")
        print(final_data_list)
        print("=====================================================")

        return {"data_list": final_data_list}

    async def method_retrieve_node(self, state: AnalystAgentsState) -> dict:
        """Retrieve relevant bioinformatics methods, SOPs, and literature.

        This node searches the knowledge base for relevant analysis methods,
        standard operating procedures, and cutting-edge literature based on
        the research goal. It also processes any user-uploaded files from OBS.
        The retrieved context is used to inform plan generation.

        Args:
            state: The current workflow state containing goal_description
                and obs_file_list.

        Returns:
            A dictionary containing the method_context with upload_context
            and retrieve_context.
        """
        total_length = 0
        upload_context = ""
        if state["obs_file_list"]:
            upload_str_list = await download_list_convert(
                obs_file_list=state["obs_file_list"],
                server_dir=self.ac.TEMP_DIR,
                access_key_id=self.sc.AccessKeyID.get_secret_value(),
                secret_access_key=self.sc.SecretAccessKey.get_secret_value(),
                obs_server=self.ac.OBS_SERVER,
                bucket_name=self.ac.BUCKET_NAME,
                part_size=self.ac.PART_SIZT,
                task_num=self.ac.TASK_NUM,
                max_retries=self.ac.MAX_RETRIES,
                max_concurrency=self.ac.MAX_CONCURRENCY,
                max_workers=self.ac.MAX_WORKERS,
            )
            upload_results = []
            for i, doc in enumerate(upload_str_list):
                fragment = (
                    f"[user upload file {i+1} begin]\n"
                    f"{doc}\n[user upload file {i+1} end]"
                )
                if total_length + len(fragment) <= self.ac.MAX_TOKENS:
                    upload_results.append(fragment)
                    total_length += len(fragment)
                else:
                    break
            upload_context = "\n\n".join(upload_results)
        retrieve_response = await multi_retrieve(
            user_query=state["goal_description"],
            retrieve_url=self.ac.RETRIEVE_URL,
            repo_id_dict=self.ac.REPO_ID_DICT,
            page_num=self.ac.PAGE_NUM,
            filter_string=self.ac.FILTER_STRING,
            scope=self.ac.SCOPE,
            extra_repo_ids=self.ac.EXTRA_REPO_IDS,
            rerank_url=self.ac.RERANK_URL,
            rerank_batch_size=self.ac.RERANK_BATCH_SIZE,
            score_threshold=self.ac.SCORE_THRESHOLD,
            top_n=self.ac.TOP_N,
            timeout=self.ac.TIMEOUT,
            retriable_codes=self.ac.RETRIABLE_CODES,
            max_retries=self.ac.MAX_RETRIES,
        )
        retrieve_results = []
        for i, doc in enumerate(retrieve_response.get("doc_list", [])):
            header = f"[document {i+1} begin] {doc['title']}"
            content_field = (
                doc.get("big_content")
                if "big_content" in doc
                else doc.get("content", "")
            )
            body = (
                f"{doc['subtitle']}\n{content_field}"
                if doc.get("subtitle")
                else doc.get("content", "")
            )
            fragment = f"{header}\n{body} [document {i+1} end]"
            if total_length + len(fragment) <= self.ac.MAX_TOKENS:
                retrieve_results.append(fragment)
                total_length += len(fragment)
            else:
                break
        retrieve_context = "\n\n".join(retrieve_results)
        print("===================Retrieve Information===================")
        print(retrieve_context)
        print("==========================================================")
        return {
            "method_context": {
                "upload_context": upload_context,
                "retrieve_context": retrieve_context,
            }
        }

    async def plan_node(self, state: AnalystAgentsState):
        """Generate or revise the analysis plan.

        This node generates an analysis plan based on the research goal and
        retrieved method context. If plan_feedback exists (from a previous
        rejection), it revises the plan accordingly. The plan describes
        the step-by-step workflow for the bioinformatics analysis.

        Args:
            state: The current workflow state containing goal_description,
                   method_context, plan_feedback, and obs_file_list.

        Returns:
            A dictionary containing the generated plan, incremented
            plan_retries, and reset plan_feedback.

        Raises:
            McpError: If the LLM fails to generate a valid plan.
        """
        if state.get("plan_feedback"):
            if state["obs_file_list"]:
                user_query = get_prompt(
                    self.ac.PROMPT_FILE,
                    "user/analysis_retrieve_file_feedback",
                    {
                        "retrieve_results": state["method_context"][
                            "retrieve_context"
                        ],
                        "upload_context": state["method_context"][
                            "upload_context"
                        ],
                        "feed_back": state["plan_feedback"],
                        "raw_plan": state.get("plan", ""),
                        "user_query": state["goal_description"],
                    },
                )
            else:
                user_query = get_prompt(
                    self.ac.PROMPT_FILE,
                    "user/analysis_retrieve_feedback",
                    {
                        "retrieve_results": state["method_context"][
                            "retrieve_context"
                        ],
                        "feed_back": state["plan_feedback"],
                        "raw_plan": state.get("plan", ""),
                        "user_query": state["goal_description"],
                    },
                )
        else:
            if state["obs_file_list"]:
                user_query = get_prompt(
                    self.ac.PROMPT_FILE,
                    "user/analysis_retrieve_file",
                    {
                        "retrieve_results": state["method_context"][
                            "retrieve_context"
                        ],
                        "upload_context": state["method_context"][
                            "upload_context"
                        ],
                        "user_query": state["goal_description"],
                    },
                )
            else:
                user_query = get_prompt(
                    self.ac.PROMPT_FILE,
                    "user/analysis_retrieve",
                    {
                        "retrieve_results": state["method_context"][
                            "retrieve_context"
                        ],
                        "user_query": state["goal_description"],
                    },
                )
        phyto_response = await phyto_chat(
            user_query=user_query,
            prompt_file=self.ac.PROMPT_FILE,
            prompt_path=self.ac.PROMPT_PATH,
            api_key=self.sc.API_KEY.get_secret_value(),
            base_url=self.sc.BASE_URL,
            model=self.sc.MODEL_ID,
            frequency_penalty=self.ac.FREQUENCY_PENALTY,
            n=self.ac.N,
            presence_penalty=self.ac.PRESENCE_PENALTY,
            reasoning_effort=self.ac.REASONING_EFFORT,
            response_format=self.ac.RESPONSE_FORMAT,
            stream=self.ac.STREAM,
            temperature=self.ac.TEMPERATURE,
            top_p=self.ac.TOP_P,
            user=self.ac.USER,
            timeout=self.ac.TIMEOUT,
            retriable_codes=self.ac.RETRIABLE_CODES,
            max_retries=self.ac.MAX_RETRIES,
        )
        content = None
        if (
            phyto_response
            and phyto_response.get("choices")
            and len(phyto_response["choices"]) > 0
            and phyto_response["choices"][0].get("message")
            and phyto_response["choices"][0]["message"].get("content")
        ):
            content = phyto_response["choices"][0]["message"]["content"]
        if not content:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message="Failed to generate plan: "
                    "Invalid response from language model",
                )
            )
        print("===================Plan===================")
        print(content)
        print("==========================================")
        return {
            "plan": content,
            "plan_retries": state.get("plan_retries", 0) + 1,
            "plan_feedback": None,
        }

    async def check_node(self, state: AnalystAgentsState):
        """Validate the generated analysis plan using a critic mechanism.

        This node evaluates the generated plan for accuracy, feasibility, and
        alignment with the research goal. It uses an LLM as a critic to score
        the plan and provide feedback. If the plan is approved or max retries
        are reached, it proceeds to tool extraction. Otherwise, it returns
        feedback to revise the plan.

        Args:
            state: The current workflow state containing goal_description,
                   data_list, method_context, plan, and plan_retries.

        Returns:
            A dictionary containing plan_feedback.
        """
        check_prompt = get_prompt(
            self.ac.PROMPT_FILE,
            "user/meta_step_check",
            {
                "goal_description": state["goal_description"],
                "data_list": str(state["data_list"]),
                "method_context": state["method_context"],
                "current_plan": state["plan"],
            },
        )
        max_retries = self.ac.MAX_RETRIES
        current_retries = state.get("plan_retries", 0)
        try:
            phyto_response = await phyto_chat(
                user_query=check_prompt,
                prompt_file=self.ac.PROMPT_FILE,
                prompt_path=self.ac.PROMPT_PATH,
                api_key=self.sc.API_KEY.get_secret_value(),
                base_url=self.sc.BASE_URL,
                model=self.sc.MODEL_ID,
                frequency_penalty=self.ac.FREQUENCY_PENALTY,
                n=self.ac.N,
                presence_penalty=self.ac.PRESENCE_PENALTY,
                reasoning_effort=self.ac.REASONING_EFFORT,
                response_format={"type": "json_object"},
                stream=self.ac.STREAM,
                temperature=self.ac.TEMPERATURE,
                top_p=self.ac.TOP_P,
                user=self.ac.USER,
                timeout=self.ac.TIMEOUT,
                retriable_codes=self.ac.RETRIABLE_CODES,
                max_retries=self.ac.MAX_RETRIES,
            )
            content = "{}"
            if (
                phyto_response
                and phyto_response.get("choices")
                and len(phyto_response["choices"]) > 0
                and phyto_response["choices"][0].get("message")
                and phyto_response["choices"][0]["message"].get("content")
            ):
                content = phyto_response["choices"][0]["message"]["content"]
            pattern = r"```json(.*?)```"
            match = re.search(pattern, content, re.DOTALL)
            if match:
                json_string = match.group(1).strip()
                result = json.loads(json_string)
            else:
                result = json.loads(content)
            score = result.get("score", 0)
            decision = result.get("decision", "REJECTED")
            feedback = result.get("feedback", "")
        except Exception:
            score = 0
            decision = "REJECTED"
            feedback = ""
        print("===================Check===================")
        print(f"Retries: {current_retries}/{max_retries}")
        print(f"Score: {score}")
        print(f"Feedback: {feedback}")
        print("==========================================")
        if decision == "APPROVED" or current_retries >= max_retries:
            return {"plan_feedback": "APPROVED"}
        else:
            return {"plan_feedback": feedback}

    async def tool_extract_node(self, state: AnalystAgentsState) -> dict:
        """Extract required bioinformatics tools from the analysis plan.

        This node analyzes the generated plan and extracts the specific tools,
        algorithms, or software mentioned that are needed to execute the
        workflow.

        Args:
            state: The current workflow state containing plan.

        Returns:
            A dictionary containing the extracted_tools list.

        Raises:
            McpError: If parsing the tool extraction response fails.
        """
        tool_extract_prompt = get_prompt(
            self.ac.PROMPT_FILE, "user/tool_extract", {"plan": state["plan"]}
        )
        phyto_response = await phyto_chat(
            user_query=tool_extract_prompt,
            prompt_file=self.ac.PROMPT_FILE,
            prompt_path=self.ac.PROMPT_PATH,
            api_key=self.sc.API_KEY.get_secret_value(),
            base_url=self.sc.BASE_URL,
            model=self.sc.MODEL_ID,
            frequency_penalty=self.ac.FREQUENCY_PENALTY,
            n=self.ac.N,
            presence_penalty=self.ac.PRESENCE_PENALTY,
            reasoning_effort=self.ac.REASONING_EFFORT,
            response_format={"type": "json_object"},
            stream=self.ac.STREAM,
            temperature=self.ac.TEMPERATURE,
            top_p=self.ac.TOP_P,
            user=self.ac.USER,
            timeout=self.ac.TIMEOUT,
            retriable_codes=self.ac.RETRIABLE_CODES,
            max_retries=self.ac.MAX_RETRIES,
        )
        content = "{}"
        if (
            phyto_response
            and phyto_response.get("choices")
            and len(phyto_response["choices"]) > 0
            and phyto_response["choices"][0].get("message")
            and phyto_response["choices"][0]["message"].get("content")
        ):
            content = phyto_response["choices"][0]["message"]["content"]
        pattern = r"```json(.*?)```"
        match = re.search(pattern, content, re.DOTALL)
        if match:
            json_string = match.group(1).strip()
            result = json.loads(json_string)
        else:
            result = json.loads(content)
        print("===================Tools===================")
        print(result["tools"])
        print("===========================================")
        return {"extracted_tools": result["tools"]}

    async def tool_retrieve_node(self, state: AnalystAgentsState) -> dict:
        """Retrieve usage instructions for the extracted tools.

        This node queries the knowledge base for documentation, usage examples,
        and instructions for each tool extracted from the plan. The retrieved
        information is formatted and combined into tool_usages for the
        executor.

        Args:
            state: The current workflow state containing extracted_tools.

        Returns:
            A dictionary containing the tool_usages string with all retrieved
            documentation.
        """
        tools = state.get("extracted_tools", [])
        tool_usages = ""
        for tool in tools:
            tool_usages += f"[{tool} Usage START]\n"
            try:
                tool_usage_info = await retrieve(
                    user_query=tool,
                    retrieve_url=self.ac.RETRIEVE_URL,
                    repo_id=self.ac.TOOL_REPO_ID,
                    page_num=self.ac.TOOL_PAGE_NUM,
                    page_size=self.ac.TOOL_PAGE_SIZE,
                    filter_string=self.ac.FILTER_STRING,
                    scope=self.ac.SCOPE,
                    extra_repo_ids=self.ac.EXTRA_REPO_IDS,
                    rerank_url=self.ac.RERANK_URL,
                    rerank_batch_size=self.ac.RERANK_BATCH_SIZE,
                    score_threshold=self.ac.SCORE_THRESHOLD,
                    timeout=self.ac.TIMEOUT,
                    retriable_codes=self.ac.RETRIABLE_CODES,
                    max_retries=self.ac.MAX_RETRIES,
                )
            except Exception:
                tool_usage_info = {"doc_list": []}
            for doc in tool_usage_info["doc_list"]:
                tool_usages += f"{doc['content']}\n"
            tool_usages += f"[{tool} Usage END]\n\n\n"
        print("===================Tools Usage===================")
        print(tool_usages)
        print("=================================================")
        return {"tool_usages": tool_usages}

    async def submit_node(self, state: AnalystAgentsState):
        """Prepare and submit the analysis task to the computation platform.

        This node constructs the job payload including the analysis plan,
        selected data files, and tool usage instructions. It creates an
        output directory, uploads the metadata to OBS, and submits the job
        to the analysis platform.

        Args:
            state: The current workflow state containing goal_description,
                   data_list, output_dir, plan, tool_usages, and
                   compute_resource.

        Returns:
            A dictionary containing task_id, task_status, job_name, and
            output_dir.

        Raises:
            McpError: If task submission fails after all retries.
        """
        timeout = self.ac.TIMEOUT
        max_retries = self.ac.MAX_RETRIES
        client_timeout = Timeout(timeout, connect=timeout)
        analysis_url = self.ac.ANALYSIS_URL

        raw_data_list = state.get("data_list", {})
        processed_data_list = {}
        for k, v in raw_data_list.items():
            if isinstance(k, str) and k.startswith("obs://"):
                new_key = "/obs/" + k[6:].lstrip("/")
                processed_data_list[new_key] = v
            else:
                processed_data_list[k] = v

        plan = state.get("plan", "")
        tool_usages = state.get("tool_usages", "")
        plan += (
            "\nnext step, summarize each of the generated result files "
            "(including images, result files, etc.) into a json file (named "
            "`result_files.json`) and save it, with the key of the file "
            "being the absolute path of the generated result and the value "
            "being a detailed description of the file.\nlast step, compress "
            "the output folder into a zip file (zip -r $output_dir.zip "
            "$output_dir)."
        )

        final_meta = f"### EXECUTION PLAN\n{plan}\n\n"
        f"### TOOL USAGE\n{tool_usages}"

        output_dir = state.get("output_dir")
        if self.ac.CREATE_DIR:
            output_dir = create_output_dir(
                user_id=self.ac.USER_ID or str(uuid1()),
                task="analysis_agents_task",
                access_key_id=self.sc.AccessKeyID.get_secret_value(),
                secret_access_key=self.sc.SecretAccessKey.get_secret_value(),
                obs_server=self.ac.OBS_SERVER,
                bucket_name=self.ac.BUCKET_NAME,
            )

        submit_payload = {
            "goal_description": state.get("goal_description"),
            "data_list": processed_data_list,
            "output_dir": output_dir,
            "meta": final_meta,
            "execute_code": self.ac.EXECUTE_CODE,
            "model_url": self.sc.CODER_URL,
            "model_name": self.sc.CODER_MODEL,
            "api_key": self.sc.CODER_API_KEY.get_secret_value(),
        }

        json_file = Path(f"{uuid1()}.json")
        try:
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(submit_payload, f)

            obs_meta_path = upload_analyst_agents_data(
                analyst_agents_datapath=str(json_file),
                access_key_id=self.sc.AccessKeyID.get_secret_value(),
                secret_access_key=self.sc.SecretAccessKey.get_secret_value(),
                obs_server=self.ac.OBS_SERVER,
                bucket_name=self.ac.BUCKET_NAME,
            )
        finally:
            if json_file.exists():
                json_file.unlink()

        token = await get_token(
            timeout=self.ac.TIMEOUT, region=self.ac.ANALYSIS_REGION
        )
        job_headers = {
            "Content-Type": "application/json",
            "X-Auth-Token": token,
        }

        time_stamp = datetime.datetime.now().strftime("%H%M%S-%f")
        job_name = f"{self.ac.TASK_NAME.replace('_', '-')}-{time_stamp}"
        compute_res = state.get("compute_resource", self.ac.COMPUTE_RESOURCE)
        resource = self.ac.RESOURCE[compute_res]

        job_data = {
            "name": job_name,
            "timeout": self.ac.MAX_POLL,
            "tool_id": self.ac.APP_ID[compute_res],
            "tool_type": "app",
            "tasks": [
                {
                    "task_name": f"analyst-agents-{compute_res}",
                    "display_name": job_name,
                    "inputs": [
                        {
                            "name": "obs-mount",
                            "type": "DIRECTORY",
                            "values": ["phytomni:/agent_data/"],
                        },
                        {
                            "name": "meta-file",
                            "type": "FILE",
                            "values": [obs_meta_path],
                        },
                    ],
                    "resources": {
                        "cpu": f"{resource['cpu']}C",
                        "memory": f"{resource['memory']}G",
                        "cpu_type": "X86",
                    },
                }
            ],
            "automatic": True,
        }

        async with AsyncClient(timeout=client_timeout, verify=False) as client:
            for attempt in range(max_retries + 1):
                try:
                    response = await client.post(
                        analysis_url,
                        headers=job_headers,
                        json=job_data,
                    )
                    if response.status_code == 201:
                        print("===================Submit===================")
                        print(f"Job_Name: {job_name}")
                        print(f"Task_id: {json.loads(response.text)['id']}")
                        print(f"Output_Dir: {output_dir}")
                        print("Task_Status: RUNNING")
                        print("============================================")
                        return {
                            "task_id": json.loads(response.text)["id"],
                            "task_status": "PENDING",
                            "job_name": job_name,
                            "output_dir": output_dir,
                        }
                    raise McpError(
                        ErrorData(
                            code=INTERNAL_ERROR,
                            message="Failed to submit task",
                        )
                    )

                except HTTPStatusError as e:
                    if (
                        hasattr(e, "response")
                        and e.response is not None
                        and e.response.status_code in self.ac.RETRIABLE_CODES
                        and attempt < max_retries
                    ):
                        wait_time = (2**attempt) + uniform(0, 1)
                        await asyncio.sleep(wait_time)
                        continue
                    raise McpError(
                        ErrorData(
                            code=INTERNAL_ERROR,
                            message=f"Failed to submit task: {str(e)}",
                        )
                    ) from e

                except (ConnectError, TimeoutException) as e:
                    if attempt < max_retries:
                        await asyncio.sleep(1.5**attempt)
                        continue
                    raise McpError(
                        ErrorData(
                            code=INTERNAL_ERROR,
                            message=f"Network error: {str(e)}",
                        )
                    ) from e

        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR, message="Submission failed after retries"
            )
        )

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
        await asyncio.sleep(self.ac.POLL_INTERVAL)
        task_id = state["task_id"]
        try:
            status_data = await task_status(
                task_id,
                analysis_url=self.ac.ANALYSIS_URL,
                region=self.ac.ANALYSIS_REGION,
                timeout=self.ac.TIMEOUT,
                retriable_codes=self.ac.RETRIABLE_CODES,
                max_retries=self.ac.MAX_RETRIES,
            )
            current_status = status_data.get("status")
            return {"task_status": current_status}
        except Exception as exc:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Task status request failed: {str(exc)}",
                )
            )

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
        else:
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

        # 如果节点返回了 "APPROVED"，说明通过检查
        if feedback == "APPROVED":
            return "tool_extract_node"
        # 否则带着 feedback 回到 plan_node 重写
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
        goal_description: Optional[str] = None,
        user: str = ac.USER,
        user_id: str = ac.USER_ID,
        is_create_dir: bool = ac.CREATE_DIR,
        output_dir: str = ac.OUTPUT_DIR,
        execute_code: bool = ac.EXECUTE_CODE,
        compute_resource: Literal[
            "small", "medium", "large"
        ] = ac.COMPUTE_RESOURCE,
        timeout: float = ac.TIMEOUT,
        max_retries: int = ac.MAX_RETRIES,
        reasoning_effort: Optional[str] = ac.REASONING_EFFORT,
        frequency_penalty: float = ac.FREQUENCY_PENALTY,
        presence_penalty: float = ac.PRESENCE_PENALTY,
        n: int = ac.N,
        stream: bool = ac.STREAM,
        temperature: float = ac.TEMPERATURE,
        top_p: float = ac.TOP_P,
        prompt_file: str = ac.PROMPT_FILE,
        preset_data_list: Optional[Any] = None,
        obs_file_list: List = [],
        preset_plan: Optional[str] = None,
        thread_id: Optional[str] = None,
        is_auto_select: bool = True,
        is_polling: bool = True,
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
        if not thread_id:
            thread_id = str(uuid1())

        initial_state = {
            "query": query,
            "goal_description": goal_description,
            "obs_file_list": obs_file_list,
            "data_list": preset_data_list or {},
            "output_dir": output_dir,
            "compute_resource": compute_resource,
            "method_context": None,
            "plan": preset_plan,
            "plan_feedback": None,
            "plan_retries": 0,
            "extracted_tools": [],
            "tool_usages": "",
            "job_name": None,
            "task_id": None,
            "task_status": None,
            "is_polling": is_polling,
            "is_auto_select": is_auto_select,
        }
        config = {"configurable": {"thread_id": thread_id}}

        try:
            final_state = await self.app.ainvoke(
                cast(Any, initial_state), config=cast(Any, config)
            )
            return {
                "task_id": final_state["task_id"],
                "output_dir": final_state["output_dir"],
                "job_name": final_state["job_name"],
                "compute_resource": final_state["compute_resource"],
            }
        except Exception as e:
            return {
                **initial_state,
                "task_status": "FAILED_AT_AGENT_LEVEL",
                "error_detail": str(e),
            }


def _analyst_config_with_overrides(
    user_id: str,
    is_create_dir: bool,
    output_dir: str,
    compute_resource: Literal["small", "medium", "large"],
    **kwargs: Any,
):
    """Build an AnalystConfig copy from compatibility wrapper arguments."""
    return copy_config_with_overrides(
        ac,
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
        sc,
        kwargs,
        field_map=ANALYST_SENSITIVE_FIELD_MAP,
        secret_field_map=ANALYST_SECRET_FIELD_MAP,
    )


async def submit(
    goal_description: str,
    data_list: Any,
    user_id: str = ac.USER_ID,
    is_create_dir: bool = ac.CREATE_DIR,
    output_dir: str = ac.OUTPUT_DIR,
    meta: str = "",
    compute_resource: Literal[
        "small", "medium", "large"
    ] = ac.COMPUTE_RESOURCE,
    enable_auto_select: bool = True,
    meta_meta: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Compatibility wrapper around the LangGraph-based AnalystAgent."""
    user_id = user_id or str(uuid1())
    analyst_config = _analyst_config_with_overrides(
        user_id=user_id,
        is_create_dir=is_create_dir,
        output_dir=output_dir,
        compute_resource=compute_resource,
        **kwargs,
    )
    agent = AnalystAgent(
        analyst_config=analyst_config,
        sensitive_config=_sensitive_config_with_overrides(**kwargs),
    )
    return await agent.arun(
        query=goal_description,
        goal_description=goal_description,
        output_dir=output_dir,
        compute_resource=compute_resource,
        preset_data_list=data_list,
        preset_plan=meta + (meta_meta or ""),
        thread_id=user_id,
        is_auto_select=enable_auto_select,
        is_polling=False,
    )


async def retrieve_plan_submit(
    goal_description: str,
    data_list: Dict[str, str],
    user_id: str = ac.USER_ID,
    is_create_dir: bool = ac.CREATE_DIR,
    output_dir: str = ac.OUTPUT_DIR,
    compute_resource: Literal[
        "small", "medium", "large"
    ] = ac.COMPUTE_RESOURCE,
    meta_meta: Optional[str] = None,
    obs_file_list: Optional[List[str]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Compatibility wrapper around the LangGraph-based AnalystAgent."""
    user_id = user_id or str(uuid1())
    analyst_config = _analyst_config_with_overrides(
        user_id=user_id,
        is_create_dir=is_create_dir,
        output_dir=output_dir,
        compute_resource=compute_resource,
        **kwargs,
    )
    agent = AnalystAgent(
        analyst_config=analyst_config,
        sensitive_config=_sensitive_config_with_overrides(**kwargs),
    )
    result = await agent.arun(
        query=goal_description,
        goal_description=goal_description,
        output_dir=output_dir,
        compute_resource=compute_resource,
        preset_data_list=data_list,
        obs_file_list=obs_file_list or [],
        thread_id=user_id,
        is_auto_select=True,
        is_polling=False,
    )
    if meta_meta:
        result["meta_meta"] = meta_meta
    return result


async def wait_for_completion(
    task_id: str,
    analysis_url: str = ac.ANALYSIS_URL,
    region: str = ac.ANALYSIS_REGION,
    timeout: float = ac.TIMEOUT,
    retriable_codes: List[int] = ac.RETRIABLE_CODES,
    max_retries: int = ac.MAX_RETRIES,
    poll_interval: float = ac.POLL_INTERVAL,
    max_poll: float = ac.MAX_POLL,
) -> Dict[str, Any]:
    """Poll a submitted task until it reaches a terminal status."""
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
    analysis_url: str = ac.ANALYSIS_URL,
    region: str = ac.ANALYSIS_REGION,
    timeout: float = ac.TIMEOUT,
    retriable_codes: List[int] = ac.RETRIABLE_CODES,
    max_retries: int = ac.MAX_RETRIES,
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
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        for attempt in range(max_retries + 1):
            try:
                response = await client.post(
                    url=f"{analysis_url}/{task_id}/terminate",
                    headers={
                        "Content-Type": "application/json",
                        "X-Auth-Token": await get_token(
                            timeout=timeout, region=region
                        ),
                    },
                    json={"force": True},
                    timeout=timeout,
                )
                if response.status_code == 200:
                    return f"Delete task {task_id} success."
                raise McpError(
                    ErrorData(
                        code=INTERNAL_ERROR, message="Failed to delete task"
                    )
                )

            except HTTPStatusError as e:
                if (
                    hasattr(e, "response")
                    and e.response is not None
                    and e.response.status_code in retriable_codes
                    and attempt < max_retries
                ):
                    wait_time = (2**attempt) + uniform(0, 1)
                    await asyncio.sleep(wait_time)
                    continue
                raise McpError(
                    ErrorData(
                        code=INTERNAL_ERROR,
                        message=f"Failed to delete task: {str(e)}",
                    )
                ) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5**attempt)
                    continue
                raise McpError(
                    ErrorData(
                        code=INTERNAL_ERROR,
                        message=f"Network error: {str(e)}",
                    )
                ) from e

    raise McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message="Failed to delete task after all retries",
        )
    )


async def task_status(
    task_id: str,
    analysis_url: str = ac.ANALYSIS_URL,
    region: str = ac.ANALYSIS_REGION,
    timeout: float = ac.TIMEOUT,
    retriable_codes: List[int] = ac.RETRIABLE_CODES,
    max_retries: int = ac.MAX_RETRIES,
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
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        for attempt in range(max_retries + 1):
            try:
                response = await client.get(
                    f"{analysis_url}/{task_id}",
                    headers={
                        "Content-Type": "application/json",
                        "X-Auth-Token": await get_token(
                            timeout=timeout, region=region
                        ),
                    },
                    timeout=timeout,
                )
                if response.status_code == 200:
                    return response.json()
                raise McpError(
                    ErrorData(
                        code=INTERNAL_ERROR,
                        message=f"Check task {task_id} status failed.",
                    )
                )

            except HTTPStatusError as e:
                if (
                    hasattr(e, "response")
                    and e.response is not None
                    and e.response.status_code in retriable_codes
                    and attempt < max_retries
                ):
                    wait_time = (2**attempt) + uniform(0, 1)
                    await asyncio.sleep(wait_time)
                    continue
                raise McpError(
                    ErrorData(
                        code=INTERNAL_ERROR,
                        message=f"Failed to delete task: {str(e)}",
                    )
                ) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5**attempt)
                    continue
                raise McpError(
                    ErrorData(
                        code=INTERNAL_ERROR,
                        message=f"Network error: {str(e)}",
                    )
                ) from e

    raise McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message="Failed to check task status after all retries",
        )
    )


async def task_log(
    task_id: str,
    analysis_url: str = ac.ANALYSIS_URL,
    compute_resource: Literal[
        "small", "medium", "large"
    ] = ac.COMPUTE_RESOURCE,
    region: str = ac.ANALYSIS_REGION,
    timeout: float = ac.TIMEOUT,
    retriable_codes: List[int] = ac.RETRIABLE_CODES,
    max_retries: int = ac.MAX_RETRIES,
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
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        for attempt in range(max_retries + 1):
            try:
                response = await client.get(
                    f"{analysis_url}/{task_id}/logs"
                    f"?task_name=analyst-agents-{compute_resource}",
                    headers={
                        "Content-Type": "application/json",
                        "X-Auth-Token": await get_token(
                            timeout=timeout, region=region
                        ),
                    },
                    timeout=timeout,
                )
                if response.status_code == 200:
                    return response.json()
                raise McpError(
                    ErrorData(
                        code=INTERNAL_ERROR,
                        message=f"Check task {task_id} log failed.",
                    )
                )

            except HTTPStatusError as e:
                if (
                    hasattr(e, "response")
                    and e.response is not None
                    and e.response.status_code in retriable_codes
                    and attempt < max_retries
                ):
                    wait_time = (2**attempt) + uniform(0, 1)
                    await asyncio.sleep(wait_time)
                    continue
                raise McpError(
                    ErrorData(
                        code=INTERNAL_ERROR,
                        message=f"Failed to delete task: {str(e)}",
                    )
                ) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5**attempt)
                    continue
                raise McpError(
                    ErrorData(
                        code=INTERNAL_ERROR,
                        message=f"Network error: {str(e)}",
                    )
                ) from e

    raise McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message="Failed to check task log after all retries",
        )
    )


def upload_analyst_agents_data(
    analyst_agents_datapath: str,
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = ac.OBS_SERVER,
    bucket_name: str = ac.BUCKET_NAME,
) -> str:
    """
    Uploads data to an Object Storage Service (OBS) bucket.

    This function takes a local file path and uploads the file to a specified
    OBS bucket. It handles the connection and authentication with the OBS
    service.

    Args:
        analyst_agents_datapath: The local path to the file to be uploaded.
        access_key_id: The access key ID for the OBS bucket.
        secret_access_key: The secret access key for the OBS bucket.
        obs_server: The server address of the OBS.
        bucket_name: The name of the OBS bucket.

    Returns:
        The OBS path of the uploaded file, in the format
        'bucket_name:/object_key'.

    Raises:
        OSError: If the file upload to OBS fails.
    """
    obsclient = ObsClient(
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        server=obs_server,
    )
    try:
        headers = PutObjectHeader()
        headers.contentType = "text/plain"
        object_file = analyst_agents_datapath.split("/")[-1]
        object_key = f"agent_data/tmp_data/{object_file}"
        response = obsclient.putFile(
            bucketName=bucket_name,
            objectKey=object_key,
            file_path=object_file,
            metadata={"meta1": "value1", "meta2": "value2"},
            headers=headers,
        )
        status_code = getattr(response, "status", None)
        if status_code is not None and status_code < 300:
            return f"{bucket_name}:/{object_key}"
        raise OSError(
            "Put File Failed\n"
            f"requestId: {getattr(response, 'requestId', 'unknown')}\n"
            f"errorCode: {getattr(response, 'errorCode', 'unknown')}\n"
            f"errorMessage: {getattr(response, 'errorMessage', 'unknown')}"
        )
    except Exception as exc:
        raise OSError(f"Put File Failed\n{format_exc()}") from exc


def delete_analyst_agents_data(
    analyst_agents_datapath: str,
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = ac.OBS_SERVER,
    bucket_name: str = ac.BUCKET_NAME,
) -> str:
    """
    Deletes data from an Object Storage Service (OBS) bucket.

    This function removes a specified object from an OBS bucket using its path.
    It handles the connection and authentication required for the deletion.

    Args:
        analyst_agents_datapath: The OBS path of the file to be deleted.
        access_key_id: The access key ID for the OBS bucket.
        secret_access_key: The secret access key for the OBS bucket.
        obs_server: The server address of the OBS.
        bucket_name: The name of the OBS bucket.

    Returns:
        A confirmation message indicating the successful deletion of the
        object, including details like the request ID.

    Raises:
        OSError: If the file deletion from OBS fails.
    """
    obsclient = ObsClient(
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        server=obs_server,
    )
    try:
        object_key = analyst_agents_datapath
        response = obsclient.deleteObject(bucket_name, object_key)
        status_code = getattr(response, "status", None)
        if status_code is not None and status_code < 300:
            delete_marker = getattr(response, "body", {}).get(
                "deleteMarker", "unknown"
            )
            version_id = getattr(response, "body", {}).get(
                "versionId", "unknown"
            )
            return (
                "Delete Object Succeeded\n"
                f"requestId: {getattr(response, 'requestId', 'unknown')}\n"
                f"deleteMarker: {delete_marker}\nversionId: {version_id}"
            )
        raise OSError(
            "Delete Object Failed\n"
            f"requestId: {getattr(response, 'requestId', 'unknown')}\n"
            f"errorCode: {getattr(response, 'errorCode', 'unknown')}\n"
            f"errorMessage: {getattr(response, 'errorMessage', 'unknown')}"
        )
    except Exception as exc:
        raise OSError(f"Delete Object Failed\n{format_exc()}") from exc


def get_data_list(data_file: str, analysis_type: str, species: str) -> list:
    """Generate ready-to-use prompt from template components.

    Combines template loading and rendering in one workflow:
    1. Load base template from YAML file
    2. Apply parameter substitutions

    Args:
        data_file: data_list_file for json format
        analysis_type: analysis_type[evolution_analysis, deepgo2_analysis,
                                     structure_analysis, prompter_analysis,
                                     protein_design_analysis,
                                     gene_expression_analysis, ppi_analysis]
        species: 65 species ...

    Returns:
        data_list for analysis
    """
    try:
        with open(data_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Data file not found: {data_file}") from exc
    try:
        analysis_data_list = data[analysis_type]
    except KeyError as exc:
        raise KeyError(f"Analysis type not found: {analysis_type}") from exc
    try:
        data_list = analysis_data_list[species]
    except KeyError as exc:
        raise KeyError(f"Species not found: {species}") from exc
    return data_list


def create_output_dir(
    user_id: str,
    task: str,
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = ac.OBS_SERVER,
    bucket_name: str = ac.BUCKET_NAME,
) -> str:
    """Create a unique output directory for analysis tasks in Object Storage
        Service.

    This function generates a timestamped, user-specific directory structure
    in OBS for storing analysis results. The directory path includes user ID,
    task type, timestamp, and a unique identifier to prevent conflicts.

    Args:
        user_id: Unique identifier for the user requesting the analysis.
        task: Name or type of the analysis task (e.g., 'network_task',
            'evolution_task').
        access_key_id: Access key identifier for Object Storage Service (OBS)
            authentication, required for directory creation operations.
        secret_access_key: Secret access key for OBS authentication, paired
            with access_key_id for secure storage operations.
        obs_server: Base URL endpoint for the Object Storage Service where
            the directory will be created.
        bucket_name: Name of the OBS bucket where the output directory
            will be created.

    Returns:
        The full OBS path to the created output directory in the format:
        '/obs/{bucket_name}/agent_data/user_data/'
        '{user_id}/output/{task}_{timestamp}_{uuid}/'

    Raises:
        OSError: If the directory creation fails due to OBS connectivity
            issues, authentication problems, or insufficient permissions.

    Examples:
        Basic usage:
            >>> output_path = create_output_dir(
            ...     user_id='user123',
            ...     task='gene_analysis'
            ... )
            >>> print(output_path)
            '/obs/phytomni/agent_data/user_data/'
            'user123/output/gene_analysis_1640995200_abc123/'

        Custom configuration:
            >>> output_path = create_output_dir(
            ...     user_id='researcher001',
            ...     task='network_analysis',
            ...     bucket_name='custom_bucket'
            ... )

    Note:
        The generated directory path includes a timestamp and UUID to ensure
        uniqueness across multiple analysis runs. The directory is created
        as an empty placeholder in OBS and can be used immediately for
        storing analysis results.
    """
    obs_client = ObsClient(
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        server=obs_server,
    )
    try:
        output_dir = (
            f"agent_data/user_data/{user_id}/output/"
            f"{task}_{int(time.time())}_{uuid1()}/"
        )
        response = obs_client.putContent(
            bucketName=bucket_name, objectKey=output_dir, content=None
        )
        status_code = getattr(response, "status", None)
        if status_code is not None and status_code < 300:
            return f"/obs/{bucket_name}/{output_dir}"
        raise OSError(
            f"Put File Failed\n"
            f"requestId: {getattr(response, 'requestId', 'unknown')}\n"
            f"errorCode: {getattr(response, 'errorCode', 'unknown')}\n"
            f"errorMessage: {getattr(response, 'errorMessage', 'unknown')}"
        )
    except Exception as exc:
        raise OSError(f"Put File Failed\n{format_exc()}") from exc


def download_obs_out(
    task_dir: str,
    obs_output_path: str,
    download_path: str = ac.DOWNLOAD_PATH,
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = ac.OBS_SERVER,
    target_file_feature: List[str] = ac.TARGET_FILE_FEATURE,
    bucket_name: str = ac.BUCKET_NAME,
    marker: Optional[str] = ac.DOWNLOAD_MARKER,
    max_keys: int = ac.DOWNLOAD_MAX_KEYS,
    if_download_all: bool = ac.IF_DOWNLOAD_ALL,
):
    """Download analysis results from Object Storage Service to local
        filesystem.

    This generator function downloads files from an OBS path to a local
    directory, with options for selective downloading based on file extensions
    or patterns. It supports pagination for large directories and provides
    progress feedback through yielded status messages.

    Args:
        task_dir: Local directory name where files will be downloaded, created
            under the download_path.
        obs_output_path: Source path in OBS containing the files to download
            (without bucket name prefix).
        download_path: Local filesystem path where the task directory will
            be created for storing downloaded files.
        access_key_id: Access key identifier for Object Storage Service (OBS)
            authentication, required for file download operations.
        secret_access_key: Secret access key for OBS authentication, paired
            with access_key_id for secure storage operations.
        obs_server: Base URL endpoint for the Object Storage Service where
            files are stored.
        target_file_feature: List of file extensions or suffixes to download
            (e.g., ['.png', '.pdf', '.csv']). Used when if_download_all is
            False.
        bucket_name: Name of the OBS bucket containing the source files.
        marker: Optional marker for pagination, specifying where to start
            listing objects in large directories.
        max_keys: Maximum number of objects to list per request, used for
            pagination control.
        if_download_all: Flag indicating whether to download all files (True)
            or only files matching target_file_feature patterns (False).

    Yields:
        str: Status messages for each file download attempt, indicating success
        or failure for individual files (e.g., "file.png download succeed").

    Raises:
        OSError: If OBS listing operations fail, directory creation fails,
            or file download operations encounter errors.

    Examples:
        Download specific file types:
            >>> for status in download_obs_out(
            ...     task_dir='analysis_001',
            ...     obs_output_path='results/gene_analysis/',
            ...     target_file_feature=['.png', '.csv'],
            ...     if_download_all=False
            ... ):
            ...     print(status)

        Download all files:
            >>> for status in download_obs_out(
            ...     task_dir='complete_results',
            ...     obs_output_path='analysis/output/',
            ...     if_download_all=True
            ... ):
            ...     print(status)

    Note:
        This function creates the local directory structure automatically.
        Downloads are performed with conditional headers to avoid unnecessary
        transfers. Large directories are handled through pagination to manage
        memory usage efficiently.
    """
    output_path = Path(f"{download_path}/{task_dir}")
    output_path.mkdir(parents=True, exist_ok=True)
    headers = GetObjectHeader()
    headers.if_modified_since = "date"
    obs_client = ObsClient(
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        server=obs_server,
    )
    try:
        while True:
            file_response = obs_client.listObjects(
                bucketName=bucket_name,
                prefix=obs_output_path,
                marker=marker,
                max_keys=max_keys,
                encoding_type="url",
            )
            file_status = getattr(file_response, "status", None)
            if file_status is not None and file_status < 300:
                file_body = getattr(file_response, "body", None)
                if file_body and hasattr(file_body, "contents"):
                    for content in file_body.contents:
                        obj_file = content.key
                        if obj_file.endswith("/"):
                            continue
                        output_file = obj_file.split("/")[-1]
                        if not if_download_all and not any(
                            output_file.endswith(suffix)
                            for suffix in target_file_feature
                        ):
                            continue
                        full_path = str(output_path / output_file)
                        download_response = obs_client.getObject(
                            bucketName=bucket_name,
                            objectKey=obj_file,
                            downloadPath=full_path,
                            headers=headers,
                        )
                        download_status = getattr(
                            download_response, "status", None
                        )
                        if (
                            download_status is not None
                            and download_status > 300
                        ):
                            yield f"{output_file} download failed."
                            continue
                        yield f"{output_file} download succeed."
                        continue
                if (
                    file_body
                    and hasattr(file_body, "is_truncated")
                    and file_body.is_truncated is True
                ):
                    marker = getattr(file_body, "next_marker", None)
                else:
                    break
            else:
                request_id = getattr(file_response, "requestId", "unknown")
                error_code = getattr(file_response, "errorCode", "unknown")
                error_message = getattr(
                    file_response, "errorMessage", "unknown"
                )
                raise OSError(
                    "Get File List Failed\n"
                    f"requestId: {request_id}\n"
                    f"errorCode: {error_code}\n"
                    f"errorMessage: {error_message}"
                )
    except Exception as exc:
        raise OSError(f"Download File Failed\n{format_exc()}") from exc
