# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Digital-design subgraph mount adapter for DeepGenomeAgents.

Hosts ``make_design_mount_node``: a factory closure that wires the
standalone ``DigitalDesignAgents`` graph as a structural subgraph. The
closure captures the compiled app for xray, projects the Send payload
into the design graph input (submit-only), and hands normalized design
submissions to the owning coordinator.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from langgraph.graph.state import CompiledStateGraph

from ...runtime.deep_genome_store import DeepGenomeTrackingError
from .coordinator import RemoteSubmission, normalize_submission
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

# Callback shape: (submissions, state) -> analyst-branch delta. The
# finalize helper reads species_code / target_gene / task_index from the
# Send payload state, so the mount node only forwards normalized remote
# identities.
FinalizeFn = Callable[
    [dict[str, RemoteSubmission | None], "DeepGenomeState"],
    Awaitable[dict[str, Any]],
]
FailureFn = Callable[["DeepGenomeState", tuple[str, ...]], Awaitable[None]]


def _work_item_key(payload: dict[str, Any], task_ids: dict[str, Any]) -> str:
    """Resolve one design result to its concrete work-item key."""
    analysis_type = payload.get("analysis_type")
    if analysis_type in {"protein_design", "protein_design_analysis"}:
        return "protein_design"
    if analysis_type in {"promoter_design", "promoter_design_analysis"}:
        return "promoter_design"
    task_id = payload.get("task_id")
    for key, known_task_id in task_ids.items():
        if key in {"protein_design", "promoter_design"} and str(
            known_task_id
        ) == str(task_id):
            return key
    raise ValueError("design submission has no concrete work-item key")


def _normalize_design_submissions(
    design_output: dict[str, Any],
) -> dict[str, RemoteSubmission | None]:
    """Normalize the two independent Design submission acknowledgements."""
    task_ids = dict(design_output.get("task_ids") or {})
    raw_results = design_output.get("design_task_result") or []
    if not isinstance(raw_results, list):
        raw_results = []
    submissions: dict[str, RemoteSubmission | None] = {
        "protein_design": None,
        "promoter_design": None,
    }
    accepted_keys: set[str] = set()
    for raw_result in raw_results:
        if not isinstance(raw_result, dict):
            continue
        try:
            key = _work_item_key(raw_result, task_ids)
        except ValueError:
            continue
        if key in accepted_keys:
            continue
        try:
            submission = normalize_submission(raw_result)
        except ValueError:
            continue
        submissions[key] = submission
        accepted_keys.add(key)
    return submissions


def make_design_mount_node(
    design_app: CompiledStateGraph,
    finalize_fn: FinalizeFn,
    failure_fn: FailureFn | None = None,
) -> Any:
    """Return a node body that mounts the digital-design graph.

    Closes over ``design_app`` so ``find_subgraph_pregel`` discovers the
    compiled graph through the closure free-variable and xray expands it
    under the parent ``design_node`` key. ``finalize_fn`` is the
    deep_genome dispatch helper that records the normalized submissions;
    it is a plain callable (not captured for xray).

    Input projection: the node receives a ``Send`` payload carrying the
    design task (``species_code`` / ``target_gene`` / ``task_index``) and
    projects it into the design graph input with ``is_polling`` off. The
    owning coordinator polls each returned work item independently.

    On a mount fault the closure logs the exception and returns a
    degraded delta (a ``FailureRecord`` on the ``failures`` channel plus
    a failed branch) so the analysis barrier still advances.

    Args:
        design_app: Compiled ``DigitalDesignAgents`` subgraph for this
            consumer instance.
        finalize_fn: Async callback receiving normalized submissions for
            the independent protein and promoter work items.

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
            "is_polling": False,
            "locale": state.get("locale"),
        }
        try:
            design_output: dict[str, Any] = await design_app.ainvoke(
                design_input
            )
            submissions = _normalize_design_submissions(design_output)
            return await finalize_fn(submissions, state)
        except DeepGenomeTrackingError:
            raise
        except _DESIGN_MOUNT_CAUGHT as exc:
            if failure_fn is not None:
                await failure_fn(
                    state,
                    ("protein_design", "promoter_design"),
                )
            logger.exception(
                "design mount failed for gene_id=%s; emitting a "
                "FailureRecord + failed branch so the analysis barrier "
                "advances",
                gene_id,
            )
            return degraded_analysis_delta("digital_design", task_index, exc)

    return _design_mount
