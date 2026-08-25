# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Evolution subgraph mount adapter for DeepGenomeAgents.

Hosts the ``make_evolution_mount_node`` factory closure that wires the
standalone evolution graph (``build_evolution_graph``) as a structural
subgraph:
the closure captures the compiled app for xray expansion, projects the
Send payload into ``EvolutionInput`` (scope ``"All"``, submit-only), and
hands the normalized submission to a shared coordinator callback.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from langgraph.graph.state import CompiledStateGraph

from ...runtime.deep_genome_store import DeepGenomeTrackingError
from ...runtime.langgraph_runner import invoke_graph
from .coordinator import RemoteSubmission, normalize_submission
from .mount_common import degraded_analysis_delta

if TYPE_CHECKING:
    from .agent import DeepGenomeState
else:
    DeepGenomeState = dict[str, Any]

logger = logging.getLogger(__name__)

# Evolution-mount catch scope (grep this name).
_EVOLUTION_MOUNT_CAUGHT: tuple[type[BaseException], ...] = (Exception,)

# Finalize reads species_code / target_gene / task_index from the Send
# payload, so the mount only forwards a normalized submission.
FinalizeFn = Callable[
    [RemoteSubmission, "DeepGenomeState"],
    Awaitable[dict[str, Any]],
]
FailureFn = Callable[["DeepGenomeState", tuple[str, ...]], Awaitable[None]]


def make_evolution_mount_node(
    evolution_app: CompiledStateGraph,
    finalize_fn: FinalizeFn,
    failure_fn: FailureFn | None = None,
) -> Any:
    """Return a node body that mounts the evolution graph as a subgraph.

    Closes over ``evolution_app`` so ``find_subgraph_pregel`` discovers
    the compiled graph through the closure free-variable and xray
    expands it under the parent ``evolution_node`` key. ``finalize_fn``
    is the deep_genome dispatch helper that builds the sub-summary after
    the coordinator observes remote success; it is a plain callable (not
    captured for xray).

    Input projection: the node receives a ``Send`` payload carrying the
    evolution task (``species_code`` / ``target_gene`` / ``task_index``)
    and projects it into ``EvolutionInput`` with the taxonomy scope
    pinned to ``"All"`` (so the mounted graph skips its chat extraction)
    and ``is_polling`` off (so the coordinator owns remote waiting).

    On a mount fault the closure logs the exception and returns a
    degraded delta (a ``FailureRecord`` on the ``failures`` channel plus
    a failed branch) so the analysis barrier still advances.

    Args:
        evolution_app: Compiled evolution subgraph for this consumer
            instance (from ``build_evolution_graph()``).
        finalize_fn: Async callback receiving a normalized remote
            submission.

    Returns:
        Async callable suitable for ``StateGraph.add_node``.
    """

    async def _evolution_mount(state: DeepGenomeState) -> dict[str, Any]:
        species_code = state["species_code"]
        gene_id = state["target_gene"]
        task_index = state.get("task_index")
        evo_input: dict[str, Any] = {
            "query": gene_id,
            "species_code": species_code,
            "gene_id": gene_id,
            "target_taxids": "All",
            "is_polling": False,
            "locale": state.get("locale"),
        }
        try:
            evo_output: dict[str, Any] = await invoke_graph(
                evolution_app, evo_input
            )
            task = evo_output.get("evolution_agents_task")
            if not isinstance(task, dict):
                raise ValueError("evolution submission is not an object")
            submission = normalize_submission(task)
            return await finalize_fn(submission, state)
        except DeepGenomeTrackingError:
            raise
        except _EVOLUTION_MOUNT_CAUGHT as exc:
            if failure_fn is not None:
                await failure_fn(state, ("evolution_analysis",))
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
