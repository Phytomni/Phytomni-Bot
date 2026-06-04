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

from typing import TYPE_CHECKING, Any, Dict, List, cast

from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from ...graphs.brief_gene_to_knowledge_adapters import (
    build_brief_gene_knowledge_input,
    extract_brief_gene_knowledge_response,
)
from .pipeline import _dedupe, _format_docs

if TYPE_CHECKING:
    from .state import BriefGeneAgentState
else:
    BriefGeneAgentState = Dict[str, Any]


# Broad exception catch tuple used by the Send-dispatched retrieve
# worker factory. Module-level constant lifts the pylint
# ``broad-except`` (W0718) report once at module load instead of
# silencing it at every ``except`` site; downstream readers can grep
# this constant to find the catch scope. Matches the W0718 mitigation
# pattern other consumer agents use (analyst / review).
_BRIEF_GENE_RETRIEVE_WORKER_CAUGHT: tuple[type[BaseException], ...] = (
    Exception,
)


class BriefGeneKnowledgeSubgraphMixin:
    """Dual-wire dispatch + Send fan-out for the brief_gene retrieve site."""

    def _retrieve_targets(self: Any) -> tuple[str, str]:
        """Return (incoming, outgoing) node names for the retrieve site.

        The legacy single-node form keeps ``retrieve_node`` as both
        the incoming target (callers routing into retrieval) and the
        outgoing source (edges fanning out after retrieval). The
        knowledge-subgraph form splits the site into
        ``retrieve_prep_tasks_node`` (incoming) and
        ``retrieve_reduce_node`` (outgoing), with the Send-dispatched
        per-symbol fan-out into a shared ``knowledge`` mount sitting
        between them. Returning the pair from one helper lets
        ``_wire_legacy`` and ``_wire_chat_subgraph`` substitute names
        without duplicating the conditional.
        """
        if self.brief_config.USE_KNOWLEDGE_SUBGRAPH:
            return "retrieve_prep_tasks_node", "retrieve_reduce_node"
        return "retrieve_node", "retrieve_node"

    def _register_retrieve_nodes(self: Any, workflow: StateGraph) -> None:
        """Register the retrieve node(s) on ``workflow``.

        Under ``USE_KNOWLEDGE_SUBGRAPH=False`` registers the legacy
        single ``retrieve_node``. Under ``=True`` registers the
        Send-dispatch triad
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
        if not self.brief_config.USE_KNOWLEDGE_SUBGRAPH:
            workflow.add_node("retrieve_node", self.retrieve_node)
            return
        knowledge_app = self._knowledge_app
        if knowledge_app is None:
            raise RuntimeError(
                "unreachable: USE_KNOWLEDGE_SUBGRAPH is True but "
                "_knowledge_app was not built in __init__"
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
            self.route_retrieve_tasks,
            ["retrieve_worker_node"],
        )
        workflow.add_edge("retrieve_worker_node", "retrieve_reduce_node")

    async def retrieve_prep_tasks_node(
        self: Any, state: BriefGeneAgentState
    ) -> Dict[str, Any]:
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
            payload list the routing function consumes.
        """
        if state["gene_found"]:
            symbols = list(_dedupe(state["gene_id_list"]))
            species = state["species_all_name"]
            combined_symbols = "\n".join(symbols)
            query_terms = list(_dedupe([*symbols, combined_symbols]))
            tasks: List[Dict[str, Any]] = [
                {
                    "knowledge_input": build_brief_gene_knowledge_input(
                        f"{species}\n{symbol}"
                    ),
                }
                for symbol in query_terms
            ]
        else:
            tasks = [
                {
                    "knowledge_input": build_brief_gene_knowledge_input(
                        state["user_query"]
                    ),
                }
            ]
        return {"retrieve_tasks": tasks}

    def route_retrieve_tasks(
        self: Any, state: BriefGeneAgentState
    ) -> List[Send]:
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
        retrieve_tasks = cast(
            List[Dict[str, Any]], state.get("retrieve_tasks", [])
        )
        return [
            Send(
                "retrieve_worker_node",
                {
                    **state,
                    "task_index": index,
                    "knowledge_input": task["knowledge_input"],
                },
            )
            for index, task in enumerate(retrieve_tasks)
        ]

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
        reducer. On exception writes ``(task_index, [])`` so the
        reduce node's downstream merge sees a stable per-task entry
        and the loud sentinel makes the empty result attributable to
        a specific ``task_index`` rather than silently dropping it.

        Args:
            knowledge_app: Compiled KnowledgeAgent subgraph for this
                consumer instance. Must be a ``CompiledStateGraph`` so
                xray expansion discovers the subgraph.

        Returns:
            Async callable suitable for ``StateGraph.add_node``.
        """

        async def _retrieve_worker(
            state: BriefGeneAgentState,
        ) -> Dict[str, Any]:
            task_index = cast(int, state.get("task_index", 0))
            knowledge_input = cast(
                Dict[str, Any], state.get("knowledge_input", {})
            )
            try:
                knowledge_output = await knowledge_app.ainvoke(knowledge_input)
                docs = extract_brief_gene_knowledge_response(knowledge_output)
                return {
                    "retrieve_indexed_results": [(task_index, docs)],
                }
            except _BRIEF_GENE_RETRIEVE_WORKER_CAUGHT:
                return {
                    "retrieve_indexed_results": [(task_index, [])],
                }

        return _retrieve_worker

    async def retrieve_reduce_node(
        self: Any, state: BriefGeneAgentState
    ) -> Dict[str, Any]:
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
        indexed = sorted(
            state.get("retrieve_indexed_results", []),
            key=lambda entry: entry[0],
        )
        merged_docs: List[Dict[str, Any]] = []
        for _, docs in indexed:
            merged_docs.extend(doc for doc in docs if isinstance(doc, dict))
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
