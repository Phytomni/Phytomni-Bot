# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Common conformance suite for the five canonical Driver skeletons."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast

import pytest

from mcp_server_phytomni.public_agent_catalog import (
    PUBLIC_AGENT_CATALOG,
    public_agent_spec,
)
from mcp_server_phytomni.runtime.execution_drivers_v2 import (
    CANONICAL_DRIVER_TYPES,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    DriverOperation,
    DriverOutcome,
    ExecutionCommand,
    ExecutionContext,
    ExecutionRuntimeError,
    ExecutionServices,
)


def _context(driver_kind):
    spec = next(
        spec for spec in PUBLIC_AGENT_CATALOG if spec.driver == driver_kind
    )
    return ExecutionContext(
        owner_ref="alice",
        execution_id=f"turn-{driver_kind}",
        fingerprint_version=1,
        fingerprint="a" * 64,
        agent=spec,
        root_span_id="span-root",
        current_span_id="span-root",
        transport="test",
        deadline_at=datetime(2026, 8, 20, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    "driver_kind,driver_type", CANONICAL_DRIVER_TYPES.items()
)
def test_driver_delegates_every_normalized_operation_without_business_logic(
    driver_kind,
    driver_type,
) -> None:
    calls = []

    async def handler(context, command, services):
        calls.append((context, command, services))
        return DriverOutcome.running()

    handlers = {operation: handler for operation in DriverOperation}
    driver = driver_type(handlers)
    context = _context(driver_kind)
    command = ExecutionCommand(agent_slug=context.agent.slug, arguments={})
    services = object()

    for operation in DriverOperation:
        outcome = asyncio.run(
            driver.execute(operation, context, command, services)
        )
        assert outcome.status.value == "running"
    assert len(calls) == len(DriverOperation)
    assert all(call == (context, command, services) for call in calls)


@pytest.mark.parametrize(
    "driver_kind,driver_type", CANONICAL_DRIVER_TYPES.items()
)
def test_driver_fails_closed_for_missing_operation(
    driver_kind, driver_type
) -> None:
    driver = driver_type({})
    context = _context(driver_kind)
    command = ExecutionCommand(agent_slug=context.agent.slug, arguments={})

    with pytest.raises(
        ExecutionRuntimeError,
        match="driver_operation_unavailable",
    ):
        asyncio.run(
            driver.execute(
                DriverOperation.START,
                context,
                command,
                cast(ExecutionServices, object()),
            )
        )


def test_driver_rejects_agent_bound_to_another_driver() -> None:
    async def handler(context, command, services):
        del context, command, services
        return DriverOutcome.running()

    driver = CANONICAL_DRIVER_TYPES["remote_task"](
        {DriverOperation.START: handler}
    )
    chat = public_agent_spec("chat")
    assert chat is not None
    context = ExecutionContext(
        owner_ref="alice",
        execution_id="turn-mismatch",
        fingerprint_version=1,
        fingerprint="b" * 64,
        agent=chat,
        root_span_id="span-root",
        current_span_id="span-root",
        transport="test",
    )
    with pytest.raises(ExecutionRuntimeError, match="driver_agent_mismatch"):
        asyncio.run(
            driver.execute(
                DriverOperation.START,
                context,
                ExecutionCommand(agent_slug="chat", arguments={}),
                cast(ExecutionServices, object()),
            )
        )


def test_catalog_and_driver_registry_have_exactly_the_same_driver_kinds() -> (
    None
):
    assert set(CANONICAL_DRIVER_TYPES) == {
        spec.driver for spec in PUBLIC_AGENT_CATALOG
    }
