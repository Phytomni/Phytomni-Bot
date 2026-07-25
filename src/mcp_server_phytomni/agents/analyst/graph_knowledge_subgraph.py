# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Prep + post knowledge-subgraph nodes for AnalystAgent.

Exports :class:`AnalystKnowledgeSubgraphMixin`, the prep + post halves
surrounding the per-instance compiled KnowledgeAgent app that
replaces the inline ``multi_retrieve`` call. The legacy node
stays in ``graph.py``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ...common.docs import format_retrieved_doc_context
from ...graphs.analyst_to_knowledge_adapters import (
    build_analyst_knowledge_input,
    extract_analyst_knowledge_response,
)
from ...storage.downloads import download_upload_context

if TYPE_CHECKING:
    from .agent import AnalystAgentsState
else:
    AnalystAgentsState = dict[str, Any]

logger = logging.getLogger(__name__)


class AnalystKnowledgeSubgraphMixin:
    """Prep + post halves for the analyst method_retrieve site."""

    async def method_retrieve_prep_node(
        self: Any, state: AnalystAgentsState
    ) -> dict[str, Any]:
        """Stage the knowledge input + post-knowledge sentinel.

        Mirrors the input-building half of the legacy
        ``method_retrieve_node`` but only emits the ``knowledge_payload``
        plus the ``pending_post_knowledge`` sentinel that routes the
        knowledge output back to ``method_retrieve_post_node``. No
        retrieve call happens here; the shared knowledge node runs
        between this prep and the post, then ``ainvoke`` of the
        compiled KA subgraph writes its return into
        ``knowledge_response`` for the post node to consume.

        Args:
            state: The current workflow state. Reads ``goal_description``
                (the retrieve query) so the KnowledgeInput payload mirrors
                the legacy ``user_query=state['goal_description']`` argument.

        Returns:
            A state delta with the ``KnowledgeInput`` dict under
            ``knowledge_payload`` and
            ``"method_retrieve_post_node"`` under
            ``pending_post_knowledge``.
        """
        return {
            "knowledge_payload": build_analyst_knowledge_input(
                state["goal_description"],
                self.analyst_config.REPO_ID_DICT,
                state.get("locale"),
            ),
            "pending_post_knowledge": "method_retrieve_post_node",
        }

    async def method_retrieve_post_node(
        self: Any, state: AnalystAgentsState
    ) -> dict[str, Any]:
        """Parse the knowledge response into the ``method_context`` delta.

        Mirrors the post-processing half of the legacy
        ``method_retrieve_node`` but reads the retrieved doc list from
        ``state['knowledge_response']`` instead of awaiting a fresh
        ``multi_retrieve`` call. Still performs the
        ``download_upload_context`` and ``format_retrieved_doc_context``
        steps locally because ``KnowledgeOutput`` exposes only the raw
        ``retrieved_docs`` list (not the formatted context string), and
        the analyst pre-shapes the multi-format upload differently than
        the knowledge subgraph's ``process_files_node`` would.

        Args:
            state: The current workflow state. Reads ``obs_file_list``
                (for the upload-context download) and
                ``knowledge_response`` (the KA subgraph's final state)
                written by the shared knowledge node.

        Returns:
            A state delta with the ``method_context`` dict carrying
            ``upload_context`` and ``retrieve_context`` keys — the
            exact shape the downstream ``plan_node`` reads.
        """
        upload_context, total_length = await download_upload_context(
            state["obs_file_list"],
            self.analyst_config,
            self.sensitive_config,
        )
        docs = extract_analyst_knowledge_response(
            state.get("knowledge_response") or {}
        )
        retrieve_context, _ = format_retrieved_doc_context(
            docs,
            max_tokens=self.analyst_config.MAX_TOKENS,
            initial_length=total_length,
        )
        logger.debug("Retrieve Information: %s", retrieve_context)
        return {
            "method_context": {
                "upload_context": upload_context,
                "retrieve_context": retrieve_context,
            }
        }
