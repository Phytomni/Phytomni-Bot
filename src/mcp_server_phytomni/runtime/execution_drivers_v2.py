# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Thin Drivers that delegate to canonical business operation seams."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import ClassVar

from ..public_agent_catalog import Driver
from .execution_runtime_contracts import (
    DriverOperation,
    DriverOutcome,
    ExecutionCommand,
    ExecutionContext,
    ExecutionRuntimeError,
    ExecutionServices,
)

type DriverOperationHandler = Callable[
    [ExecutionContext, ExecutionCommand, ExecutionServices],
    Awaitable[DriverOutcome],
]


@dataclass(init=False, repr=False, eq=False, match_args=False)
class DelegatingExecutionDriver:
    """Common validation and operation dispatch for every Driver topology."""

    driver_kind: ClassVar[Driver]

    def __init__(
        self,
        handlers: Mapping[DriverOperation, DriverOperationHandler],
    ) -> None:
        self._handlers = dict(handlers)

    def supports(self, operation: DriverOperation) -> bool:
        """Return whether this Driver has a handler for the operation."""
        return operation in self._handlers

    async def execute(
        self,
        operation: DriverOperation,
        context: ExecutionContext,
        command: ExecutionCommand,
        services: ExecutionServices,
    ) -> DriverOutcome:
        """Validate the driver and dispatch one registered operation."""
        if context.agent.driver != self.driver_kind:
            raise ExecutionRuntimeError("driver_agent_mismatch")
        if not self.supports(operation):
            raise ExecutionRuntimeError("driver_operation_unavailable")
        handler = self._handlers[operation]
        outcome = await handler(context, command, services)
        if not isinstance(outcome, DriverOutcome):
            raise ExecutionRuntimeError("invalid_driver_outcome")
        return outcome


class LocalGraphDriver(DelegatingExecutionDriver):
    """Adapter for in-process canonical graph handlers."""

    driver_kind = "local_graph"


class RemoteTaskDriver(DelegatingExecutionDriver):
    """Adapter for one durable provider task and its reconciler."""

    driver_kind = "remote_task"


class RemoteFanoutDriver(DelegatingExecutionDriver):
    """Adapter for durable parallel provider branches and their join."""

    driver_kind = "remote_fanout"


class ResumableGraphDriver(DelegatingExecutionDriver):
    """Adapter for checkpointed graph handlers and input actions."""

    driver_kind = "resumable_graph"


class HybridDriver(DelegatingExecutionDriver):
    """Adapter for canonical graph plus durable remote work handlers."""

    driver_kind = "hybrid"


CANONICAL_DRIVER_TYPES: Mapping[Driver, type[DelegatingExecutionDriver]] = {
    "local_graph": LocalGraphDriver,
    "remote_task": RemoteTaskDriver,
    "remote_fanout": RemoteFanoutDriver,
    "resumable_graph": ResumableGraphDriver,
    "hybrid": HybridDriver,
}
