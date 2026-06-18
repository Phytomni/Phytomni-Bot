# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Evolution subgraph mount adapter for DeepGenomeAgents.

Hosts the ``make_evolution_mount_node`` factory closure that wires the
standalone evolution graph (``build_evolution_graph``) as a structural
subgraph:
the closure captures the compiled app for xray expansion, projects the
Send payload into ``EvolutionInput`` (scope ``"All"``, polling on), and
hands the submitted task to a shared finalize callback.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Dict, Optional

from langgraph.graph.state import CompiledStateGraph

from .mount_common import degraded_analysis_delta

if TYPE_CHECKING:
    from .agent import DeepGenomeState
else:
    DeepGenomeState = Dict[str, Any]

logger = logging.getLogger(__name__)

# Broad catch tuple, lifted to module scope so the pylint broad-except
# (W0718) report fires once at load instead of at the except site —
# matches the brief_gene mount's _BRIEF_GENE_MOUNT_CAUGHT pattern.
_EVOLUTION_MOUNT_CAUGHT: tuple[type[BaseException], ...] = (Exception,)

# Callback shape: (task, state) -> analyst-branch delta. The finalize
# helper reads species_code / target_gene / task_index from the Send
# payload state, so the mount node only forwards the submitted task.
FinalizeFn = Callable[
    [Optional[dict], "DeepGenomeState"],
    Awaitable[Dict[str, Any]],
]


def make_evolution_mount_node(
    evolution_app: CompiledStateGraph,
    finalize_fn: FinalizeFn,
) -> Any:
    """Return a node body that mounts the evolution graph as a subgraph.

    Closes over ``evolution_app`` so ``find_subgraph_pregel`` discovers
    the compiled graph through the closure free-variable and xray
    expands it under the parent ``evolution_node`` key. ``finalize_fn``
    is the deep_genome dispatch helper that downloads results and builds
    the sub-summary; it is a plain callable (not captured for xray).

    Input projection: the node receives a ``Send`` payload carrying the
    evolution task (``species_code`` / ``target_gene`` / ``task_index``)
    and projects it into ``EvolutionInput`` with the taxonomy scope
    pinned to ``"All"`` (so the mounted graph skips its chat extraction)
    and ``is_polling`` on (so the analyst submission blocks and the
    report can read results synchronously).

    On a mount fault the closure logs the exception and returns a
    degraded delta (a ``FailureRecord`` on the ``failures`` channel plus
    a failed branch) so the analysis barrier still advances.

    Args:
        evolution_app: Compiled evolution subgraph for this consumer
            instance (from ``build_evolution_graph()``).
        finalize_fn: Async callback running the shared download +
            ``build_sub_summary`` post-processing.

    Returns:
        Async callable suitable for ``StateGraph.add_node``.
    """

    async def _evolution_mount(state: DeepGenomeState) -> Dict[str, Any]:
        species_code = state["species_code"]
        gene_id = state["target_gene"]
        task_index = state.get("task_index")
        evo_input: Dict[str, Any] = {
            "query": gene_id,
            "species_code": species_code,
            "gene_id": gene_id,
            "target_taxids": "All",
            "is_polling": True,
        }
        try:
            evo_output: Dict[str, Any] = await evolution_app.ainvoke(evo_input)
            task = evo_output.get("evolution_agents_task")
            return await finalize_fn(task, state)
        except _EVOLUTION_MOUNT_CAUGHT as exc:
            logger.exception(
                "evolution mount failed for gene_id=%s; emitting a "
                "FailureRecord + failed branch so the analysis barrier "
                "advances",
                gene_id,
            )
            return degraded_analysis_delta(
                "evolution_analysis", task_index, exc
            )

    return _evolution_mount
