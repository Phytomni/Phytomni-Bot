# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: lihu (lihu0628@qq.com)
#         maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Planning and retrieval nodes for the deep research review workflow.

Exports ReviewPlanningMixin, which decomposes the user query into research
dimensions and retrieves bounded documents for each dimension. The mixin
plugs into DeepResearchAgent via multiple inheritance and shares the
``self.review_config`` / ``self.sensitive_config`` / ``self.ka`` /
``self._chat`` surface owned by the assembling class.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List

from ...common.prompts import get_prompt
from ...common.responses import message_content
from ...graphs.chat_adapters import build_chat_input, build_chat_kwargs_for
from ...runtime.workflow_mixins import WorkflowMixinBase
from ...storage.downloads import download_upload_context
from .helpers import _extract_json_object, _format_doc_fragment

if TYPE_CHECKING:
    from .agent import DeepResearchState
else:
    DeepResearchState = Dict[str, Any]


@dataclass
class RetrievalAccumulator:
    """Mutable counters for bounded document retrieval.

    Attributes:
        raw_docs: Accepted raw documents with internal doc ids.
        current_length: Current prompt-context length.
        file_id: Next sequential base document id.
    """

    raw_docs: List[Dict[str, Any]]
    current_length: int
    file_id: int = 0


class ReviewPlanningMixin(WorkflowMixinBase):
    """Planning and retrieval nodes for the deep research workflow."""

    async def plan_node(self: Any, state: DeepResearchState):
        """Process uploaded files and decompose the topic into dimensions.

        Args:
            state: Current workflow state containing the original query and
                optional uploaded OBS files.

        Returns:
            State updates containing expanded query text, upload context, token
            length, and planned research dimensions.
        """
        user_query = state["original_user_query"]
        total_length = 0
        upload_context = ""

        if state["obs_file_list"]:
            upload_context, total_length = await download_upload_context(
                state["obs_file_list"],
                self.review_config,
                self.sensitive_config,
            )
            user_query = get_prompt(
                self.review_config.PROMPT_FILE,
                "user/deep_research_query_file",
                {
                    "upload_context": upload_context,
                    "user_query": user_query,
                },
            )
        else:
            user_query = get_prompt(
                self.review_config.PROMPT_FILE,
                "user/deep_research_query",
                {"user_query": user_query},
            )

        query_response = await self._chat(
            user_query,
            {
                "type": "json_schema",
                "json_schema": {
                    "type": "object",
                    "properties": {
                        "Research_dimensions": {
                            "type": "array",
                            "items": {"type": "string"},
                        }
                    },
                    "required": ["Research_dimensions"],
                },
            },
        )
        dimensions_json = _extract_json_object(message_content(query_response))
        dimensions = dimensions_json.get("Research_dimensions", [])
        if not isinstance(dimensions, list) or not dimensions:
            raise ValueError("Invalid research dimensions from phyto_chat")

        return {
            "user_query": user_query,
            "upload_context": upload_context,
            "total_length": total_length,
            "research_dimensions": [
                str(dimension) for dimension in dimensions[:4]
            ],
        }

    async def plan_query_prep_node(
        self: Any, state: DeepResearchState
    ) -> Dict[str, Any]:
        """Build the chat payload for the plan-query call.

        Mirrors the prompt-building half of ``plan_node``, including
        the file-upload context download. The actual chat dispatch runs
        in the shared chat node; ``plan_query_post_node`` parses the
        research dimensions from the response.

        Args:
            state: Current workflow state containing the original query and
                optional uploaded OBS files.

        Returns:
            State delta with the upload context / length fields already
            committed, a ``ChatInput`` payload under ``chat_payload``,
            and the ``pending_post`` sentinel for the after-chat router.
        """
        user_query = state["original_user_query"]
        total_length = 0
        upload_context = ""

        if state["obs_file_list"]:
            upload_context, total_length = await download_upload_context(
                state["obs_file_list"],
                self.review_config,
                self.sensitive_config,
            )
            user_query = get_prompt(
                self.review_config.PROMPT_FILE,
                "user/deep_research_query_file",
                {
                    "upload_context": upload_context,
                    "user_query": user_query,
                },
            )
        else:
            user_query = get_prompt(
                self.review_config.PROMPT_FILE,
                "user/deep_research_query",
                {"user_query": user_query},
            )

        chat_kwargs = build_chat_kwargs_for(
            self.review_config,
            self.sensitive_config,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "type": "object",
                    "properties": {
                        "Research_dimensions": {
                            "type": "array",
                            "items": {"type": "string"},
                        }
                    },
                    "required": ["Research_dimensions"],
                },
            },
        )
        chat_payload = build_chat_input(user_query, chat_kwargs)
        return {
            "user_query": user_query,
            "upload_context": upload_context,
            "total_length": total_length,
            "chat_payload": chat_payload,
            "pending_post": "plan_query_post_node",
        }

    async def plan_query_post_node(
        self: Any, state: DeepResearchState
    ) -> Dict[str, Any]:
        """Parse the plan-query chat response into the legacy delta.

        Mirrors the response-parsing half of ``plan_node`` but reads
        the chat response from ``state['chat_response']`` instead of
        awaiting a fresh ``_chat`` call. Raises ``ValueError`` when the
        LLM returns no usable dimensions, matching ``plan_node``'s contract.

        Args:
            state: Current workflow state. Reads ``chat_response``
                written by the shared chat node.

        Returns:
            State delta with ``research_dimensions``.

        Raises:
            ValueError: If the LLM returns no valid dimensions.
        """
        phyto_response = state.get("chat_response") or {}
        content = "{}"
        if (
            phyto_response
            and phyto_response.get("choices")
            and len(phyto_response["choices"]) > 0
            and phyto_response["choices"][0].get("message")
            and phyto_response["choices"][0]["message"].get("content")
        ):
            content = phyto_response["choices"][0]["message"]["content"]
        dimensions_json = _extract_json_object(content)
        dimensions = dimensions_json.get("Research_dimensions", [])
        if not isinstance(dimensions, list) or not dimensions:
            raise ValueError("Invalid research dimensions from phyto_chat")
        return {
            "research_dimensions": [
                str(dimension) for dimension in dimensions[:4]
            ],
        }

    async def retrieve_node(self: Any, state: DeepResearchState):
        """Retrieve documents for each research dimension.

        Args:
            state: Current workflow state with planned research dimensions and
                upload context length.

        Returns:
            State updates containing raw documents, per-dimension prompt
            parameters, and accumulated context length.
        """
        dimensions = state["research_dimensions"]
        results = await asyncio.gather(
            *[
                self.ka.arun(
                    user_query=dimension,
                    is_generate=False,
                    is_follow_up=False,
                )
                for dimension in dimensions
            ],
            return_exceptions=True,
        )

        accumulator = RetrievalAccumulator(
            raw_docs=[],
            current_length=state["total_length"],
        )
        dimension_params = []
        dimension_length = (
            self.review_config.MAX_TOKENS - state["total_length"]
        ) / max(1, len(dimensions))

        for index, result in enumerate(results):
            fragments = self._dimension_fragments(
                result,
                accumulator,
                state["total_length"] + dimension_length * (index + 1),
            )
            dimension_params.append(
                {
                    "subtopic": dimensions[index],
                    "knowledge": "\n\n".join(fragments),
                }
            )

        return {
            "all_raw_doc_list": accumulator.raw_docs,
            "dimension_params": dimension_params,
            "total_length": accumulator.current_length,
        }

    def _dimension_fragments(
        self: Any,
        dimension_result: Any,
        accumulator: RetrievalAccumulator,
        length_limit: float,
    ) -> List[str]:
        """Format bounded fragments for one research dimension."""
        fragments: List[str] = []
        if isinstance(dimension_result, BaseException):
            return fragments

        for doc in dimension_result:
            current_doc_id = f"document {accumulator.file_id + 1:03d}"
            doc_copy = doc.copy()
            doc_copy["doc_id"] = current_doc_id
            fragment = _format_doc_fragment(doc_copy, current_doc_id)
            if accumulator.current_length + len(fragment) <= length_limit:
                fragments.append(fragment)
                accumulator.raw_docs.append(doc_copy)
                accumulator.current_length += len(fragment)
                accumulator.file_id += 1
            else:
                break
        return fragments
