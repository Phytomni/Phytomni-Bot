# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""AnalystAgent LangGraph orchestrator and its state schema.

Lives in its own module so ``submission.py`` and ``task_ops.py`` can
import the class without entangling with ``agent.py``'s wrapper layer.
This eliminates the AnalystAgent <-> submission import cycle that the
prior late-import workaround in ``agent.py`` papered over.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from ...config.overrides import copy_config_with_overrides
from ...config.settings import SensitiveConfig, get_sensitive_config
from ...runtime.langgraph_runner import (
    ainvoke_graph,
    capture_workflow_boundary,
    ensure_checkpointer,
)
from ..shared.chat_subgraph import (
    make_chat_after_router,
    make_chat_node_wrapper,
)
from ..shared.intermediate_state import merge_intermediate_state
from .defaults import ANALYST_CONFIG, ANALYST_CONFIG_FIELD_MAP
from .graph import AnalystGraphMixin
from .graph_chat_subgraph import AnalystChatSubgraphMixin
from .state import (
    AnalystAgentsState,
    AnalystInput,
    AnalystOutput,
    AnalystState,
)
from .task_ops import task_status


class AnalystAgent(AnalystChatSubgraphMixin, AnalystGraphMixin):
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
                          Defaults to the cached ``get_sensitive_config()``
                          instance when ``None``.

    Attributes:
        checkpointer: The checkpointer for state persistence.
        analyst_config: The analyst configuration instance.
        sensitive_config: The sensitive configuration instance.
        app: The compiled LangGraph application.
    """

    def __init__(
        self,
        checkpointer: Optional[MemorySaver] = None,
        analyst_config=ANALYST_CONFIG,
        sensitive_config: Optional[SensitiveConfig] = None,
    ):
        """Initialize the AnalystAgent and build the graph."""
        self.checkpointer = ensure_checkpointer(checkpointer)
        self.analyst_config = analyst_config
        self.sensitive_config = sensitive_config or get_sensitive_config()
        self.app = self._build_graph()

    def _build_graph(self):
        """Build and compile the LangGraph StateGraph workflow.

        Two shapes are returned based on ``USE_CHAT_SUBGRAPH``:
        flag-off keeps the legacy nine-node form where each chat node
        (``parse_query`` / ``data_select`` / ``plan`` / ``check`` /
        ``tool_extract``) awaits ``phyto_chat`` directly; flag-on
        splits each chat node into a prep + post pair surrounding a
        single shared ``chat`` node registered via
        ``add_node(make_chat_node_wrapper(...))`` with a conditional
        router that reads ``pending_post`` to direct the chat output
        back to the correct post node.

        Wires the analyst pipeline against a three-schema
        ``StateGraph``: ``AnalystState`` for internal node access,
        ``AnalystInput`` as the public contract a parent graph
        supplies, and ``AnalystOutput`` as the surface ``arun`` and
        downstream adapters consume after compile.
        """
        workflow = StateGraph(
            AnalystState,
            input_schema=AnalystInput,
            output_schema=AnalystOutput,
        )
        if self.analyst_config.USE_CHAT_SUBGRAPH:
            self._wire_chat_subgraph(workflow)
        else:
            self._wire_legacy(workflow)
        return workflow.compile(checkpointer=self.checkpointer)

    def _wire_legacy(self, workflow: StateGraph) -> None:
        """Register the legacy nine-node form on ``workflow``.

        Each chat node awaits ``phyto_chat`` inline; no shared chat
        subgraph mount. Preserves the pre-``USE_CHAT_SUBGRAPH``
        wiring untouched.
        """
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
            "parse_query_node",
            self.route_after_extract,
            {
                "data_select_node": "data_select_node",
                "method_retrieve_node": "method_retrieve_node",
                "tool_extract_node": "tool_extract_node",
            },
        )
        workflow.add_conditional_edges(
            "data_select_node",
            self.route_after_data_select,
            {
                "method_retrieve_node": "method_retrieve_node",
                "tool_extract_node": "tool_extract_node",
            },
        )
        workflow.add_edge("method_retrieve_node", "plan_node")
        workflow.add_edge("plan_node", "check_node")
        workflow.add_conditional_edges(
            "check_node",
            self.route_after_check,
            {
                "plan_node": "plan_node",
                "tool_extract_node": "tool_extract_node",
            },
        )
        workflow.add_edge("tool_extract_node", "tool_retrieve_node")
        workflow.add_edge("tool_retrieve_node", "submit_node")
        workflow.add_conditional_edges(
            "submit_node",
            self.route_after_submit,
            {
                "pooling_node": "pooling_node",
                "__end__": END,
            },
        )
        workflow.add_conditional_edges(
            "pooling_node",
            self.route_after_pooling,
            {
                "__end__": END,
                "pooling_node": "pooling_node",
            },
        )

    def _wire_chat_subgraph(self, workflow: StateGraph) -> None:
        """Register the prep + post + shared chat form on ``workflow``.

        Replaces each of the five chat nodes with a prep + post pair
        surrounding a single shared ``chat`` node. The chat node is
        registered via ``make_chat_node_wrapper`` so LangGraph's
        ``xray`` rendering can inline the compiled chat subgraph in
        the analyst render. ``make_chat_after_router`` reads the
        ``pending_post`` sentinel each prep node stages to branch
        back to the correct post node after the chat call. A
        per-prep conditional edge short-circuits to the post node
        directly when the prep set ``chat_payload`` to ``None`` for
        the early-return cases (``parse_query`` when
        ``goal_description`` is already set; ``check`` when a preset
        plan with no method context auto-approves).
        """
        workflow.add_node("parse_query_prep_node", self.parse_query_prep_node)
        workflow.add_node("parse_query_post_node", self.parse_query_post_node)
        workflow.add_node("data_select_prep_node", self.data_select_prep_node)
        workflow.add_node("data_select_post_node", self.data_select_post_node)
        workflow.add_node("plan_prep_node", self.plan_prep_node)
        workflow.add_node("plan_post_node", self.plan_post_node)
        workflow.add_node("check_prep_node", self.check_prep_node)
        workflow.add_node("check_post_node", self.check_post_node)
        workflow.add_node(
            "tool_extract_prep_node", self.tool_extract_prep_node
        )
        workflow.add_node(
            "tool_extract_post_node", self.tool_extract_post_node
        )
        workflow.add_node("method_retrieve_node", self.method_retrieve_node)
        workflow.add_node("tool_retrieve_node", self.tool_retrieve_node)
        workflow.add_node("submit_node", self.submit_node)
        workflow.add_node("pooling_node", self.pooling_node)
        workflow.add_node(
            "chat",
            make_chat_node_wrapper(
                build_input_fn=lambda state: state["chat_payload"],
                extract_output_fn=lambda chat_output: (
                    chat_output.get("response") or {}
                ),
                response_key="chat_response",
            ),
        )
        workflow.add_edge(START, "parse_query_prep_node")
        workflow.add_conditional_edges(
            "parse_query_prep_node",
            self._route_prep_to_chat_or_post(
                post_node="parse_query_post_node"
            ),
            {
                "chat": "chat",
                "parse_query_post_node": "parse_query_post_node",
            },
        )
        workflow.add_conditional_edges(
            "chat",
            make_chat_after_router(),
            {
                "parse_query_post_node": "parse_query_post_node",
                "data_select_post_node": "data_select_post_node",
                "plan_post_node": "plan_post_node",
                "check_post_node": "check_post_node",
                "tool_extract_post_node": "tool_extract_post_node",
            },
        )
        workflow.add_conditional_edges(
            "parse_query_post_node",
            self.route_after_extract,
            {
                "data_select_node": "data_select_prep_node",
                "method_retrieve_node": "method_retrieve_node",
                "tool_extract_node": "tool_extract_prep_node",
            },
        )
        workflow.add_edge("data_select_prep_node", "chat")
        workflow.add_conditional_edges(
            "data_select_post_node",
            self.route_after_data_select,
            {
                "method_retrieve_node": "method_retrieve_node",
                "tool_extract_node": "tool_extract_prep_node",
            },
        )
        workflow.add_edge("method_retrieve_node", "plan_prep_node")
        workflow.add_edge("plan_prep_node", "chat")
        workflow.add_edge("plan_post_node", "check_prep_node")
        workflow.add_conditional_edges(
            "check_prep_node",
            self._route_prep_to_chat_or_post(post_node="check_post_node"),
            {
                "chat": "chat",
                "check_post_node": "check_post_node",
            },
        )
        workflow.add_conditional_edges(
            "check_post_node",
            self.route_after_check,
            {
                "plan_node": "plan_prep_node",
                "tool_extract_node": "tool_extract_prep_node",
            },
        )
        workflow.add_edge("tool_extract_prep_node", "chat")
        workflow.add_edge("tool_extract_post_node", "tool_retrieve_node")
        workflow.add_edge("tool_retrieve_node", "submit_node")
        workflow.add_conditional_edges(
            "submit_node",
            self.route_after_submit,
            {
                "pooling_node": "pooling_node",
                "__end__": END,
            },
        )
        workflow.add_conditional_edges(
            "pooling_node",
            self.route_after_pooling,
            {
                "__end__": END,
                "pooling_node": "pooling_node",
            },
        )

    @staticmethod
    def _route_prep_to_chat_or_post(post_node: str):
        """Return a router callable that short-circuits prep early-exit.

        Used by ``parse_query_prep_node`` and ``check_prep_node`` to
        bypass the shared chat call when the prep already committed
        the legacy early-return delta to state (signalled by
        ``chat_payload is None``).
        """

        def _router(state: AnalystAgentsState) -> str:
            if state.get("chat_payload") is not None:
                return "chat"
            return post_node

        return _router

    async def pooling_node(self, state: AnalystAgentsState):
        """Poll task status until completion.

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
        """Route after the parse_query node based on configuration."""
        if state.get("is_auto_select"):
            return "data_select_node"
        if state.get("is_preset_plan"):
            return "tool_extract_node"
        return "method_retrieve_node"

    def route_after_data_select(
        self, state: AnalystAgentsState
    ) -> Literal["method_retrieve_node", "tool_extract_node"]:
        """Route after the data_select node based on plan availability."""
        if state.get("is_preset_plan"):
            return "tool_extract_node"
        return "method_retrieve_node"

    def route_after_plan(
        self, state: AnalystAgentsState
    ) -> Literal["check_node", "tool_extract_node"]:
        """Route after the plan node based on plan availability."""
        if state.get("is_preset_plan"):
            return "tool_extract_node"
        return "check_node"

    def route_after_check(
        self, state: AnalystAgentsState
    ) -> Literal["plan_node", "tool_extract_node"]:
        """Route after the check node based on plan validation."""
        feedback = state.get("plan_feedback")

        if feedback == "APPROVED" or state.get("is_preset_plan"):
            return "tool_extract_node"
        return "plan_node"

    def route_after_submit(
        self, state: AnalystAgentsState
    ) -> Literal["pooling_node", "__end__"]:
        """Route after submit based on polling preference."""
        if state.get("is_polling"):
            return "pooling_node"
        return "__end__"

    def route_after_pooling(
        self, state: AnalystAgentsState
    ) -> Literal["__end__", "pooling_node"]:
        """Route based on task completion status."""
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

        Args:
            query: The user's natural language query for the analysis.
            **kwargs: Optional state-level overrides forwarded into the
                initial graph state (``goal_description``, ``user_id``,
                ``output_dir``, ``compute_resource``, ``obs_file_list``,
                ``preset_plan``, ``thread_id``, ``is_auto_select``,
                ``is_polling``, ``is_preset_plan``) plus per-call config
                overrides bound through ``ANALYST_CONFIG_FIELD_MAP``.

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
            "preset_plan": kwargs.get("preset_plan"),
            "plan": None,
            "plan_feedback": None,
            "plan_retries": 0,
            "extracted_tools": [],
            "tool_usages": "",
            "job_name": None,
            "task_id": None,
            "task_status": None,
            "is_polling": kwargs.get("is_polling", True),
            "is_auto_select": kwargs.get("is_auto_select", True),
            "is_preset_plan": kwargs.get("is_preset_plan", False),
        }

        async def run_graph() -> dict[str, Any]:
            """Invoke the analyst graph and return public result fields."""
            final_state = await ainvoke_graph(
                self.app,
                initial_state,
                thread_id=kwargs.get("thread_id"),
            )
            return merge_intermediate_state(
                final_state,
                surface_keys=(
                    "task_id",
                    "output_dir",
                    "job_name",
                    "compute_resource",
                ),
            )

        def failure_state(exc: Exception) -> dict[str, Any]:
            """Return graph failures as agent state."""
            return {
                **initial_state,
                "task_status": "FAILED_AT_AGENT_LEVEL",
                "error_detail": str(exc),
            }

        return await capture_workflow_boundary(run_graph, failure_state)


__all__ = [
    "AnalystAgent",
    "AnalystAgentsState",
]
