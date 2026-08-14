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

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langgraph.types import Send

from ...common.prompts import get_prompt
from ...common.responses import message_content, parse_json_object_fragment
from ...graphs.chat_adapters import build_chat_input, build_chat_kwargs_for
from ...graphs.review_to_knowledge_adapters import (
    build_review_knowledge_input,
    extract_review_knowledge_response,
)
from ...mcp.progress_events import emit_progress
from ...storage.downloads import download_upload_context
from ..knowledge.retrieval_result import (
    RetrievalProtocolError,
    retrieval_unavailable_error,
)
from .helpers import _format_doc_fragment

if TYPE_CHECKING:
    from .agent import DeepResearchState
else:
    DeepResearchState = dict[str, Any]

logger = logging.getLogger(__name__)

# Ordinary retrieval failures stay in a private index accumulator. They are
# not valid empty evidence and must not enter the public failures channel.
_RETRIEVE_WORKER_CAUGHT: tuple[type[Exception], ...] = (Exception,)


@dataclass
class RetrievalAccumulator:
    """Mutable counters for bounded document retrieval.

    Attributes:
        raw_docs: Accepted raw documents with internal doc ids.
        current_length: Current prompt-context length.
        file_id: Next sequential base document id.
    """

    raw_docs: list[dict[str, Any]]
    current_length: int
    file_id: int = 0


