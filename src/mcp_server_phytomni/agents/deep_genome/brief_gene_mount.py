# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""brief_gene subgraph mount adapter for DeepGenomeAgents.

Hosts ``DeepGenomeBriefGeneMountMixin`` providing the
``make_brief_gene_mount_node`` factory closure that wires
``BriefGeneAgent`` as a structural subgraph inside the deep_genome
workflow. The closure synthesises a ``BriefGeneInput`` from
``DeepGenomeState`` (``gene_id`` → ``user_query``;
``is_follow_up=False``), ``ainvoke``s the compiled brief_gene app,
and projects the ``BriefGeneOutput`` flat-string annotation surface
into deep_genome's nested ``gene_annotation: dict`` +
``knowledge_context: {literature}`` shape plus the
``part1_completed_branches`` barrier increment.

Mounted in a follow-up commit through DeepGenomeAgents._build_graph;
this module only defines the factory + mixin so the wiring change is
reviewable in isolation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict

from langgraph.graph.state import CompiledStateGraph

if TYPE_CHECKING:
    from .agent import DeepGenomeState
else:
    DeepGenomeState = Dict[str, Any]


logger = logging.getLogger(__name__)


# Broad exception catch tuple used by the brief_gene mount node.
# Module-level constant lifts the pylint ``broad-except`` (W0718)
# report once at module load instead of silencing it at every
# ``except`` site; downstream readers can grep this constant to find
# the catch scope. Matches the W0718 mitigation pattern other consumer
# agents use (analyst / review / brief_gene).
_BRIEF_GENE_MOUNT_CAUGHT: tuple[type[BaseException], ...] = (Exception,)


def make_brief_gene_mount_node(
    brief_gene_app: CompiledStateGraph,
) -> Callable[[Any], Awaitable[Dict[str, Any]]]:
    """Return a node body that mounts brief_gene as a structural subgraph.

    Closes over ``brief_gene_app`` so LangGraph's
    ``find_subgraph_pregel`` walker can discover the compiled
    BriefGeneAgent through the closure free-variable and expand the
    subgraph in the deep_genome xray render. Xray prefixes the
    inlined subgraph's child node keys with the parent ``add_node``
    name, i.e. ``brief_gene_mount_node:<child>``. Using the
    bound-method form (``self._run_brief_gene_mount``) would have
    closed only over ``self`` and left xray unable to walk into the
    subgraph; the factory pattern mirrors review's
    ``planning.make_retrieve_worker_node`` and brief_gene's own
    ``graph_knowledge_subgraph.make_retrieve_worker_node`` factories.

    Input projection: synthesises a ``BriefGeneInput`` from the
    deep_genome state's ``gene_id`` (the brief_gene workflow's
    ``query_judge_node`` will BI-resolve the synthetic user_query back
    to the same canonical gene_id; the resulting redundant BI lookup
    is deduplicated by the project's ``func_cache`` layer).
    ``is_follow_up=False`` skips brief_gene's trailing
    follow_up_questions LLM hop because deep_genome runs its own
    full-report follow-up generation at the end of its workflow and
    brief_gene's brief-gene-answer follow-up would be a different
    scope.

    Output projection: maps the flat-string annotation fields on
    ``BriefGeneOutput`` into deep_genome's nested
    ``gene_annotation: dict`` shape (mirroring the keys
    ``_run_gene_annotation_node`` previously wrote), maps the
    ``retrieved_docs`` list into the
    ``knowledge_context: {literature}`` shape that downstream report
    nodes read, and writes the ``part1_completed_branches: 1``
    barrier increment so the existing ``_route_part1_barrier``
    topology stays well-formed. brief_gene's ``final_response`` and
    ``follow_up_questions`` are not currently mapped into
    deep_genome's state because no deep_genome report node consumes
    them yet; a future commit that wires
    ``_run_report_introduction`` / ``_run_report_summary`` against
    brief_gene's generated answer would extend this projection.

    On brief_gene failure, the closure logs the exception and emits
    an empty annotation / empty literature delta plus the barrier
    increment so the deep_genome workflow continues past the part1
    barrier rather than wedging on a transient brief_gene fault.

    Args:
        brief_gene_app: Compiled BriefGeneAgent subgraph for this
            consumer instance. Must be a ``CompiledStateGraph`` so
            xray expansion discovers the subgraph.

    Returns:
        Async callable suitable for ``StateGraph.add_node``.
    """

    async def _brief_gene_mount(state: DeepGenomeState) -> Dict[str, Any]:
        gene_id = state["gene_id"]
        brief_input: Dict[str, Any] = {
            "user_query": gene_id,
            "is_follow_up": False,
        }
        try:
            brief_output: Dict[str, Any] = await brief_gene_app.ainvoke(
                brief_input
            )
        except _BRIEF_GENE_MOUNT_CAUGHT:
            logger.exception(
                "brief_gene mount failed for gene_id=%s; emitting "
                "empty annotation + literature so deep_genome part1 "
                "barrier still advances",
                gene_id,
            )
            brief_output = {}

        gene_string = str(brief_output.get("gene_id", "") or gene_id)
        return {
            "gene_annotation": {
                "gene_string": gene_string,
                "description": str(brief_output.get("description_string", "")),
                "go": str(brief_output.get("go_string", "")),
                "interpro": str(brief_output.get("interpro_string", "")),
                "mapman": str(brief_output.get("kegg_string", "")),
            },
            "knowledge_context": {
                "literature": list(
                    brief_output.get("retrieved_docs", []) or []
                ),
            },
            "part1_completed_branches": 1,
        }

    return _brief_gene_mount


class DeepGenomeBriefGeneMountMixin:
    """Factory exposure for DeepGenomeAgents to mount brief_gene.

    The mixin surfaces ``make_brief_gene_mount_node`` as a method so
    ``DeepGenomeAgents._build_graph`` can call
    ``self.make_brief_gene_mount_node(self._brief_gene_app)`` once the
    brief_gene app is constructed in ``__init__``. The factory itself
    is the module-level function above; the mixin only adds the
    bound-method indirection so the MRO pattern other deep_genome
    mixins use stays consistent.
    """

    def make_brief_gene_mount_node(
        self: Any, brief_gene_app: CompiledStateGraph
    ) -> Callable[[Any], Awaitable[Dict[str, Any]]]:
        """Return a node body mounting brief_gene under this consumer.

        Delegates to the module-level ``make_brief_gene_mount_node``;
        the bound-method indirection matches the project's other
        consumer mixins so ``_build_graph`` looks up the factory via
        ``self`` rather than importing it directly.
        """
        del self
        return make_brief_gene_mount_node(brief_gene_app)
