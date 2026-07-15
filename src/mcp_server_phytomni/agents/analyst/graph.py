# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""LangGraph node methods for AnalystAgent.

Exports AnalystGraphMixin, which provides the tool-usage retrieval and
task submission nodes used by the AnalystAgent workflow graph. The
parse / data-select / method-retrieve / plan / check / tool-extract
chat sites live as prep + post pairs in ``graph_chat_subgraph`` and
``graph_knowledge_subgraph`` around the shared chat / knowledge mounts.
"""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING, Any

import yaml
from httpx import Timeout
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from ...auth.iam import get_token
from ...common.http import (
    JsonPostRequest,
    JsonPostRetry,
    request_response_with_retries,
)
from ...common.httpx_client import get_async_client
from ...common.prompts import get_prompt
from ...common.relay_client import current_relay_client
from ...config.relay_mode import relay_mode_enabled
from ...runtime.workflow_mixins import WorkflowMixinBase
from ...storage.path_policy import RunIdentity, task_tmp_key
from ..knowledge.retrieval import retrieve
from ..shared.analysis_storage import ensure_run_output_dir
from .model_yaml import build_model_yaml
from .storage import upload_analyst_agents_content

if TYPE_CHECKING:
    from .agent import AnalystAgentsState
else:
    AnalystAgentsState = dict[str, Any]

logger = logging.getLogger(__name__)


class LiteralString(str):
    """``str`` subclass marker used to render YAML scalars in literal
    block style (``|``) instead of single-line quoted form."""


class CustomDumper(yaml.SafeDumper):
    """Project YAML dumper that registers the :class:`LiteralString`
    representer below so analyst task config emits multi-line fields
    (``goal_description`` / ``meta`` / ``json_output_format``) in
    block-scalar style for analyst-backend readability."""


def literal_str_representer(dumper, data):
    """Render a :class:`LiteralString` as a YAML block scalar (``|``).

    Used by :class:`CustomDumper`; surfacing it as a free function
    keeps the representer registration line a one-liner.
    """
    return dumper.represent_scalar(
        "tag:yaml.org,2002:str", str(data), style="|"
    )


CustomDumper.add_representer(LiteralString, literal_str_representer)


class AnalystGraphMixin(WorkflowMixinBase):
    """Tool-usage retrieval and task submission nodes for AnalystAgent."""

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
        logger.debug("Tools usage: %s", tool_usages)
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
        obs_task_path, obs_model_path = await self._upload_submit_meta(
            state,
            output_dir,
            run_identity,
        )
        job_headers = await self._submit_headers()
        job_name, job_data = self._submit_job_data(
            state,
            obs_task_path,
            obs_model_path,
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
        fingerprint = state.get("input_fingerprint") or ""
        return ensure_run_output_dir(
            self.analyst_config,
            self.sensitive_config,
            "analysis_agents_task",
            run_identity,
            output_dir,
            fingerprint=fingerprint,
        )

    async def _upload_submit_meta(
        self: Any,
        state: AnalystAgentsState,
        output_dir: str,
        run_identity: RunIdentity,
    ) -> tuple[str, str]:
        """Upload submit metadata content to OBS storage.

        Returns:
            Tuple of OBS paths ``(task_yaml_path, model_yaml_path)``.
        """
        access_key_id, secret_access_key = (
            self.sensitive_config.obs_credentials()
        )
        task_object_name = "task.yaml"
        task_path = await upload_analyst_agents_content(
            content=self._submit_payload(state, output_dir),
            object_name=task_object_name,
            object_key=task_tmp_key(
                run_identity,
                "analysis_agents_task",
                task_object_name,
            ),
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=self.analyst_config.OBS_SERVER,
            bucket_name=self.analyst_config.BUCKET_NAME,
        )
        model_object_name = "model.yaml"
        model_path = await upload_analyst_agents_content(
            content=self._submit_coder_payload(),
            object_name=model_object_name,
            object_key=task_tmp_key(
                run_identity,
                "analysis_agents_config",
                model_object_name,
            ),
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=self.analyst_config.OBS_SERVER,
            bucket_name=self.analyst_config.BUCKET_NAME,
        )
        return (task_path, model_path)

    def _submit_payload(
        self: Any,
        state: AnalystAgentsState,
        output_dir: str,
    ) -> str:
        """Build the metadata payload consumed by the compute task."""
        json_format = get_prompt(
            self.analyst_config.PROMPT_FILE, "user/analysis_output_format"
        )
        task_config_dict = {
            "json_output_format": LiteralString(json_format.strip()),
            "goal_description": LiteralString(
                state.get("goal_description", "").strip()
            ),
            "meta": LiteralString(self._submit_meta(state).strip()),
            "data_list": self._processed_data_list(state),
            "output_dir": str(output_dir),
            "working_dir": "/obs",
        }
        task_config = yaml.dump(
            task_config_dict,
            Dumper=CustomDumper,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
        return task_config

    def _submit_coder_payload(self: Any) -> str:
        """Build the coder/embed model YAML payload for the compute task.

        Delegates to :func:`build_model_yaml`, which rewrites the coder /
        embed endpoints to the relay routes in customer relay mode.
        """
        return build_model_yaml(self.sensitive_config, self.analyst_config)

    @staticmethod
    def _processed_data_list(state: AnalystAgentsState) -> list[str]:
        """Normalize OBS URL keys and return a list of YAML-friendly items."""
        processed_data_list: dict[Any, Any] = {}
        for key, value in state.get("data_list", {}).items():
            if isinstance(key, str) and key.startswith("obs://"):
                processed_data_list["/obs/" + key[6:].lstrip("/")] = value
            else:
                processed_data_list[key] = value
        return [
            f"{key}: {value}" for key, value in processed_data_list.items()
        ]

    @staticmethod
    def _submit_meta(state: AnalystAgentsState) -> str:
        """Return the final submit plan and tool usage metadata."""
        preset_plan = state.get("preset_plan")
        plan = preset_plan if preset_plan else state.get("plan", "") or ""
        plan = plan + (
            "\nnext step, summarize each of the generated result files "
            "(including images, result files, etc.) into a json file (named "
            "`result_files.json`) and save it, with the key of the file "
            "being the absolute path of the generated result and the value "
            "being a detailed description of the file.\nlast step, compress "
            "the output folder into a zip file (zip -r $output_dir.zip "
            "$output_dir)."
        )
        return (
            f"  ### EXECUTION PLAN\n{plan}\n\n"
            f"### TOOL USAGE\n{state.get('tool_usages', '')}"
        )

    async def _submit_headers(self: Any) -> dict[str, str]:
        """Return authenticated submit headers (none minted in relay mode)."""
        if relay_mode_enabled():
            return {"Content-Type": "application/json"}
        token = await get_token(
            timeout=self.analyst_config.TIMEOUT,
            region=self.analyst_config.ANALYSIS_REGION,
        )
        return {"Content-Type": "application/json", "X-Auth-Token": token}

    def _submit_job_data(
        self: Any,
        state: AnalystAgentsState,
        obs_task_path: str,
        obs_model_path: str,
    ) -> tuple[str, dict[str, Any]]:
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
            "timeout": self.analyst_config.ANALYSIS_JOB_TIMEOUT,
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
                            "name": "config-file",
                            "type": "FILE",
                            "values": [obs_model_path],
                        },
                        {
                            "name": "task-yaml",
                            "type": "FILE",
                            "values": [obs_task_path],
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
        job_headers: dict[str, str],
        job_data: dict[str, Any],
        job_name: str,
        output_dir: str,
    ) -> dict[str, Any]:
        """Submit the job payload to the analysis platform with retries."""
        if relay_mode_enabled():
            payload = await current_relay_client().post_json(
                "analysis/tasks",
                json_body=job_data,
                message="Failed to submit task",
            )
            logger.info(
                "Submit (relay): job_name=%s task_id=%s output_dir=%s "
                "task_status=RUNNING",
                job_name,
                payload["id"],
                output_dir,
            )
            return {
                "task_id": payload["id"],
                "task_status": "PENDING",
                "job_name": job_name,
                "output_dir": output_dir,
            }
        timeout = self.analyst_config.TIMEOUT
        max_retries = self.analyst_config.MAX_RETRIES
        client_timeout = Timeout(timeout, connect=timeout)
        analysis_url = self.analyst_config.ANALYSIS_URL

        async with get_async_client(timeout=client_timeout) as client:
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
                logger.info(
                    "Submit: job_name=%s task_id=%s output_dir=%s "
                    "task_status=RUNNING",
                    job_name,
                    payload["id"],
                    output_dir,
                )
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
