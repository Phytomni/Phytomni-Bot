# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: lihu (lihu0628@qq.com)
#         maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Summary and post-process nodes for the deep research review workflow.

Exports ReviewSummaryMixin, which synthesizes the revised subsections
into a final report, renumbers citations to the public ``[document:N]``
form, and attaches follow-up questions. Plugs into DeepResearchAgent
via multiple inheritance, sharing the ``self.review_config`` /
``self._chat`` surface owned by the assembling class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from ...common.prompts import get_prompt
from ...common.responses import message_content, parse_follow_up_questions
from ...graphs.chat_adapters import build_chat_input, build_chat_kwargs_for
from ...runtime.workflow_mixins import WorkflowMixinBase
from .helpers import _renumber_citations

if TYPE_CHECKING:
    from .agent import DeepResearchState
else:
    DeepResearchState = Dict[str, Any]


class ReviewSummaryMixin(WorkflowMixinBase):
    """Summary synthesis and post-process nodes for deep research."""

    async def summary_node(self: Any, state: DeepResearchState):
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

    async def summary_prep_node(
        self: Any, state: DeepResearchState
    ) -> Dict[str, Any]:
        """Build the chat payload for the summary synthesis call.

        Mirrors the prompt-building half of ``summary_node``. The
        actual chat dispatch runs in the shared chat node;
        ``summary_post_node`` extracts ``summary_content`` from the
        response.

        Args:
            state: Current workflow state with revised subsection payloads.

        Returns:
            State delta with the ``ChatInput`` payload under
            ``chat_payload`` and ``"summary_post_node"`` under
            ``pending_post``.
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

        chat_kwargs = build_chat_kwargs_for(
            self.review_config,
            self.sensitive_config,
            with_follow_up=False,
        )
        chat_payload = build_chat_input(
            get_prompt(
                self.review_config.PROMPT_FILE,
                "user/deep_research_summary",
                summary_params,
            ),
            chat_kwargs,
        )
        return {
            "chat_payload": chat_payload,
            "pending_post": "summary_post_node",
        }

    async def summary_post_node(
        self: Any, state: DeepResearchState
    ) -> Dict[str, Any]:
        """Parse the summary chat response into ``summary_content``.

        Mirrors the response-parsing half of ``summary_node`` but reads
        the chat response from ``state['chat_response']`` instead of
        awaiting a fresh ``_chat`` call.

        Args:
            state: Current workflow state. Reads ``chat_response``
                written by the shared chat node.

        Returns:
            State delta with the combined review text under
            ``summary_content``.
        """
        phyto_response = state.get("chat_response") or {}
        content = ""
        if (
            phyto_response
            and phyto_response.get("choices")
            and len(phyto_response["choices"]) > 0
            and phyto_response["choices"][0].get("message")
            and phyto_response["choices"][0]["message"].get("content")
        ):
            content = phyto_response["choices"][0]["message"]["content"]
        return {
            "summary_content": (content or "No summary generated").replace(
                "`", ""
            )
        }

    async def follow_up_prep_node(
        self: Any, state: DeepResearchState
    ) -> Dict[str, Any]:
        """Renumber citations and build the follow-up chat payload.

        Mirrors the pre-chat half of ``post_process_node``: runs
        ``_renumber_citations`` synchronously to obtain the formatted
        text and ordered document list, updates ``summary_content``
        with the renumbered text, and emits a ``ChatInput`` payload for
        the follow-up questions call. ``follow_up_post_node`` assembles
        ``final_response`` from the same renumbered text plus the LLM's
        follow-up list.

        Args:
            state: Current workflow state with ``summary_content``,
                ``all_raw_doc_list``, ``add_doc_list``, and
                ``original_user_query``.

        Returns:
            State delta with ``summary_content`` updated to the
            renumbered text, a ``ChatInput`` payload under
            ``chat_payload``, and ``"follow_up_post_node"`` under
            ``pending_post``.
        """
        formatted_text, _ = _renumber_citations(
            state["summary_content"],
            [*state["all_raw_doc_list"], *state["add_doc_list"]],
        )
        chat_kwargs = build_chat_kwargs_for(
            self.review_config,
            self.sensitive_config,
            with_follow_up=True,
        )
        chat_payload = build_chat_input(
            get_prompt(
                self.review_config.PROMPT_FILE,
                "system/follow_up_questions",
                {
                    "user_query": state["original_user_query"],
                    "system_response": formatted_text,
                },
            ),
            chat_kwargs,
        )
        return {
            "summary_content": formatted_text,
            "chat_payload": chat_payload,
            "pending_post": "follow_up_post_node",
        }

    async def follow_up_post_node(
        self: Any, state: DeepResearchState
    ) -> Dict[str, Any]:
        """Assemble ``final_response`` from the follow-up chat response.

        Mirrors the response-handling half of ``post_process_node`` but
        reads the follow-up answer from ``state['chat_response']``
        instead of awaiting a fresh ``_chat`` call. Re-runs
        ``_renumber_citations`` on ``state['summary_content']`` (which
        ``follow_up_prep_node`` already updated to the renumbered text)
        to recover the ordered document list for ``final_response``.

        Args:
            state: Current workflow state. Reads ``chat_response``
                (follow-up questions), ``summary_content`` (renumbered
                formatted text), ``all_raw_doc_list``, ``add_doc_list``,
                and ``original_user_query``.

        Returns:
            State delta with ``final_response`` carrying the chat-
            completions-style envelope.
        """
        phyto_response = state.get("chat_response") or {}
        follow_up_content = ""
        if (
            phyto_response
            and phyto_response.get("choices")
            and len(phyto_response["choices"]) > 0
            and phyto_response["choices"][0].get("message")
            and phyto_response["choices"][0]["message"].get("content")
        ):
            follow_up_content = phyto_response["choices"][0]["message"][
                "content"
            ]
        follow_up_list = parse_follow_up_questions(follow_up_content)
        # summary_content was updated to the renumbered text by prep node;
        # re-run _renumber_citations to recover the ordered_doc_list.
        formatted_text, ordered_doc_list = _renumber_citations(
            state["summary_content"],
            [*state["all_raw_doc_list"], *state["add_doc_list"]],
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

    async def post_process_node(self: Any, state: DeepResearchState):
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
