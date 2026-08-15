# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Send-dispatched knowledge subgraph wiring for BriefGeneAgent.

Hosts ``BriefGeneKnowledgeSubgraphMixin`` with the dual-wire dispatch
helpers (``_register_retrieve_nodes`` / ``_retrieve_targets``) plus
the four Send-fan-out node methods. Lifting these out of ``core.py``
keeps that module under the pylint ``C0302`` 1000-line ceiling and
mirrors the analyst-side mixin pattern in
``analyst/graph_knowledge_subgraph.py``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Overwrite, Send

from ...graphs.brief_gene_to_knowledge_adapters import (
    build_brief_gene_knowledge_input,
    extract_brief_gene_knowledge_response,
)
from ...mcp.progress_events import emit_progress
from ...runtime.langgraph_runner import ainvoke_graph, ensure_thread_id
from ..knowledge.retrieval_result import (
    RetrievalProtocolError,
    retrieval_unavailable_error,
)
from ..shared.parallel_dispatch import DegradedRecord
from .pipeline import _dedupe, _format_docs

if TYPE_CHECKING:
    from .state import BriefGeneAgentState
else:
    BriefGeneAgentState = dict[str, Any]


logger = logging.getLogger(__name__)

# Broad exception catch tuple used by the Send-dispatched retrieve
# worker factory. Module-level constant lifts the pylint
# ``broad-except`` (W0718) report once at module load instead of
# silencing it at every ``except`` site; downstream readers can grep
# this constant to find the catch scope. Matches the W0718 mitigation
# pattern other consumer agents use (analyst / review).
_BRIEF_GENE_RETRIEVE_WORKER_CAUGHT: tuple[type[BaseException], ...] = (
    Exception,
)
_LITERATURE_RETRIEVAL_DEGRADED = "retrieval_unavailable"


def _validated_retrieve_results(
    state: BriefGeneAgentState,
) -> tuple[dict[int, list[dict[str, Any]]], set[int], set[int], int]:
    """Validate one complete classification for every planned retrieve leg."""
    planned_tasks = state.get("retrieve_tasks")
    indexed = state.get("retrieve_indexed_results", []) or []
    failed_indices = state.get("retrieve_failed_indices", []) or []
    cancelled_indices = state.get("retrieve_cancelled_indices", []) or []
    if not isinstance(planned_tasks, list):
        raise RetrievalProtocolError("Invalid brief_gene retrieve plan")
    if (
        not isinstance(indexed, list)
        or not isinstance(failed_indices, list)
        or not isinstance(cancelled_indices, list)
    ):
        raise RetrievalProtocolError("Invalid brief_gene retrieve results")

    result_by_index: dict[int, list[dict[str, Any]]] = {}
    for entry in indexed:
        if not isinstance(entry, (tuple, list)) or len(entry) != 2:
            raise RetrievalProtocolError("Invalid brief_gene retrieve result")
        task_index, docs = entry
        if (
            isinstance(task_index, bool)
            or not isinstance(task_index, int)
            or not isinstance(docs, list)
            or any(not isinstance(doc, dict) for doc in docs)
            or task_index in result_by_index
        ):
            raise RetrievalProtocolError("Invalid brief_gene retrieve result")
        result_by_index[task_index] = list(docs)

    failed_set: set[int] = set()
    for task_index in failed_indices:
        if (
            isinstance(task_index, bool)
            or not isinstance(task_index, int)
            or task_index in failed_set
        ):
            raise RetrievalProtocolError("Invalid brief_gene retrieve failure")
        failed_set.add(task_index)

    cancelled_set: set[int] = set()
    for task_index in cancelled_indices:
        if (
            isinstance(task_index, bool)
            or not isinstance(task_index, int)
            or task_index in cancelled_set
        ):
            raise RetrievalProtocolError(
                "Invalid brief_gene retrieve cancellation"
            )
        cancelled_set.add(task_index)

    result_indices = set(result_by_index)
    planned = set(range(len(planned_tasks)))
    classified = result_indices | failed_set | cancelled_set
    if (
        not classified <= planned
        or result_indices & failed_set
        or result_indices & cancelled_set
        or failed_set & cancelled_set
        or classified != planned
    ):
        raise RetrievalProtocolError("Incomplete brief_gene retrieve results")
    return result_by_index, failed_set, cancelled_set, len(planned_tasks)


