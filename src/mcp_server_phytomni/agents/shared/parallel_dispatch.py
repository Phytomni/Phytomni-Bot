# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Builder for the parallel-dispatch StateGraph pattern.

Classes: FailureRecord, ParallelDispatchSpec, ParallelDispatchState.
Functions: build_parallel_dispatch_graph.
"""

import operator
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from ...common.redaction import redact_secrets
from ...runtime.langgraph_runner import make_async_router
from .interop import InteropRecord

__all__ = [
    "DegradedRecord",
    "FailureRecord",
    "ParallelDispatchSpec",
    "ParallelDispatchState",
    "build_parallel_dispatch_graph",
    "degraded_labels",
    "keep_last_error",
    "redact_failure_message",
]


class FailureRecord(TypedDict):
    """One failed-task record contributed by a parallel-dispatch worker.

    Carries (task_label, message, kind, traceback_digest) so reduce_node
    and metadata projection can show per-task failure context without
    relying on the lossy keep_last_error reducer.

    Fields:
        task_label: Caller-chosen identifier for the failed task.
            design/network/research workers use the task's domain key
            (analysis_type / goal_name); review workers use
            "<fan_out>:<task_index>" form e.g. "draft:2".
        message: ``str(exc)`` from the captured exception.
        kind: Where in the worker lifecycle the failure occurred.
        traceback_digest: SHA256-truncated-16 of the traceback string,
            or None if the worker did not compute one. Lives in
            raw.phytomni_state only; never surfaced to client metadata.
    """

    task_label: str
    message: str
    kind: Literal["dispatch", "execute", "render"]
    traceback_digest: str | None


class DegradedRecord(TypedDict):
    """One non-fatal degradation a worker recovered from.

    Distinct from :class:`FailureRecord`: rides a separate
    ``literature_degraded`` channel that never feeds the PARTIAL/FAILED
    status projection, only the status-independent ``degraded`` surface.

    Fields:
        task_label: The query term (gene symbol) whose retrieve leg
            degraded.
        message: ``redact_failure_message(str(exc))`` of the recovered
            exception.
    """

    task_label: str
    message: str


def keep_last_error(
    left: str | None,
    right: str | None,
) -> str | None:
    """Reduce concurrent error writes, keeping the latest non-empty one.

    Parallel-dispatch worker branches each merge their own update into the
    shared state; without a reducer two branches writing ``error`` raise a
    LangGraph concurrent-update conflict. Prefer the most recent non-empty
    error so a failing branch is never masked by a sibling's ``None``.

    Args:
        left: Error already on the channel (or ``None``).
        right: Error contributed by the merging branch (or ``None``).

    Returns:
        ``right`` when it is truthy, otherwise ``left``.
    """
    return right if right else left


def redact_failure_message(message: str) -> str:
    """Strip URLs and secret-like fragments from a failure message.

    ``FailureRecord.message`` is ``str(exc)`` from a worker exception and
    reaches client-facing surfaces. Backend HTTP errors (httpx) embed the
    request URL — internal hostnames, ports, and paths — and a credential
    can ride in a query parameter or an ``Authorization`` fragment.
    Delegates to ``common.redaction.redact_secrets`` (the single
    implementation shared with the package log formatter, so a redacted
    failure message and a redacted log line use identical rules) and keeps
    the surrounding error text so the failure stays diagnosable
    (``"Connection failed for <redacted-url>"``).
    """
    return redact_secrets(message)


def degraded_labels(records: list[DegradedRecord]) -> list[str]:
    """Return the sorted unique task labels from degraded records.

    Shared by the brief_gene ``degraded`` metadata projection and the
    deep_genome degraded-reason assembly so the label-list formatting
    lives once (keeps the two off the duplicate-code guard).
    """
    return sorted({record["task_label"] for record in records})


class ParallelDispatchState(TypedDict):
    """Shared parallel-dispatch bookkeeping for fan-out worker graphs.

    Domain state TypedDicts (``DigitalDesignState``,
    ``GeneNetworkState``, ...) inherit these fields so the Send-fan-out
    reducer contract — concurrent-safe ``task_ids`` / ``completed_count``
    merges and last-error retention — is declared exactly once instead
    of being copied into every domain state.

    Attributes:
        analysis_type: Selected analysis variant for the run.
        task_index: Current task index in parallel execution via Send.
        task_ids: Mapping of task names to their dispatched task IDs.
        completed_count: Counter of completed parallel tasks.
        error: Most recent non-empty per-task error message.
        failures: Per-task failure records accumulated across concurrent
            worker branches via operator.add (list concatenation).
    """

    analysis_type: str
    task_index: int | None  # Current task index in parallel execution
    task_ids: Annotated[
        dict[str, str], operator.or_
    ]  # Mapping of task names to task IDs
    completed_count: Annotated[int, operator.add]  # Completed task counter
    error: Annotated[str | None, keep_last_error]  # Last task error
    failures: Annotated[
        list[FailureRecord], operator.add
    ]  # Per-task failure records (concurrent-safe accumulator)
    interop: Annotated[
        list[InteropRecord], operator.add
    ]  # External delegation summaries
    degraded_interop: Annotated[
        bool, operator.or_
    ]  # True when auto mode fell back locally


@dataclass(frozen=True)
class ParallelDispatchSpec:
    """Inputs needed to compile a parallel-dispatch LangGraph workflow.

    Attributes:
        state_class: TypedDict subclass used as the graph state schema.
        prepare_node: Async node callable that builds the task list on
            state. Registered as ``"prepare_tasks_node"``.
        work_node: Async node callable invoked once per dispatched task.
        route_fn: Callable that converts state into a list of LangGraph
            ``Send`` commands targeted at ``work_node_name``.
        work_node_name: Node name used for the worker (matches the
            existing per-domain convention, e.g. ``"design_node"``).
        extract_node: Optional async node callable run before
            ``prepare_tasks_node`` (used by workflows that extract goals
            or context from input before task preparation).
        extract_node_name: Node name used for ``extract_node`` when
            present. Defaults to ``"extract_node"``.
    """

    state_class: type
    prepare_node: Callable[..., Any]
    work_node: Callable[..., Any]
    route_fn: Callable[..., Any]
    work_node_name: str
    extract_node: Callable[..., Any] | None = None
    extract_node_name: str = "extract_node"


def build_parallel_dispatch_graph(
    spec: ParallelDispatchSpec,
    checkpointer: Any | None = None,
    *,
    post_work_nodes: tuple[tuple[str, Callable[..., Any]], ...] = (),
) -> Any:
    """Compile the standard parallel-dispatch LangGraph workflow.

    Layout (with ``spec.extract_node`` set):

        START → extract_node → prepare_tasks_node
                                  ↓ conditional (Send list)
                                work_node → END

    Layout (without ``spec.extract_node``):

        START → prepare_tasks_node
                  ↓ conditional (Send list)
                work_node → END

    Args:
        spec: Configuration describing the state class, node callables,
            route function, and optional extract-node prefix.
        checkpointer: Optional LangGraph checkpointer forwarded to
            ``StateGraph.compile``.
        post_work_nodes: Optional node callables reached through a
            ``Command(goto=...)`` returned by ``work_node``. Each tuple
            contains the node name and callable; nodes are terminal unless
            the callable itself routes elsewhere.

    Returns:
        Compiled LangGraph application ready for ``ainvoke``.
    """
    workflow: StateGraph = StateGraph(spec.state_class)
    workflow.add_node("prepare_tasks_node", spec.prepare_node)
    workflow.add_node(spec.work_node_name, spec.work_node)
    for node_name, node_callable in post_work_nodes:
        workflow.add_node(node_name, node_callable)
    if spec.extract_node is not None:
        workflow.add_node(spec.extract_node_name, spec.extract_node)
        workflow.add_edge(START, spec.extract_node_name)
        workflow.add_edge(spec.extract_node_name, "prepare_tasks_node")
    else:
        workflow.add_edge(START, "prepare_tasks_node")
    workflow.add_conditional_edges(
        "prepare_tasks_node",
        make_async_router(spec.route_fn),
        [spec.work_node_name],
    )
    workflow.add_edge(spec.work_node_name, END)
    for node_name, _node_callable in post_work_nodes:
        workflow.add_edge(node_name, END)
    return workflow.compile(checkpointer=checkpointer)
