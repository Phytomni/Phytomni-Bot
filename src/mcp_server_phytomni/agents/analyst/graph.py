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
from collections.abc import Mapping
from contextlib import suppress
from typing import TYPE_CHECKING, Any, cast

import yaml
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from ...auth.iam import get_token
from ...common.http import (
    JsonPostRequest,
    JsonPostRetry,
    request_response_with_retries,
)
from ...common.prompts import get_prompt
from ...common.relay_client import RelayRequestOptions, current_relay_client
from ...config.relay_mode import relay_mode_enabled
from ...runtime.artifact_roles import append_artifact_manifest_contract
from ...runtime.outbound import (
    OutboundPoolName,
    current_outbound_http_client,
)
from ...runtime.result_run_layout import (
    result_child_output_dir,
    result_run_root_from_child,
)
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


class AnalystGraphMixin:
    """Tool-usage retrieval and task submission nodes for AnalystAgent.
    The nodes keep their shared submission context on the consuming agent."""

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
        output_dir = await self._submit_output_dir(state, run_identity)
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
            research_grant_sidecar=state.get("research_grant_sidecar"),
        )

    def _submit_run_identity(self: Any) -> RunIdentity:
        """Return the shared run identity for one submit request."""
        return RunIdentity.create(
            user_id=self.analyst_config.USER_ID,
            scope="analysis_agents_task",
        )

    async def _submit_output_dir(
        self: Any,
        state: Mapping[str, Any],
        run_identity: RunIdentity,
    ) -> str:
        """Return an existing or newly created submit output directory."""
        output_dir = str(state.get("output_dir") or "")
        if state.get("output_dir_is_result_child") is True:
            result_run_root_from_child(output_dir)
            return output_dir
        if not self.analyst_config.CREATE_DIR:
            return output_dir
        fingerprint = state.get("input_fingerprint") or ""
        run_root = output_dir
        if output_dir:
            with suppress(ValueError):
                run_root = result_run_root_from_child(output_dir)
        return result_child_output_dir(
            await ensure_run_output_dir(
                self.analyst_config,
                "analysis_agents_task",
                run_identity,
                run_root,
                fingerprint=fingerprint,
            ),
            0,
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
        task_object_name = "task.yaml"
        task_path = await upload_analyst_agents_content(
            content=self._submit_payload(state, output_dir),
            object_name=task_object_name,
            object_key=task_tmp_key(
                run_identity,
                "analysis_agents_task",
                task_object_name,
            ),
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
            "being a detailed description of the file."
        )
        return append_artifact_manifest_contract(
            f"  ### EXECUTION PLAN\n{plan}\n\n"
            f"### TOOL USAGE\n{state.get('tool_usages', '')}"
        )

    async def _submit_headers(self: Any) -> dict[str, str]:
        """Return authenticated submit headers (none minted in relay mode)."""
        if relay_mode_enabled():
            return {"Content-Type": "application/json"}
        token = await get_token(
            request_timeout=self.analyst_config.TIMEOUT,
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
        **options: Any,
    ) -> dict[str, Any]:
        """Submit the job payload to the analysis platform with retries.

        A Research grant sidecar is an operator-relay concern only.  It is
        wrapped around the established analysis request after the job YAML
        has been built; direct platform requests and ordinary relay requests
        therefore retain their historical request bytes.
        """
        research_grant_sidecar = options.get("research_grant_sidecar")
        if relay_mode_enabled():
            relay_body = _relay_analysis_body(job_data, research_grant_sidecar)
            payload = await current_relay_client().post_json(
                "analysis/tasks",
                relay_body,
                pool=OutboundPoolName.ANALYSIS_CONTROL,
                options=RelayRequestOptions(message="Failed to submit task"),
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
        analysis_url = self.analyst_config.ANALYSIS_URL

        client = current_outbound_http_client(
            OutboundPoolName.ANALYSIS_CONTROL
        )
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


def _relay_analysis_body(
    job_data: dict[str, Any], sidecar: Any
) -> dict[str, Any]:
    """Return the raw Analyst body or a validated private grant envelope."""
    if sidecar is None:
        return job_data
    if not isinstance(sidecar, Mapping):
        raise McpError(
            ErrorData(code=INTERNAL_ERROR, message="invalid research grant")
        )
    schema_version = sidecar.get("schema_version")
    parent_run_id = sidecar.get("parent_run_id")
    execution_fingerprint = sidecar.get("execution_fingerprint")
    objects = sidecar.get("objects")
    if not _valid_sidecar_header(
        schema_version, parent_run_id, execution_fingerprint, objects
    ):
        raise McpError(
            ErrorData(code=INTERNAL_ERROR, message="invalid research grant")
        )
    safe_objects: list[dict[str, str]] = []
    sidecar_objects = cast(tuple[Any, ...] | list[Any], objects)
    for item in sidecar_objects:
        if not isinstance(item, Mapping):
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR, message="invalid research grant"
                )
            )
        values = {
            key: item.get(key)
            for key in (
                "dataset_id",
                "exact_reference",
                "grant_id",
                "snapshot_digest",
            )
        }
        if not all(
            isinstance(value, str) and value for value in values.values()
        ):
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR, message="invalid research grant"
                )
            )
        safe_objects.append({key: cast(str, values[key]) for key in values})
    return {
        "analysis_request": job_data,
        "research_input_grants": {
            "schema_version": 1,
            "parent_run_id": parent_run_id,
            "execution_fingerprint": execution_fingerprint,
            "objects": safe_objects,
        },
    }


def _valid_sidecar_header(
    schema_version: Any,
    parent_run_id: Any,
    execution_fingerprint: Any,
    objects: Any,
) -> bool:
    """Check the envelope fields before inspecting private object values."""
    return (
        schema_version == 1
        and isinstance(parent_run_id, str)
        and bool(parent_run_id)
        and isinstance(execution_fingerprint, str)
        and bool(execution_fingerprint)
        and isinstance(objects, (tuple, list))
        and bool(objects)
    )
