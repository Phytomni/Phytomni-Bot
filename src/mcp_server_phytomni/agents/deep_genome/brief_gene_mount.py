# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""brief_gene subgraph mount adapter for DeepGenomeAgents.

Hosts ``DeepGenomeBriefGeneMountMixin`` + the
``make_brief_gene_mount_node`` factory closure that wires
``BriefGeneAgent`` as a structural subgraph inside the deep_genome
workflow, projecting BriefGeneOutput into deep_genome's
``gene_annotation`` + ``knowledge_context`` + verbatim ``preamble``
plus the experiment barrier increment.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langgraph.graph.state import CompiledStateGraph

from ...common.responses import message_content
from ..shared.parallel_dispatch import FailureRecord, redact_failure_message

if TYPE_CHECKING:
    from .agent import DeepGenomeState
else:
    DeepGenomeState = dict[str, Any]


logger = logging.getLogger(__name__)

_BRIEF_TITLE_PREFIX = "# Brief Gene Analysis of"


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


# Broad exception catch tuple used by the brief_gene mount node.
# Module-level constant lifts the pylint ``broad-except`` (W0718)
# report once at module load instead of silencing it at every
# ``except`` site; downstream readers can grep this constant to find
# the catch scope. Matches the W0718 mitigation pattern other consumer
# agents use (analyst / review / brief_gene).
_BRIEF_GENE_MOUNT_CAUGHT: tuple[type[BaseException], ...] = (Exception,)


_DEGRADED_BANNER = (
    "> ⚠️ **Gene profile unavailable** — the gene-overview step did "
    "not complete\n> for this run, so the introduction and gene-profile "
    "section are omitted.\n> The bioinformatic analysis below proceeded "
    "without them."
)


def _degraded_preamble_banner(gene_id: str) -> str:
    """Return a titled preamble carrying a visible degradation banner.

    Used when the brief_gene mount catches a fault: the report still
    gets an H1 title plus a blockquote that flags the missing profile,
    so a human reading the persisted markdown sees the degradation
    rather than a headless document.
    """
    return f"# Deep Genome Analysis of {gene_id}\n\n{_DEGRADED_BANNER}"


def _degraded_mount_delta(gene_id: str, exc: BaseException) -> dict[str, Any]:
    """Return the deep_genome state delta for a failed brief_gene mount.

    Carries a ``FailureRecord`` on the ``failures`` channel (machine
    signal), a banner ``preamble`` (human signal), an empty but
    well-formed annotation / literature delta, and the experiment
    barrier increment so the workflow advances past the part1 barrier
    instead of wedging. ``traceback_digest`` is ``None``: a single mount
    record needs no cross-record correlation, and ``logger.exception``
    already records the full traceback. The message is redacted at
    creation (``redact_failure_message``) so no secret rides it into the
    ``raw.phytomni_state`` debug envelope.
    """
    return {
        "failures": [
            FailureRecord(
                task_label="brief_gene_preamble",
                message=redact_failure_message(str(exc)),
                kind="execute",
                traceback_digest=None,
            )
        ],
        "gene_annotation": {
            "gene_string": gene_id,
            "description": "",
            "go": "",
            "interpro": "",
            "mapman": "",
            "gene_structure": "",
        },
        "knowledge_context": {"literature": []},
        "preamble": _degraded_preamble_banner(gene_id),
        "experiment_completed_branches": 1,
    }


def make_brief_gene_mount_node(
    brief_gene_app: CompiledStateGraph,
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

    On brief_gene failure, the closure logs the exception and returns a
    degraded delta (a ``FailureRecord`` on the ``failures`` channel, a
    visible banner ``preamble``, and an empty annotation / literature
    delta) plus the barrier increment, so the deep_genome workflow
    continues past the part1 barrier rather than wedging on a transient
    brief_gene fault while still flagging the missing gene profile.

    Args:
        brief_gene_app: Compiled BriefGeneAgent subgraph for this
            consumer instance. Must be a ``CompiledStateGraph`` so
            xray expansion discovers the subgraph.

    Returns:
        Async callable suitable for ``StateGraph.add_node``.
    """

    async def _brief_gene_mount(state: DeepGenomeState) -> dict[str, Any]:
        gene_id = state["gene_id"]
        brief_input: dict[str, Any] = {
            "user_query": gene_id,
            "is_follow_up": False,
        }
        try:
            brief_output: dict[str, Any] = await brief_gene_app.ainvoke(
                brief_input
            )
        except _BRIEF_GENE_MOUNT_CAUGHT as exc:
            logger.exception(
                "brief_gene mount failed for gene_id=%s; emitting a "
                "degraded preamble + FailureRecord so the part1 barrier "
                "advances and the report flags the missing gene profile",
                gene_id,
            )
            return _degraded_mount_delta(gene_id, exc)

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
                "literature": list(
                    brief_output.get("retrieved_docs", []) or []
                ),
            },
            # brief_gene owns the entire preamble: its rendered answer
            # is the report's pre-analysis block verbatim (only the H1
            # title is swapped). deep_genome appends its
            # ``## Bioinformatic Analysis`` body from synthesize_node
            # onward, so the section / introduction / homology fields
            # brief_gene uses internally are no longer projected.
            "preamble": _deep_genome_preamble(brief_output, gene_id),
            # Preamble convergence — the brief_gene mount substitutes for
            # the legacy 4-branch preamble + part1_node aggregator
            # entirely, so we satisfy the experiment_node barrier
            # contribution that part1_node used to write (the analyst
            # synthesize_node contributes the other +1).
            "experiment_completed_branches": 1,
            "literature_degraded": brief_output.get("literature_degraded", []),
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
    ) -> Any:
        """Return a node body mounting brief_gene under this consumer.

        Delegates to the module-level ``make_brief_gene_mount_node``;
        the bound-method indirection matches the project's other
        consumer mixins so ``_build_graph`` looks up the factory via
        ``self`` rather than importing it directly.
        """
        del self
        return make_brief_gene_mount_node(brief_gene_app)
