# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: lihu (lihu0628@qq.com)
#         maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""LangGraph-based deep research and literature review generation.

Hosts DeepResearchAgent (graph construction, node methods, arun
entry point) plus the review_agent_function compatibility wrapper.
Public IO schemas live in state.py; planning / report / summary
mixins live in their own modules; pipeline helpers live in
pipeline.py-style siblings.
"""

import asyncio
from typing import Any, Dict, List, Optional, Union

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from ...common.prompts import get_prompt
from ...common.responses import message_content
from ...config.defaults import ReviewConfig
from ...config.overrides import (
    CHAT_COMPLETION_CONFIG_FIELD_MAP,
    OBS_TRANSFER_CONFIG_FIELD_MAP,
    RETRIEVAL_CONFIG_FIELD_MAP,
    RETRY_CONFIG_FIELD_MAP,
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from ...config.settings import SensitiveConfig, get_sensitive_config
from ...runtime.agent_registry import (
    agent_fingerprint_values,
    get_cached_agent,
)
from ...runtime.langgraph_runner import ainvoke_graph, ensure_checkpointer
from ..chat.service import phyto_chat
from ..knowledge.agent import KnowledgeAgent
from ..shared.chat_subgraph import (
    make_chat_after_router,
    make_chat_node_wrapper,
)
from ..shared.intermediate_state import merge_intermediate_state
from .planning import ReviewPlanningMixin
from .report import ReviewReportMixin
from .state import (
    DeepResearchInput,
    DeepResearchOutput,
    DeepResearchState,
)
from .summary import ReviewSummaryMixin

REVIEW_CONFIG = ReviewConfig()

REVIEW_CONFIG_FIELD_MAP = {
    **CHAT_COMPLETION_CONFIG_FIELD_MAP,
    **RETRIEVAL_CONFIG_FIELD_MAP,
    **OBS_TRANSFER_CONFIG_FIELD_MAP,
    **RETRY_CONFIG_FIELD_MAP,
}
REVIEW_SENSITIVE_FIELD_MAP = {
    "base_url": "BASE_URL",
    "model": "MODEL_ID",
}
REVIEW_SECRET_FIELD_MAP = {
    "api_key": "API_KEY",
    "access_key_id": "ACCESS_KEY_ID",
    "secret_access_key": "SECRET_ACCESS_KEY",
}


class DeepResearchAgent(
    ReviewPlanningMixin, ReviewReportMixin, ReviewSummaryMixin
):
    """LangGraph-based deep research agent from the lihu branch logic.

    Attributes:
        checkpointer: LangGraph checkpointer used by the compiled graph.
        review_config: Public config for retrieval, upload, and chat defaults.
        sensitive_config: Sensitive config with model and OBS credentials.
        ka: KnowledgeAgent used for literature retrieval.
        app: Compiled LangGraph application.
    """

    def __init__(
        self,
        checkpointer: Optional[MemorySaver] = None,
        review_config: ReviewConfig = REVIEW_CONFIG,
        sensitive_config: Optional[SensitiveConfig] = None,
        knowledge_agent: Optional[KnowledgeAgent] = None,
    ):
        self.checkpointer = ensure_checkpointer(checkpointer)
        self.review_config = review_config
        self.sensitive_config = sensitive_config or get_sensitive_config()
        self.ka = knowledge_agent or KnowledgeAgent(
            knowledge_config=review_config,
            sensitive_config=self.sensitive_config,
        )
        self.app = self._build_graph()

    def _build_graph(self):
        """Build and compile the LangGraph StateGraph workflow.

        Two shapes based on ``USE_CHAT_SUBGRAPH``:

        Flag-off (default): preserves the legacy seven-node linear
        pipeline (``plan_node`` → ``retrieve_node`` → ``draft_node`` →
        ``review_node`` → ``revise_node`` → ``summary_node`` →
        ``post_process_node`` → END). Each node calls ``self._chat``
        directly via the inherited ``_chat`` helper.

        Flag-on: replaces the three single-shot chat sites
        (``plan_query`` / ``summary`` / ``follow_up``) with prep + post
        pairs surrounding a single shared ``chat`` node registered via
        :func:`~agents.shared.chat_subgraph.make_chat_node_wrapper`.
        The four fan-out sites (``retrieve_node`` / ``draft_node`` /
        ``review_node`` / ``revise_node``) retain their legacy
        ``asyncio.gather`` bodies; F3.C3.3-C3.5 convert them to
        ``Send``-dispatch workers in subsequent steps.

        Returns:
            Compiled LangGraph application bound to
            ``self.checkpointer``.
        """
        workflow = StateGraph(
            state_schema=DeepResearchState,
            input_schema=DeepResearchInput,
            output_schema=DeepResearchOutput,
        )

        if self.review_config.USE_CHAT_SUBGRAPH:
            self._wire_chat_subgraph(workflow)
        else:
            self._wire_legacy(workflow)

        return workflow.compile(checkpointer=self.checkpointer)

    def _wire_legacy(self, workflow: StateGraph) -> None:
        """Register the legacy seven-node linear pipeline on ``workflow``.

        Preserves the flag-off behavior exactly as it existed before
        the ``USE_CHAT_SUBGRAPH`` dual-path split. Each single-shot
        chat site calls ``self._chat`` directly through the inherited
        helper.

        Args:
            workflow: Uncompiled ``StateGraph`` to register nodes and
                edges on.
        """
        workflow.add_node("plan_node", self.plan_node)
        workflow.add_node("retrieve_node", self.retrieve_node)
        workflow.add_node("draft_node", self.draft_node)
        workflow.add_node("review_node", self.review_node)
        workflow.add_node("revise_node", self.revise_node)
        workflow.add_node("summary_node", self.summary_node)
        workflow.add_node("post_process_node", self.post_process_node)

        workflow.add_edge(START, "plan_node")
        workflow.add_edge("plan_node", "retrieve_node")
        workflow.add_edge("retrieve_node", "draft_node")
        workflow.add_edge("draft_node", "review_node")
        workflow.add_edge("review_node", "revise_node")
        workflow.add_edge("revise_node", "summary_node")
        workflow.add_edge("summary_node", "post_process_node")
        workflow.add_edge("post_process_node", END)

    def _wire_chat_subgraph(self, workflow: StateGraph) -> None:
        """Register the prep + post + shared chat form on ``workflow``.

        Replaces the three single-shot chat sites (``plan_query`` /
        ``summary`` / ``follow_up``) with prep + post pairs surrounding
        a single shared ``chat`` node. The chat node is registered via
        :func:`~agents.shared.chat_subgraph.make_chat_node_wrapper` so
        LangGraph's ``xray`` rendering can inline the compiled chat
        subgraph in the review render.
        :func:`~agents.shared.chat_subgraph.make_chat_after_router`
        reads the ``pending_post`` sentinel each prep node stages to
        branch back to the correct post node after the chat call.

        The four fan-out sites (``retrieve_node`` / ``draft_node`` /
        ``review_node`` / ``revise_node``) retain their legacy
        ``asyncio.gather`` bodies and are wired identically to the
        flag-off path. F3.C3.3-C3.5 will convert those sites to
        ``Send``-dispatch workers in subsequent steps.

        Args:
            workflow: Uncompiled ``StateGraph`` to register nodes and
                edges on.
        """
        # === 3 prep + 3 post + 1 shared chat mount ===
        workflow.add_node("plan_query_prep_node", self.plan_query_prep_node)
        workflow.add_node("plan_query_post_node", self.plan_query_post_node)
        workflow.add_node("summary_prep_node", self.summary_prep_node)
        workflow.add_node("summary_post_node", self.summary_post_node)
        workflow.add_node("follow_up_prep_node", self.follow_up_prep_node)
        workflow.add_node("follow_up_post_node", self.follow_up_post_node)
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
        # === Fan-out sites retain legacy gather bodies ===
        workflow.add_node("retrieve_node", self.retrieve_node)
        workflow.add_node("draft_node", self.draft_node)
        workflow.add_node("review_node", self.review_node)
        workflow.add_node("revise_node", self.revise_node)

        # === Wire prep → chat (3 sites) ===
        workflow.add_edge("plan_query_prep_node", "chat")
        workflow.add_edge("summary_prep_node", "chat")
        workflow.add_edge("follow_up_prep_node", "chat")

        # === Shared chat → post (after-router) ===
        workflow.add_conditional_edges(
            "chat",
            make_chat_after_router(),
            {
                "plan_query_post_node": "plan_query_post_node",
                "summary_post_node": "summary_post_node",
                "follow_up_post_node": "follow_up_post_node",
            },
        )

        # === Linear pipeline edges ===
        workflow.add_edge(START, "plan_query_prep_node")
        workflow.add_edge("plan_query_post_node", "retrieve_node")
        workflow.add_edge("retrieve_node", "draft_node")
        workflow.add_edge("draft_node", "review_node")
        workflow.add_edge("review_node", "revise_node")
        workflow.add_edge("revise_node", "summary_prep_node")
        workflow.add_edge("summary_post_node", "follow_up_prep_node")
        workflow.add_edge("follow_up_post_node", END)

    async def _chat(
        self,
        prompt: str,
        response_format_override: Optional[Dict[str, Union[str, Dict]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Call the configured LLM."""
        return await phyto_chat(
            user_query=prompt,
            prompt_file=self.review_config.PROMPT_FILE,
            prompt_path=self.review_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            frequency_penalty=self.review_config.FREQUENCY_PENALTY,
            n=self.review_config.N,
            presence_penalty=self.review_config.PRESENCE_PENALTY,
            reasoning_effort=self.review_config.REASONING_EFFORT,
            response_format=response_format_override
            or self.review_config.RESPONSE_FORMAT,
            stream=self.review_config.STREAM,
            temperature=self.review_config.TEMPERATURE,
            top_p=self.review_config.TOP_P,
            user=self.review_config.USER,
            timeout=self.review_config.TIMEOUT,
            retriable_codes=self.review_config.RETRIABLE_CODES,
            max_retries=self.review_config.MAX_RETRIES,
        )

    async def draft_node(self, state: DeepResearchState):
        """Create one draft subsection per dimension.

        Args:
            state: Current workflow state with per-dimension prompt params.

        Returns:
            State update containing one draft string per dimension.
        """
        draft_tasks = [
            self._chat(
                get_prompt(
                    self.review_config.PROMPT_FILE,
                    "user/deep_research_dimension",
                    param,
                )
            )
            for param in state["dimension_params"]
        ]
        draft_results = await asyncio.gather(
            *draft_tasks, return_exceptions=True
        )
        return {
            "draft_contents": [
                (
                    ""
                    if isinstance(result, BaseException)
                    else message_content(result)
                )
                for result in draft_results
            ]
        }

    async def arun(
        self,
        user_query: str,
        obs_file_list: Optional[List[str]] = None,
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute the DeepResearchAgent workflow.

        Args:
            user_query: Research question to expand into a literature review.
            obs_file_list: Optional OBS files to include as source context.
            thread_id: Optional LangGraph checkpoint thread id.

        Returns:
            Chat-completions-style final response payload with review text,
            ordered references, and follow-up questions.
        """
        initial_state: DeepResearchState = {
            "original_user_query": user_query,
            "user_query": "",
            "obs_file_list": obs_file_list or [],
            "upload_context": "",
            "total_length": 0,
            "research_dimensions": [],
            "all_raw_doc_list": [],
            "dimension_params": [],
            "draft_contents": [],
            "review_contents": [],
            "revised_reports": [],
            "add_doc_list": [],
            "summary_content": "",
            "final_response": {},
            # Inherited from ParallelDispatchState
            "analysis_type": "",
            "task_index": None,
            "task_ids": {},
            "completed_count": 0,
            "error": None,
            "failures": [],
            # Single-shot chat-mount fields (Step 6.2 pattern)
            "chat_payload": None,
            "chat_response": None,
            "pending_post": None,
            # Send-payload transient fields
            "subtopic": None,
            "knowledge": None,
            "dimension": None,
            "review_draft": None,
            "original_draft": None,
            "review_feedback": None,
            "add_query_input": None,
            "knowledge_payload": None,
            # Fan-out indexed_results accumulators
            "retrieve_indexed_results": [],
            "draft_indexed_results": [],
            "review_indexed_results": [],
            "revised_indexed_results": [],
            "add_query_indexed_results": [],
            # Fan-out final ordered outputs
            "revised_contents": [],
            "add_query_contents": [],
        }
        final_state = await ainvoke_graph(
            self.app, initial_state, thread_id=thread_id
        )
        return merge_intermediate_state(final_state)


async def review_agent_function(
    user_query: str,
    obs_file_list: Optional[List[str]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run the LangGraph deep research review workflow.

    Args:
        user_query: Research question to expand into a literature review.
        obs_file_list: Optional OBS files to include as source context.
        **kwargs: Optional chat, retrieval, upload, retry, credential, and
            cache-fingerprint overrides.

    Returns:
        Chat-completions-style final response payload from DeepResearchAgent.
    """
    review_config = copy_config_with_overrides(
        REVIEW_CONFIG,
        kwargs,
        REVIEW_CONFIG_FIELD_MAP,
    )
    sensitive_config = copy_sensitive_config_with_overrides(
        get_sensitive_config(),
        kwargs,
        field_map=REVIEW_SENSITIVE_FIELD_MAP,
        secret_field_map=REVIEW_SECRET_FIELD_MAP,
    )
    agent = get_cached_agent(
        "DeepResearchAgent",
        lambda: DeepResearchAgent(
            review_config=review_config,
            sensitive_config=sensitive_config,
            knowledge_agent=KnowledgeAgent(
                knowledge_config=review_config,
                sensitive_config=sensitive_config,
            ),
        ),
        agent_fingerprint_values(
            review_config=review_config,
            sensitive_config=sensitive_config,
        ),
    )
    return await agent.arun(
        user_query=user_query,
        obs_file_list=obs_file_list or [],
    )