def _validated_annotation_failures(state: BriefGeneAgentState) -> set[int]:
    """Validate the bounded BI table-failure ordinals carried by state."""
    failed_indices = state.get("annotation_failed_indices", []) or []
    if not isinstance(failed_indices, list):
        raise RetrievalProtocolError("Invalid brief_gene annotation failures")
    failed_set: set[int] = set()
    for index in failed_indices:
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index in failed_set
            or index not in range(6)
        ):
            raise RetrievalProtocolError(
                "Invalid brief_gene annotation failures"
            )
        failed_set.add(index)
    return failed_set


class BriefGeneKnowledgeSubgraphMixin:
    """Dual-wire dispatch + Send fan-out for the brief_gene retrieve site."""

    def _retrieve_targets(self: Any) -> tuple[str, str]:
        """Return (incoming, outgoing) node names for the retrieve site.

        The knowledge-subgraph form splits the site into
        ``retrieve_prep_tasks_node`` (incoming) and
        ``retrieve_reduce_node`` (outgoing), with the Send-dispatched
        per-symbol fan-out into a shared ``knowledge`` mount sitting
        between them. Returning the pair from one helper lets
        ``_wire_chat_subgraph`` substitute names without inlining the
        pair at each call site.
        """
        return "retrieve_prep_tasks_node", "retrieve_reduce_node"

    def _register_retrieve_nodes(self: Any, workflow: StateGraph) -> None:
        """Register the retrieve nodes on ``workflow``.

        Registers the Send-dispatch triad
        (``retrieve_prep_tasks_node`` → ``route_retrieve_tasks`` →
        ``retrieve_worker_node`` × N → ``retrieve_reduce_node``).
        Each per-worker ``ainvoke`` closes over the compiled
        knowledge app through the ``make_retrieve_worker_node``
        factory closure so xray expands the shared knowledge subgraph
        beneath every Send leg.

        Args:
            workflow: Uncompiled ``StateGraph`` to register the
                retrieve site nodes and edges on.
        """
        knowledge_app = self._knowledge_app
        if knowledge_app is None:
            raise RuntimeError(
                "unreachable: _knowledge_app must be built in __init__"
            )
        workflow.add_node(
            "retrieve_prep_tasks_node", self.retrieve_prep_tasks_node
        )
        workflow.add_node(
            "retrieve_worker_node",
            self.make_retrieve_worker_node(knowledge_app),
        )
        workflow.add_node("retrieve_reduce_node", self.retrieve_reduce_node)
        workflow.add_conditional_edges(
            "retrieve_prep_tasks_node",
            self._route_retrieve_tasks_async,
            ["retrieve_worker_node"],
        )
        workflow.add_edge("retrieve_worker_node", "retrieve_reduce_node")

    async def retrieve_prep_tasks_node(
        self: Any, state: BriefGeneAgentState
    ) -> dict[str, Any]:
        """Build the per-symbol task list for the Send-dispatched fan-out.

        Mirrors the prompt-building half of the legacy
        ``retrieve_node`` body and the per-symbol fan-out of the
        ``gene_retrieve`` helper. On ``gene_found=True`` the task
        list is the deduplicated ``gene_id_list`` plus a single
        combined-symbols query (the newline-joined symbol string the
        legacy helper appended); on ``gene_found=False`` the task
        list is a single entry carrying the raw ``user_query``. Both
        paths share the per-task ``KnowledgeInput`` shape so the
        worker and reduce nodes treat them uniformly.

        Args:
            state: Current workflow state with the BI gene-resolution
                annotations and the legacy user query.

        Returns:
            State delta with ``retrieve_tasks`` set to the per-task
            payload list the routing function consumes, plus per-run
            retrieval reducers overwritten to empty before fan-out.
        """
        if state["gene_found"]:
            symbols = list(_dedupe(state["gene_id_list"]))
            species = state["species_all_name"]
            combined_symbols = "\n".join(symbols)
            query_terms = list(_dedupe([*symbols, combined_symbols]))
            tasks: list[dict[str, Any]] = [
                {
                    "knowledge_input": build_brief_gene_knowledge_input(
                        f"{species}\n{symbol}",
                        state.get("locale"),
                    ),
                    "task_label": symbol,
                }
                for symbol in query_terms
            ]
        else:
            tasks = [
                {
                    "knowledge_input": build_brief_gene_knowledge_input(
                        state["user_query"],
                        state.get("locale"),
                    ),
                    "task_label": state["user_query"],
                }
            ]
        return {
            "retrieve_tasks": tasks,
            "gene_profile_completed_branches": Overwrite(value=0),
            "retrieve_indexed_results": Overwrite(value=[]),
            "retrieve_failed_indices": Overwrite(value=[]),
            "retrieve_cancelled_indices": Overwrite(value=[]),
            "literature_degraded": Overwrite(value=[]),
        }

    def route_retrieve_tasks(
        self: Any, state: BriefGeneAgentState
    ) -> list[Send]:
        """Dispatch one ``Send`` per ``retrieve_tasks`` entry.

        Each ``Send`` carries the per-task ``knowledge_input`` plus a
        ``task_index`` so the worker can write an indexed
        ``(task_index, doc_list)`` tuple onto the reducer channel and
        the reduce node can sort the tuples back into deterministic
        order before merging.

        Args:
            state: Current workflow state with ``retrieve_tasks``
                already staged by ``retrieve_prep_tasks_node``.

        Returns:
            List of ``Send`` objects, one per task, each routed to
            ``retrieve_worker_node`` with the per-task payload merged
            into the worker's state delta.
        """
        retrieve_tasks = state.get("retrieve_tasks", [])
        return [
            Send(
                "retrieve_worker_node",
                {
                    **state,
                    "task_index": index,
                    "knowledge_input": task["knowledge_input"],
                    "task_label": task["task_label"],
                },
            )
            for index, task in enumerate(retrieve_tasks)
        ]

    async def _route_retrieve_tasks_async(
        self: Any, state: BriefGeneAgentState
    ) -> list[Send]:
        """Adapt the synchronous router for async graph execution.

        LangGraph executes conditional paths in the event-loop runner for
        ``ainvoke``. Keeping the public router synchronous preserves the
        existing inspection/test seam, while this async adapter avoids the
        sync conditional-runner deadlock in the supported runtime.
        """
        return self.route_retrieve_tasks(state)

    def make_retrieve_worker_node(
        self: Any, knowledge_app: CompiledStateGraph
    ) -> Any:
        """Return a Send-invoked per-symbol retrieve worker.

        Closes over ``knowledge_app`` so LangGraph's
        ``find_subgraph_pregel`` walker can discover the compiled
        KnowledgeAgent through the closure free-variable and expand
        the subgraph in the xray render. Xray prefixes the inlined
        subgraph's child node keys with the parent ``add_node``
        name, i.e. ``retrieve_worker_node:<child>``. Using the
        bound-method form (``self.retrieve_worker_node``) would have
        closed only over ``self`` and left xray unable to walk into
        the subgraph; the factory pattern mirrors review's
        ``planning.make_retrieve_worker_node``.

        On success writes a single ``(task_index, docs)`` tuple onto
        ``retrieve_indexed_results`` via the ``operator.add``
        reducer. On ordinary exception it writes only the failed index.
        A cancelled child writes only its private index because LangGraph
        treats a cancelled Send future as completed; the reduce node validates
        the complete classification and re-raises cancellation before progress
        or report work can continue.

        Args:
            knowledge_app: Compiled KnowledgeAgent subgraph for this
                consumer instance. Must be a ``CompiledStateGraph`` so
                xray expansion discovers the subgraph.

        Returns:
            Async callable suitable for ``StateGraph.add_node``.
        """

        async def _retrieve_worker(
            state: BriefGeneAgentState,
        ) -> dict[str, Any]:
            task_index = state.get("task_index", 0)
            knowledge_input = state.get("knowledge_input", {})
            try:
                thread_id = ensure_thread_id()
                cancelled = False
                knowledge_output: Any = None
                try:
                    try:
                        knowledge_output = await ainvoke_graph(
                            knowledge_app,
                            knowledge_input,
                            thread_id=thread_id,
                        )
                    except asyncio.CancelledError:
                        cancelled = True
                finally:
                    try:
                        checkpointer = getattr(
                            knowledge_app, "checkpointer", None
                        )
                        if (
                            checkpointer is not None
                            and checkpointer is not False
                        ):
                            deleter = getattr(
                                checkpointer, "adelete_thread", None
                            )
                            if not callable(deleter):
                                raise RuntimeError(
                                    "knowledge checkpointer cannot "
                                    "delete threads"
                                )
                            await cast(
                                Callable[[str], Awaitable[None]], deleter
                            )(thread_id)
                    except _BRIEF_GENE_RETRIEVE_WORKER_CAUGHT:
                        if not cancelled:
                            raise
                        logger.warning(
                            "brief_gene retrieve cleanup failed after "
                            "cancellation: task_index=%s",
                            task_index,
                        )
                if cancelled:
                    return {"retrieve_cancelled_indices": [task_index]}
                docs, outcome = extract_brief_gene_knowledge_response(
                    knowledge_output
                )
                result: dict[str, Any] = {
                    "retrieve_indexed_results": [(task_index, docs)],
                }
                if outcome == "partial":
                    result["literature_degraded"] = [
                        DegradedRecord(
                            task_label=state.get("task_label", ""),
                            message=_LITERATURE_RETRIEVAL_DEGRADED,
                        )
                    ]
                return result
            except asyncio.CancelledError:
                return {"retrieve_cancelled_indices": [task_index]}
            except _BRIEF_GENE_RETRIEVE_WORKER_CAUGHT as exc:
                del exc
                logger.warning(
                    "brief_gene retrieve worker failed: task_index=%s",
                    task_index,
                )
                return {
                    "retrieve_failed_indices": [task_index],
                    "literature_degraded": [
                        DegradedRecord(
                            task_label=state.get("task_label", ""),
                            message=_LITERATURE_RETRIEVAL_DEGRADED,
                        )
                    ],
                }

        return _retrieve_worker

    async def retrieve_reduce_node(
        self: Any, state: BriefGeneAgentState
    ) -> dict[str, Any]:
        """Reduce per-worker indexed doc lists into the legacy outputs.

        Sorts the reducer-accumulated ``retrieve_indexed_results``
        tuples by ``task_index`` so the downstream merge order is
        deterministic regardless of worker completion order. Merges
        the per-task doc lists, sorts by score descending, applies
        the ``TOP_N`` cap (matching the legacy
        ``_gene_retrieve.make_gene_retrieve`` tail), and projects
        the final ``retrieved_docs`` plus the ``retrieve_context``
        formatted string the downstream ``generate_prep_node`` /
        ``generate_node`` reads.

        Args:
            state: Current workflow state. Reads the reducer-populated
                ``retrieve_indexed_results``.

        Returns:
            State delta with ``retrieved_docs`` and ``retrieve_context``
            written to the legacy field names so the downstream nodes
            stay untouched.
        """
        indexed, failed_indices, cancelled_indices, planned_count = (
            _validated_retrieve_results(state)
        )
        if cancelled_indices:
            raise asyncio.CancelledError
        emit_progress(
            "retrieving",
            len(indexed) + len(failed_indices),
            planned_count,
            detail="reducing literature results",
        )
        annotation_failed_indices = _validated_annotation_failures(state)
        merged_docs: list[dict[str, Any]] = []
        for task_index in sorted(indexed):
            merged_docs.extend(indexed[task_index])
        has_reliable_annotation = bool(state.get("gene_found")) and (
            annotation_failed_indices != set(range(6))
        )
        if (
            failed_indices and not merged_docs and not has_reliable_annotation
        ) or (annotation_failed_indices == set(range(6)) and not merged_docs):
            raise retrieval_unavailable_error()
        sorted_docs = sorted(
            merged_docs,
            key=lambda item: item.get("score", 0),
            reverse=True,
        )
        top_n = self.brief_config.TOP_N
        if top_n is not None and top_n > 0:
            sorted_docs = sorted_docs[:top_n]
        return {
            "retrieved_docs": sorted_docs,
            "retrieve_context": _format_docs(
                sorted_docs, self.brief_config.MAX_TOKENS
            ),
        }
