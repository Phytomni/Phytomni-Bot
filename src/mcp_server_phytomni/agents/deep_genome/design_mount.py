# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Digital-design subgraph mount adapter for DeepGenomeAgents.

Hosts ``make_design_mount_node``: a factory closure that wires the
standalone ``DigitalDesignAgents`` graph as a structural subgraph. The
closure captures the compiled app for xray, projects the Send payload
into the design graph input (polling on), and hands the design output to
a shared finalize callback that lights the §8.2 protein-design section.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from langgraph.graph.state import CompiledStateGraph

from .mount_common import degraded_analysis_delta

if TYPE_CHECKING:
    from .agent import DeepGenomeState
else:
    DeepGenomeState = dict[str, Any]

logger = logging.getLogger(__name__)

# Broad catch tuple, lifted to module scope so the pylint broad-except
# (W0718) report fires once at load instead of at the except site --
# matches the evolution mount's _EVOLUTION_MOUNT_CAUGHT pattern.
_DESIGN_MOUNT_CAUGHT: tuple[type[BaseException], ...] = (Exception,)

# Callback shape: (design_output, state) -> analyst-branch delta. The
# finalize helper reads species_code / target_gene / task_index from the
# Send payload state, so the mount node only forwards the graph output.
FinalizeFn = Callable[
    [dict[str, Any], "DeepGenomeState"],
    Awaitable[dict[str, Any]],
]


def make_design_mount_node(
    design_app: CompiledStateGraph,
    finalize_fn: FinalizeFn,
) -> Any:
    """Return a node body that mounts the digital-design graph.

    Closes over ``design_app`` so ``find_subgraph_pregel`` discovers the
    compiled graph through the closure free-variable and xray expands it
    under the parent ``design_node`` key. ``finalize_fn`` is the
    deep_genome dispatch helper that downloads the protein-design result
    and builds the §8.2 sub-summary; it is a plain callable (not captured
    for xray).

    Input projection: the node receives a ``Send`` payload carrying the
    design task (``species_code`` / ``target_gene`` / ``task_index``) and
    projects it into the design graph input with ``is_polling`` on (so
    both design sub-tasks block and the report can read results
    synchronously).

    On a mount fault the closure logs the exception and returns a
    degraded delta (a ``FailureRecord`` on the ``failures`` channel plus
    a failed branch) so the analysis barrier still advances.

    Args:
        design_app: Compiled ``DigitalDesignAgents`` subgraph for this
            consumer instance.
        finalize_fn: Async callback running the shared download +
            ``build_sub_summary`` post-processing for the protein-design
            task.

    Returns:
        Async callable suitable for ``StateGraph.add_node``.
    """

    async def _design_mount(state: DeepGenomeState) -> dict[str, Any]:
        species_code = state["species_code"]
        gene_id = state["target_gene"]
        task_index = state.get("task_index")
        design_input: dict[str, Any] = {
            "species_code": species_code,
            "gene_id": gene_id,
            "is_polling": True,
        }
        try:
            design_output: dict[str, Any] = await design_app.ainvoke(
                design_input
            )
            return await finalize_fn(design_output, state)
        except _DESIGN_MOUNT_CAUGHT as exc:
            logger.exception(
                "design mount failed for gene_id=%s; emitting a "
                "FailureRecord + failed branch so the analysis barrier "
                "advances",
                gene_id,
            )
            return degraded_analysis_delta("digital_design", task_index, exc)

    return _design_mount
