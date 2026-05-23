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
