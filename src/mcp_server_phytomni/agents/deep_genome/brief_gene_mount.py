# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""brief_gene subgraph mount adapter for DeepGenomeAgents.

Hosts the ``make_brief_gene_mount_node`` factory closure that wires
``BriefGeneAgent`` as a structural subgraph inside the deep_genome
workflow, projecting BriefGeneOutput into deep_genome's
``gene_annotation`` + ``knowledge_context`` + verbatim ``preamble``
plus the experiment barrier increment.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from langgraph.graph.state import CompiledStateGraph

from ...common.responses import message_content

if TYPE_CHECKING:
    from .agent import DeepGenomeState
else:
    DeepGenomeState = dict[str, Any]


logger = logging.getLogger(__name__)

_BRIEF_TITLE_PREFIX = "# Brief Gene Analysis of"


class RequiredBriefGeneError(RuntimeError):
    """Raised when the mandatory BriefGene profile cannot be produced."""


def _deep_genome_preamble(brief_output: dict[str, Any], gene_id: str) -> str:
    """Return brief_gene's preamble with the deep_genome H1 title.

    brief_gene renders ``# Brief Gene Analysis of <gene>`` as the first
    line; deep_genome's report uses the same preamble verbatim from the
    second line down, so only the H1 title is swapped. A degraded or
    empty brief_gene answer (no recognised title line) is passed through
    unchanged.
    """
    raw = message_content(brief_output.get("final_response", {}) or {})
    resolved = str(brief_output.get("gene_id", "") or gene_id)
    deep_title = f"# Deep Genome Analysis of {resolved}"
    head, sep, tail = raw.partition("\n")
    if head.startswith(_BRIEF_TITLE_PREFIX):
        return f"{deep_title}{sep}{tail}"
    return raw


# Module-level tuple documents the ordinary-error boundary; cancellation
# inherits BaseException and therefore remains outside the catch.
_BRIEF_GENE_MOUNT_CAUGHT: tuple[type[BaseException], ...] = (Exception,)


def _project_brief_gene_output(
    brief_output: dict[str, Any], gene_id: str
) -> dict[str, Any]:
    """Project one validated BriefGene output into DeepGenome state."""
    gene_string = str(brief_output.get("gene_id", "") or gene_id)
    return {
        "gene_annotation": {
            "gene_string": gene_string,
            "description": str(brief_output.get("description_string", "")),
            "go": str(brief_output.get("go_string", "")),
            "interpro": str(brief_output.get("interpro_string", "")),
            "mapman": str(brief_output.get("kegg_string", "")),
            "gene_structure": str(
                brief_output.get("gene_structure_string", "")
            ),
        },
        "knowledge_context": {
            "literature": list(brief_output.get("retrieved_docs", []) or [])
        },
        "preamble": _deep_genome_preamble(brief_output, gene_id),
        "experiment_completed_branches": 1,
        "literature_degraded": brief_output.get("literature_degraded", []),
    }


def make_brief_gene_mount_node(
    brief_gene_app: CompiledStateGraph,
    persist_fn: (
        Callable[[dict[str, Any], Any], Awaitable[dict[str, Any]]] | None
    ) = None,
) -> Any:
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
    ``gene_annotation: dict`` shape (the experiment / discussion /
    summary prompts read it), maps ``retrieved_docs`` into the
    ``knowledge_context: {literature}`` shape, projects brief_gene's
    rendered answer (``final_response``) into ``preamble`` with the H1
    title swapped to deep_genome's — the report's verbatim pre-analysis
    block — and writes ``experiment_completed_branches: 1`` so the
    experiment_node 2-source barrier still fires.

    ``persist_fn`` is an optional post-projection hook for the owning
    coordinator. It runs only after a successful BriefGene projection
    and receives the projected state delta plus the original DeepGenome
    state. Keeping the hook in this factory (rather than wrapping the
    returned callable in another closure) preserves the direct
    ``brief_gene_app`` free variable that LangGraph uses for xray
    subgraph discovery.

    On brief_gene failure, the closure logs only the sanitized exception
    class and raises ``RequiredBriefGeneError``. The graph therefore stops
    before task preparation or any remote analysis submission.

    Args:
        brief_gene_app: Compiled BriefGeneAgent subgraph for this
            consumer instance. Must be a ``CompiledStateGraph`` so
            xray expansion discovers the subgraph.
        persist_fn: Optional async owner hook invoked after successful
            projection; it must return the projected state delta.

    Returns:
        Async callable suitable for ``StateGraph.add_node``.
    """

    async def _brief_gene_mount(state: DeepGenomeState) -> dict[str, Any]:
        gene_id = state["gene_id"]
        brief_input: dict[str, Any] = {
            "user_query": gene_id,
            "is_follow_up": False,
            "locale": state.get("locale"),
        }
        try:
            brief_output = await brief_gene_app.ainvoke(brief_input)
            if not isinstance(brief_output, dict):
                raise TypeError("BriefGene output is not an object")
            projected = _project_brief_gene_output(brief_output, gene_id)
            if persist_fn is not None:
                return await persist_fn(projected, state)
            return projected
        except _BRIEF_GENE_MOUNT_CAUGHT as exc:
            logger.error(
                "brief_gene mount failed for gene_id=%s; error_type=%s",
                gene_id,
                type(exc).__name__,
            )
            raise RequiredBriefGeneError("brief gene profile failed") from None

    return _brief_gene_mount
