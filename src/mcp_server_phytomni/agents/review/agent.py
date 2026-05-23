# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: lihu (lihu0628@qq.com)
#         maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""LangGraph-based deep research and literature review generation.

Exports DeepResearchAgent, its workflow state, citation/retrieval formatting
models, and the review_agent_function compatibility wrapper used for
literature review generation with optional uploaded-file context.
"""

import asyncio
from typing import Any, Dict, List, Optional, TypedDict, Union

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from ...common.prompts import get_prompt
from ...common.responses import (
    message_content,
    parse_follow_up_questions,
)
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
from .helpers import _renumber_citations
from .planning import ReviewPlanningMixin
from .report import ReviewReportMixin

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


class DeepResearchState(TypedDict):
    """State schema for the deep research LangGraph workflow.

    Attributes:
        original_user_query: Initial user research question.
        user_query: Prompt-expanded research question after file handling.
        obs_file_list: Uploaded OBS files used as input context.
        upload_context: Text extracted from uploaded files.
        total_length: Current accumulated prompt context length.
        research_dimensions: Planned research dimensions.
        all_raw_doc_list: Retrieved documents with base citation ids.
        dimension_params: Prompt params for per-dimension drafting.
        draft_contents: Draft subsection text for each dimension.
        review_contents: Critique JSON/text for each draft.
        revised_reports: Revised subsection payloads.
        add_doc_list: Supplementary documents added during revision.
        summary_content: Combined review text before post-processing.
        final_response: Chat-completions-style final response payload.
    """

    original_user_query: str
    user_query: str
    obs_file_list: List[str]
    upload_context: str
    total_length: int
    research_dimensions: List[str]
    all_raw_doc_list: List[Dict[str, Any]]
    dimension_params: List[Dict[str, str]]
    draft_contents: List[str]
    review_contents: List[str]
    revised_reports: List[Dict[str, str]]
    add_doc_list: List[Dict[str, Any]]
    summary_content: str
    final_response: Dict[str, Any]


class DeepResearchAgent(ReviewPlanningMixin, ReviewReportMixin):
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
        workflow = StateGraph(DeepResearchState)
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
        return workflow.compile(checkpointer=self.checkpointer)

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

    async def summary_node(self, state: DeepResearchState):
        """Synthesize the revised subsections into a final report.

        Args:
            state: Current workflow state with revised subsection payloads.

        Returns:
            State update containing the combined review text.
        """
        summary_params: Dict[str, str] = {
            "user_query": state["original_user_query"]
        }
        for idx in range(4):
            report = (
                state["revised_reports"][idx]
                if idx < len(state["revised_reports"])
                else {}
            )
            title = (
                state["research_dimensions"][idx]
                if idx < len(state["research_dimensions"])
                else ""
            )
            summary_params[f"subsection_{idx + 1}_title"] = title
            summary_params[f"subsection_{idx + 1}_content"] = str(
                report.get("revised_report", "")
            )

        summary_response = await self._chat(
            get_prompt(
                self.review_config.PROMPT_FILE,
                "user/deep_research_summary",
                summary_params,
            )
        )
        content = message_content(summary_response).replace("`", "")
        return {"summary_content": content or "No summary generated"}

    async def post_process_node(self, state: DeepResearchState):
        """Renumber citations and attach references and follow-ups.

        Args:
            state: Current workflow state with summary text and document lists.

        Returns:
            State update containing the final response payload with formatted
            citations, ordered references, total count, and follow-ups.
        """
        formatted_text, ordered_doc_list = _renumber_citations(
            state["summary_content"],
            [*state["all_raw_doc_list"], *state["add_doc_list"]],
        )
        follow_up_response = await self._chat(
            get_prompt(
                self.review_config.PROMPT_FILE,
                "system/follow_up_questions",
                {
                    "user_query": state["original_user_query"],
                    "system_response": formatted_text,
                },
            )
        )
        follow_up_list = parse_follow_up_questions(
            message_content(follow_up_response)
        )
        final_response = {
            "choices": [
                {
                    "message": {
                        "content": formatted_text,
                        "doc_list": ordered_doc_list,
                        "total": 10000,
                        "follow_up_questions": follow_up_list,
                    }
                }
            ]
        }
        return {"final_response": final_response}

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
        }
        final_state = await ainvoke_graph(
            self.app, initial_state, thread_id=thread_id
        )
        return final_state["final_response"]


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
