# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared Protocols for Send-based parallel-dispatch fan-out workers."""

from typing import Any, Dict, List, Protocol, runtime_checkable

TaskPayload = Dict[str, Any]
StateDelta = Dict[str, Any]


__all__ = [
    "FanOutWorker",
    "StateDelta",
    "TaskBuilder",
    "TaskPayload",
]


@runtime_checkable
class FanOutWorker(Protocol):
    """A Send-dispatched worker for parallel fan-out subgraphs.

    Reads state fields that the dispatch route function baked into the
    per-task Send payload (because LangGraph merges the Send payload
    into state before calling the worker). Returns a state delta that
    merges through ``Annotated`` reducers (e.g., ``operator.add`` for
    ``<X>_indexed_results`` accumulators).

    Workers MUST NOT raise. On exception, write a FailureRecord to
    state["failures"] (via the ParallelDispatchState failure channel)
    AND a legacy sentinel to the domain-specific result channel
    (e.g., ``""`` for chat sentinels, ``[]`` for retrieve fragments).
    The sentinel coexistence is a compatibility shim during migration;
    summary/post_process read the failures channel directly so the
    sentinel can be retired by a later step.
    """

    async def __call__(self, state: Dict[str, Any]) -> StateDelta: ...


class TaskBuilder(Protocol):
    """A prepare-tasks function for parallel fan-out subgraphs.

    Reads base state and returns the list of per-task state dicts.
    The route function wraps each into a ``Send(node, payload)``.
    """

    def __call__(self, state: Dict[str, Any]) -> List[TaskPayload]: ...