class ReviewPlanningMixin:
    """Planning and retrieval nodes for the deep research workflow.
    Planning and retrieval stages share the consuming agent's configuration."""

    async def plan_query_prep_node(
        self: Any, state: DeepResearchState
    ) -> dict[str, Any]:
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
            with_follow_up=False,
            locale=state.get("locale"),
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
    ) -> dict[str, Any]:
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
        content = message_content(state.get("chat_response") or "")
        dimensions_json = parse_json_object_fragment(content)
        dimensions = dimensions_json.get("Research_dimensions", [])
        if not isinstance(dimensions, list) or not dimensions:
            raise ValueError("Invalid research dimensions from phyto_chat")
        return {
            "research_dimensions": [
                str(dimension) for dimension in dimensions[:4]
            ],
        }

    async def retrieve_prepare_tasks_node(
        self: Any, state: DeepResearchState
    ) -> dict[str, Any]:
        """Prepare for Send fan-out over research dimensions.

        Acts as a graph 'split' node — returns an empty delta. The
        actual per-dimension Send payloads are constructed by
        ``route_retrieve_tasks`` (see ``add_conditional_edges`` in
        ``_wire_chat_subgraph``).
        """
        del state
        return {}

    def make_retrieve_worker_node(self: Any, knowledge_app: Any) -> Any:
        """Return a Send-invoked per-dimension worker.

        Closes over ``knowledge_app`` so LangGraph's
        ``find_subgraph_pregel`` walker can discover the compiled
        KnowledgeAgent through the closure free-variable and expand
        the subgraph in the xray render. Xray prefixes the inlined
        subgraph's child node keys with the parent ``add_node``
        name, i.e. ``retrieve_worker_node:<child>``.

        On success writes a single ``(task_index, docs)`` tuple into
        ``retrieve_indexed_results`` via ``operator.add``. On ordinary
        failure writes only the task index into the private failed-index
        accumulator. Cancellation-class exceptions propagate unchanged.

        Args:
            knowledge_app: Compiled KnowledgeAgent subgraph for this
                consumer instance.  Must be a ``CompiledStateGraph`` so
                xray expansion discovers the subgraph.

        Returns:
            Async callable suitable for ``StateGraph.add_node``.
        """

        async def _retrieve_worker(state: DeepResearchState) -> dict[str, Any]:
            task_index = state["task_index"]
            try:
                knowledge_output = await knowledge_app.ainvoke(
                    state["knowledge_payload"]
                )
                docs = extract_review_knowledge_response(knowledge_output)
                return {
                    "retrieve_indexed_results": [(task_index, docs)],
                }
            except _RETRIEVE_WORKER_CAUGHT as exc:
                del exc
                logger.warning(
                    "review retrieve worker failed: "
                    "task_index=%s kind=retrieval",
                    task_index,
                )
                return {"retrieve_failed_indices": [task_index]}

        return _retrieve_worker

    async def retrieve_reduce_node(
        self: Any, state: DeepResearchState
    ) -> dict[str, Any]:
        """Reduce per-dimension docs into accumulator + dimension_params.

        Mirrors the POST-gather logic in the legacy retrieve_node.
        Reads state["retrieve_indexed_results"] (accumulated
        (task_index, docs) tuples), state["research_dimensions"],
        and state["total_length"]; writes all_raw_doc_list,
        dimension_params, and total_length.
        """
        emit_progress(
            "retrieving",
            len(state.get("retrieve_indexed_results", [])),
            total=len(state.get("research_dimensions", [])),
            detail="reducing retrieved dimensions",
        )
        dimensions = state["research_dimensions"]
        expected_indices = set(range(len(dimensions)))
        indexed_by_index: dict[int, list[dict[str, Any]]] = {}
        for entry in state["retrieve_indexed_results"]:
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise RetrievalProtocolError(
                    "Review retrieval index contract violated"
                )
            if (
                not isinstance(entry[0], int)
                or entry[0] not in expected_indices
                or entry[0] in indexed_by_index
                or not isinstance(entry[1], list)
            ):
                raise RetrievalProtocolError(
                    "Review retrieval index contract violated"
                )
            indexed_by_index[entry[0]] = entry[1]

        failed_set: set[int] = set()
        for index in state["retrieve_failed_indices"]:
            if (
                not isinstance(index, int)
                or index not in expected_indices
                or index in failed_set
                or index in indexed_by_index
            ):
                raise RetrievalProtocolError(
                    "Review retrieval index contract violated"
                )
            failed_set.add(index)

        if (set(indexed_by_index) | failed_set) != expected_indices:
            raise RetrievalProtocolError(
                "Review retrieval index contract violated"
            )

        reliable_doc_count = sum(
            len(docs) for docs in indexed_by_index.values()
        )
        if failed_set and reliable_doc_count == 0:
            raise retrieval_unavailable_error()

        accumulator = RetrievalAccumulator(
            raw_docs=[],
            current_length=state["total_length"],
        )
        dimension_params = []
        dimension_length = (
            self.review_config.MAX_TOKENS - state["total_length"]
        ) / max(1, len(dimensions))

        for index, dimension in enumerate(dimensions):
            result = indexed_by_index.get(index, [])
            fragments = self._dimension_fragments(
                result,
                accumulator,
                state["total_length"] + dimension_length * (index + 1),
            )
            dimension_params.append(
                {
                    "subtopic": dimension,
                    "knowledge": "\n\n".join(fragments),
                }
            )

        return {
            "all_raw_doc_list": accumulator.raw_docs,
            "dimension_params": dimension_params,
            "total_length": accumulator.current_length,
        }

    def route_retrieve_tasks(
        self: Any, state: DeepResearchState
    ) -> list[Send]:
        """Build N Send payloads, one per research dimension."""
        dimensions = state["research_dimensions"]
        repo_id_dict = self.review_config.REPO_ID_DICT
        return [
            Send(
                "retrieve_worker_node",
                {
                    "task_index": i,
                    "dimension": dim,
                    "knowledge_payload": build_review_knowledge_input(
                        dimension=dim,
                        repo_id_dict=repo_id_dict,
                        locale=state.get("locale"),
                    ),
                },
            )
            for i, dim in enumerate(dimensions)
        ]

    def _dimension_fragments(
        self: Any,
        dimension_result: Any,
        accumulator: RetrievalAccumulator,
        length_limit: float,
    ) -> list[str]:
        """Format bounded fragments for one research dimension."""
        fragments: list[str] = []
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
