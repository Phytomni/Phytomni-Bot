# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""LangGraph node methods for AnalystAgent."""

from __future__ import annotations

import datetime
import json
import re
from typing import TYPE_CHECKING, Any, Dict

from httpx import (
    AsyncClient,
    Timeout,
)
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from .agents.chat.service import phyto_chat
from .agents.knowledge.retrieval import multi_retrieve, retrieve
from .analyst_storage import (
    ensure_run_output_dir,
    upload_analyst_agents_content,
)
from .auth.iam import get_token
from .runtime.workflow_mixins import WorkflowMixinBase
from .storage.downloads import download_upload_context
from .storage.path_policy import RunIdentity, task_tmp_key
from .utils import (
    JsonPostRequest,
    JsonPostRetry,
    format_retrieved_doc_context,
    get_prompt,
    load_json_file,
    request_response_with_retries,
)

if TYPE_CHECKING:
    from .analyst_agents import AnalystAgentsState
else:
    AnalystAgentsState = Dict[str, Any]


class AnalystGraphMixin(WorkflowMixinBase):
    """Planning, retrieval, and submit nodes for AnalystAgent."""

    async def parse_query_node(self: Any, state: AnalystAgentsState):
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
        parse_prompt = get_prompt(
            self.analyst_config.PROMPT_FILE,
            "user/split_query",
            {"user_query": state["query"]},
        )
        phyto_response = await phyto_chat(
            user_query=parse_prompt,
            prompt_file=self.analyst_config.PROMPT_FILE,
            prompt_path=self.analyst_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            response_format={"type": "json_schema"},
            timeout=self.analyst_config.TIMEOUT,
            retriable_codes=self.analyst_config.RETRIABLE_CODES,
            max_retries=self.analyst_config.MAX_RETRIES,
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

    async def data_select_node(self: Any, state: AnalystAgentsState):
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
            species_data = load_json_file(
                self.analyst_config.PRE_PREPARED_DATA_PATH
            )
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=(
                        "Failed to load species data list from "
                        f"{self.analyst_config.PRE_PREPARED_DATA_PATH}"
                    ),
                )
            ) from exc
        data_list = state["data_list"]
        user_data_summary = json.dumps(data_list)
        selection_prompt = get_prompt(
            self.analyst_config.PROMPT_FILE,
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
                prompt_file=self.analyst_config.PROMPT_FILE,
                prompt_path=self.analyst_config.PROMPT_PATH,
                api_key=self.sensitive_config.API_KEY.get_secret_value(),
                base_url=self.sensitive_config.BASE_URL,
                model=self.sensitive_config.MODEL_ID,
                frequency_penalty=self.analyst_config.FREQUENCY_PENALTY,
                n=self.analyst_config.N,
                presence_penalty=self.analyst_config.PRESENCE_PENALTY,
                reasoning_effort=self.analyst_config.REASONING_EFFORT,
                response_format={"type": "json_schema"},
                stream=self.analyst_config.STREAM,
                temperature=self.analyst_config.TEMPERATURE,
                top_p=self.analyst_config.TOP_P,
                user=self.analyst_config.USER,
                timeout=self.analyst_config.TIMEOUT,
                retriable_codes=self.analyst_config.RETRIABLE_CODES,
                max_retries=self.analyst_config.MAX_RETRIES,
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

    async def method_retrieve_node(
        self: Any, state: AnalystAgentsState
    ) -> dict:
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
        upload_context, total_length = await download_upload_context(
            state["obs_file_list"],
            self.analyst_config,
            self.sensitive_config,
        )
        retrieve_response = await multi_retrieve(
            user_query=state["goal_description"],
            retrieve_url=self.analyst_config.RETRIEVE_URL,
            repo_id_dict=self.analyst_config.REPO_ID_DICT,
            page_num=self.analyst_config.PAGE_NUM,
            filter_string=self.analyst_config.FILTER_STRING,
            scope=self.analyst_config.SCOPE,
            extra_repo_ids=self.analyst_config.EXTRA_REPO_IDS,
            rerank_url=self.analyst_config.RERANK_URL,
            rerank_batch_size=self.analyst_config.RERANK_BATCH_SIZE,
            score_threshold=self.analyst_config.SCORE_THRESHOLD,
            top_n=self.analyst_config.TOP_N,
            timeout=self.analyst_config.TIMEOUT,
            retriable_codes=self.analyst_config.RETRIABLE_CODES,
            max_retries=self.analyst_config.MAX_RETRIES,
        )
        retrieve_context, _ = format_retrieved_doc_context(
            retrieve_response.get("doc_list", []),
            max_tokens=self.analyst_config.MAX_TOKENS,
            initial_length=total_length,
        )
        print("===================Retrieve Information===================")
        print(retrieve_context)
        print("==========================================================")
        return {
            "method_context": {
                "upload_context": upload_context,
                "retrieve_context": retrieve_context,
            }
        }

    async def plan_node(self: Any, state: AnalystAgentsState):
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
                    self.analyst_config.PROMPT_FILE,
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
                    self.analyst_config.PROMPT_FILE,
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
                    self.analyst_config.PROMPT_FILE,
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
                    self.analyst_config.PROMPT_FILE,
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
            prompt_file=self.analyst_config.PROMPT_FILE,
            prompt_path=self.analyst_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            frequency_penalty=self.analyst_config.FREQUENCY_PENALTY,
            n=self.analyst_config.N,
            presence_penalty=self.analyst_config.PRESENCE_PENALTY,
            reasoning_effort=self.analyst_config.REASONING_EFFORT,
            response_format=self.analyst_config.RESPONSE_FORMAT,
            stream=self.analyst_config.STREAM,
            temperature=self.analyst_config.TEMPERATURE,
            top_p=self.analyst_config.TOP_P,
            user=self.analyst_config.USER,
            timeout=self.analyst_config.TIMEOUT,
            retriable_codes=self.analyst_config.RETRIABLE_CODES,
            max_retries=self.analyst_config.MAX_RETRIES,
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

    async def check_node(self: Any, state: AnalystAgentsState):
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
            self.analyst_config.PROMPT_FILE,
            "user/meta_step_check",
            {
                "goal_description": state["goal_description"],
                "data_list": str(state["data_list"]),
                "method_context": state["method_context"],
                "current_plan": state["plan"],
            },
        )
        max_retries = self.analyst_config.MAX_RETRIES
        current_retries = state.get("plan_retries", 0)
        try:
            phyto_response = await phyto_chat(
                user_query=check_prompt,
                prompt_file=self.analyst_config.PROMPT_FILE,
                prompt_path=self.analyst_config.PROMPT_PATH,
                api_key=self.sensitive_config.API_KEY.get_secret_value(),
                base_url=self.sensitive_config.BASE_URL,
                model=self.sensitive_config.MODEL_ID,
                frequency_penalty=self.analyst_config.FREQUENCY_PENALTY,
                n=self.analyst_config.N,
                presence_penalty=self.analyst_config.PRESENCE_PENALTY,
                reasoning_effort=self.analyst_config.REASONING_EFFORT,
                response_format={"type": "json_object"},
                stream=self.analyst_config.STREAM,
                temperature=self.analyst_config.TEMPERATURE,
                top_p=self.analyst_config.TOP_P,
                user=self.analyst_config.USER,
                timeout=self.analyst_config.TIMEOUT,
                retriable_codes=self.analyst_config.RETRIABLE_CODES,
                max_retries=self.analyst_config.MAX_RETRIES,
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
        except (json.JSONDecodeError, TypeError, AttributeError):
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
        return {"plan_feedback": feedback}

    async def tool_extract_node(self: Any, state: AnalystAgentsState) -> dict:
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
            self.analyst_config.PROMPT_FILE,
            "user/tool_extract",
            {"plan": state["plan"]},
        )
        phyto_response = await phyto_chat(
            user_query=tool_extract_prompt,
            prompt_file=self.analyst_config.PROMPT_FILE,
            prompt_path=self.analyst_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            frequency_penalty=self.analyst_config.FREQUENCY_PENALTY,
            n=self.analyst_config.N,
            presence_penalty=self.analyst_config.PRESENCE_PENALTY,
            reasoning_effort=self.analyst_config.REASONING_EFFORT,
            response_format={"type": "json_object"},
            stream=self.analyst_config.STREAM,
            temperature=self.analyst_config.TEMPERATURE,
            top_p=self.analyst_config.TOP_P,
            user=self.analyst_config.USER,
            timeout=self.analyst_config.TIMEOUT,
            retriable_codes=self.analyst_config.RETRIABLE_CODES,
            max_retries=self.analyst_config.MAX_RETRIES,
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

    async def tool_retrieve_node(self: Any, state: AnalystAgentsState) -> dict:
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
                    retrieve_url=self.analyst_config.RETRIEVE_URL,
                    repo_id=self.analyst_config.TOOL_REPO_ID,
                    page_num=self.analyst_config.TOOL_PAGE_NUM,
                    page_size=self.analyst_config.TOOL_PAGE_SIZE,
                    filter_string=self.analyst_config.FILTER_STRING,
                    scope=self.analyst_config.SCOPE,
                    extra_repo_ids=self.analyst_config.EXTRA_REPO_IDS,
                    rerank_url=self.analyst_config.RERANK_URL,
                    rerank_batch_size=self.analyst_config.RERANK_BATCH_SIZE,
                    score_threshold=self.analyst_config.SCORE_THRESHOLD,
                    timeout=self.analyst_config.TIMEOUT,
                    retriable_codes=self.analyst_config.RETRIABLE_CODES,
                    max_retries=self.analyst_config.MAX_RETRIES,
                )
            except McpError:
                tool_usage_info = {"doc_list": []}
            for doc in tool_usage_info["doc_list"]:
                tool_usages += f"{doc['content']}\n"
            tool_usages += f"[{tool} Usage END]\n\n\n"
        print("===================Tools Usage===================")
        print(tool_usages)
        print("=================================================")
        return {"tool_usages": tool_usages}

    async def submit_node(self: Any, state: AnalystAgentsState):
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
        run_identity = self._submit_run_identity()
        output_dir = self._submit_output_dir(state, run_identity)
        obs_meta_path = self._upload_submit_meta(
            state,
            output_dir,
            run_identity,
        )
        job_headers = await self._submit_headers()
        job_name, job_data = self._submit_job_data(
            state,
            obs_meta_path,
        )
        return await self._post_submit_job(
            job_headers,
            job_data,
            job_name,
            output_dir,
        )

    def _submit_run_identity(self: Any) -> RunIdentity:
        """Return the shared run identity for one submit request."""
        return RunIdentity.create(
            user_id=self.analyst_config.USER_ID,
            scope="analysis_agents_task",
        )

    def _submit_output_dir(
        self: Any,
        state: AnalystAgentsState,
        run_identity: RunIdentity,
    ) -> str:
        """Return an existing or newly created submit output directory."""
        output_dir = str(state.get("output_dir") or "")
        if not self.analyst_config.CREATE_DIR:
            return output_dir
        return ensure_run_output_dir(
            self.analyst_config,
            self.sensitive_config,
            "analysis_agents_task",
            run_identity,
            output_dir,
        )

    def _upload_submit_meta(
        self: Any,
        state: AnalystAgentsState,
        output_dir: str,
        run_identity: RunIdentity,
    ) -> str:
        """Upload submit metadata content to OBS storage."""
        access_key_id, secret_access_key = (
            self.sensitive_config.obs_credentials()
        )
        object_name = "submit.json"
        return upload_analyst_agents_content(
            content=json.dumps(self._submit_payload(state, output_dir)),
            object_name=object_name,
            object_key=task_tmp_key(
                run_identity,
                "analysis_agents_task",
                object_name,
            ),
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=self.analyst_config.OBS_SERVER,
            bucket_name=self.analyst_config.BUCKET_NAME,
        )

    def _submit_payload(
        self: Any,
        state: AnalystAgentsState,
        output_dir: str,
    ) -> Dict[str, Any]:
        """Build the metadata payload consumed by the compute task."""
        return {
            "goal_description": state.get("goal_description"),
            "data_list": self._processed_data_list(state),
            "output_dir": output_dir,
            "meta": self._submit_meta(state),
            "execute_code": self.analyst_config.EXECUTE_CODE,
            "model_url": self.sensitive_config.CODER_URL,
            "model_name": self.sensitive_config.CODER_MODEL,
            "api_key": self.sensitive_config.CODER_API_KEY.get_secret_value(),
        }

    @staticmethod
    def _processed_data_list(state: AnalystAgentsState) -> Dict[Any, Any]:
        """Normalize OBS URL keys for the analysis platform."""
        processed_data_list = {}
        for key, value in state.get("data_list", {}).items():
            if isinstance(key, str) and key.startswith("obs://"):
                processed_data_list["/obs/" + key[6:].lstrip("/")] = value
            else:
                processed_data_list[key] = value
        return processed_data_list

    @staticmethod
    def _submit_meta(state: AnalystAgentsState) -> str:
        """Return the final submit plan and tool usage metadata."""
        plan = state.get("plan", "") + (
            "\nnext step, summarize each of the generated result files "
            "(including images, result files, etc.) into a json file (named "
            "`result_files.json`) and save it, with the key of the file "
            "being the absolute path of the generated result and the value "
            "being a detailed description of the file.\nlast step, compress "
            "the output folder into a zip file (zip -r $output_dir.zip "
            "$output_dir)."
        )
        return (
            f"### EXECUTION PLAN\n{plan}\n\n"
            f"### TOOL USAGE\n{state.get('tool_usages', '')}"
        )

    async def _submit_headers(self: Any) -> Dict[str, str]:
        """Return authenticated submit headers."""
        token = await get_token(
            timeout=self.analyst_config.TIMEOUT,
            region=self.analyst_config.ANALYSIS_REGION,
        )
        return {"Content-Type": "application/json", "X-Auth-Token": token}

    def _submit_job_data(
        self: Any,
        state: AnalystAgentsState,
        obs_meta_path: str,
    ) -> tuple[str, Dict[str, Any]]:
        """Build analysis platform job name and payload."""
        time_stamp = datetime.datetime.now().strftime("%H%M%S-%f")
        job_name = (
            f"{self.analyst_config.TASK_NAME.replace('_', '-')}-{time_stamp}"
        )
        compute_res = state.get(
            "compute_resource", self.analyst_config.COMPUTE_RESOURCE
        )
        resource = self.analyst_config.RESOURCE[compute_res]
        return job_name, {
            "name": job_name,
            "timeout": self.analyst_config.MAX_POLL,
            "tool_id": self.analyst_config.APP_ID[compute_res],
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

    async def _post_submit_job(
        self: Any,
        job_headers: Dict[str, str],
        job_data: Dict[str, Any],
        job_name: str,
        output_dir: str,
    ) -> Dict[str, Any]:
        """Submit the job payload to the analysis platform with retries."""
        timeout = self.analyst_config.TIMEOUT
        max_retries = self.analyst_config.MAX_RETRIES
        client_timeout = Timeout(timeout, connect=timeout)
        analysis_url = self.analyst_config.ANALYSIS_URL

        async with AsyncClient(timeout=client_timeout, verify=False) as client:
            response = await request_response_with_retries(
                client,
                JsonPostRequest(
                    url=analysis_url,
                    headers=job_headers,
                    json_body=job_data,
                ),
                JsonPostRetry(
                    timeout=timeout,
                    max_retries=max_retries,
                    retriable_codes=self.analyst_config.RETRIABLE_CODES,
                    message="Failed to submit task",
                ),
            )
            if response is not None and response.status_code == 201:
                payload = response.json()
                print("===================Submit===================")
                print(f"Job_Name: {job_name}")
                print(f"Task_id: {payload['id']}")
                print(f"Output_Dir: {output_dir}")
                print("Task_Status: RUNNING")
                print("============================================")
                return {
                    "task_id": payload["id"],
                    "task_status": "PENDING",
                    "job_name": job_name,
                    "output_dir": output_dir,
                }

        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR, message="Submission failed after retries"
            )
        )
