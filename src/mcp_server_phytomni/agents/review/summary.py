# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: lihu (lihu0628@qq.com)
#         maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Summary and follow-up nodes for the deep research review workflow.

Exports ReviewSummaryMixin, which synthesizes the revised subsections
into a final report, renumbers citations to the public ``[document:N]``
form, and attaches follow-up questions through prep + post pairs around
the shared chat node. Plugs into DeepResearchAgent via multiple
inheritance, sharing the ``self.review_config`` /
``self.sensitive_config`` surface owned by the assembling class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...common.prompts import get_prompt
from ...common.responses import message_content, parse_json_list_fragment
from ...graphs.chat_adapters import build_chat_input, build_chat_kwargs_for
from ...mcp.progress_events import emit_progress
from .helpers import _renumber_citations

if TYPE_CHECKING:
    from .agent import DeepResearchState
else:
    DeepResearchState = dict[str, Any]


class ReviewSummaryMixin:
    """Summary synthesis and follow-up nodes for deep research.
    Both stages use the same review-agent chat and citation context."""

    async def summary_prep_node(
        self: Any, state: DeepResearchState
    ) -> dict[str, Any]:
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
        summary_params: dict[str, str] = {
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
    ) -> dict[str, Any]:
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
        emit_progress("generating", 0, detail="composing summary")
        content = message_content(state.get("chat_response") or "")
        return {
            "summary_content": (content or "No summary generated").replace(
                "`", ""
            )
        }

    async def follow_up_prep_node(
        self: Any, state: DeepResearchState
    ) -> dict[str, Any]:
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
            renumbered text, the ordered reference list under
            ``ordered_doc_list``, a ``ChatInput`` payload under
            ``chat_payload``, and ``"follow_up_post_node"`` under
            ``pending_post``.
        """
        formatted_text, ordered_doc_list = _renumber_citations(
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
            "ordered_doc_list": ordered_doc_list,
            "chat_payload": chat_payload,
            "pending_post": "follow_up_post_node",
        }

    async def follow_up_post_node(
        self: Any, state: DeepResearchState
    ) -> dict[str, Any]:
        """Assemble ``final_response`` from the follow-up chat response.

        Mirrors the response-handling half of ``post_process_node`` but
        reads the follow-up answer from ``state['chat_response']``
        instead of awaiting a fresh ``_chat`` call. The renumbered text
        and ordered document list were both produced by
        ``follow_up_prep_node`` and read straight from state; re-running
        ``_renumber_citations`` here would see the already-renumbered
        ``[document:N]`` text (which the citation pattern does not match)
        and recover an empty list.

        Args:
            state: Current workflow state. Reads ``chat_response``
                (follow-up questions), ``summary_content`` (renumbered
                formatted text), and ``ordered_doc_list`` (the ordered
                references staged by the prep node).

        Returns:
            State delta with ``final_response`` carrying the chat-
            completions-style envelope.
        """
        follow_up_content = message_content(state.get("chat_response") or "")
        follow_up_list = parse_json_list_fragment(follow_up_content)
        formatted_text = state["summary_content"]
        ordered_doc_list = state.get("ordered_doc_list") or []
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
