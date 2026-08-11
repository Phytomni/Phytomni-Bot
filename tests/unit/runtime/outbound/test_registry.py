# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the logical, process-local outbound request pool registry."""

from __future__ import annotations

import asyncio
import logging

import pytest

from mcp_server_phytomni.runtime.outbound import (
    OutboundPoolName,
    OutboundPoolRegistry,
    OutboundRuntimeClosedError,
)

pytestmark = pytest.mark.unit


def _capacities(**overrides: int) -> dict[OutboundPoolName, int]:
    """Return every finite pool capacity with selected overrides applied."""
    capacities = dict.fromkeys(OutboundPoolName, 0)
    for name, capacity in overrides.items():
        capacities[OutboundPoolName(name)] = capacity
    return capacities


async def _wait_for_waiters(
    registry: OutboundPoolRegistry,
    name: OutboundPoolName,
    count: int,
) -> None:
    """Yield until a task reaches the requested logical waiting state."""
    for _ in range(100):
        if registry.snapshot(name).waiting == count:
            return
        await asyncio.sleep(0)
    pytest.fail(f"pool {name.value} did not reach {count} queued borrowers")


@pytest.mark.asyncio
async def test_capacity_one_waits_and_releases_in_arrival_order() -> None:
    """A bounded logical pool admits queued borrowers in FIFO order."""
    registry = OutboundPoolRegistry(_capacities(llm=1), wait_warn_seconds=0.01)
    entered = asyncio.Event()
    release = asyncio.Event()
    order: list[str] = []

    async def first() -> None:
        """Hold the one available LLM slot until the test releases it."""
        async with registry.lease(OutboundPoolName.LLM):
            order.append("first")
            entered.set()
            await release.wait()

    async def queued(label: str) -> None:
        """Acquire the LLM pool after the first borrower leaves."""
        await entered.wait()
        async with registry.lease(OutboundPoolName.LLM):
            order.append(label)

    first_task = asyncio.create_task(first())
    await entered.wait()
    second = asyncio.create_task(queued("second"))
    await _wait_for_waiters(registry, OutboundPoolName.LLM, 1)
    third = asyncio.create_task(queued("third"))
    await _wait_for_waiters(registry, OutboundPoolName.LLM, 2)
    release.set()
    await asyncio.gather(first_task, second, third)
    assert order == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_unlimited_capacity_tracks_active_borrowers() -> None:
    """Capacity zero never waits but still reports in-use logical leases."""
    registry = OutboundPoolRegistry(_capacities(), wait_warn_seconds=0.01)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def borrow() -> None:
        """Keep an unlimited lease active for snapshot inspection."""
        async with registry.lease(OutboundPoolName.OBS):
            entered.set()
            await release.wait()

    task = asyncio.create_task(borrow())
    await entered.wait()
    snapshot = registry.snapshot(OutboundPoolName.OBS)
    assert snapshot.capacity == 0
    assert snapshot.in_use == 1
    assert snapshot.waiting == 0
    release.set()
    await task
    assert registry.snapshot(OutboundPoolName.OBS).in_use == 0


@pytest.mark.asyncio
async def test_independent_pools_do_not_block_one_another() -> None:
    """One saturated service family leaves another family available."""
    registry = OutboundPoolRegistry(
        _capacities(llm=1, retrieval=1), wait_warn_seconds=0.01
    )
    llm_entered = asyncio.Event()
    retrieval_entered = asyncio.Event()
    release = asyncio.Event()

    async def hold(name: OutboundPoolName, entered: asyncio.Event) -> None:
        """Hold one named pool until both borrowers have entered."""
        async with registry.lease(name):
            entered.set()
            await release.wait()

    llm_task = asyncio.create_task(hold(OutboundPoolName.LLM, llm_entered))
    await llm_entered.wait()
    retrieval_task = asyncio.create_task(
        hold(OutboundPoolName.RETRIEVAL, retrieval_entered)
    )
    await retrieval_entered.wait()
    assert registry.snapshot(OutboundPoolName.LLM).in_use == 1
    assert registry.snapshot(OutboundPoolName.RETRIEVAL).in_use == 1
    release.set()
    await asyncio.gather(llm_task, retrieval_task)


@pytest.mark.asyncio
async def test_cancelled_waiter_is_removed_from_snapshot() -> None:
    """Cancellation while queued cannot strand a logical waiter."""
    registry = OutboundPoolRegistry(_capacities(llm=1), wait_warn_seconds=0.01)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        """Occupy the only LLM slot while another task queues."""
        async with registry.lease(OutboundPoolName.LLM):
            entered.set()
            await release.wait()

    async def waiter() -> None:
        """Wait for the held slot without entering the lease body."""
        async with registry.lease(OutboundPoolName.LLM):
            pytest.fail("cancelled waiter unexpectedly acquired the pool")

    holder_task = asyncio.create_task(holder())
    await entered.wait()
    waiter_task = asyncio.create_task(waiter())
    await _wait_for_waiters(registry, OutboundPoolName.LLM, 1)
    waiter_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter_task
    assert registry.snapshot(OutboundPoolName.LLM).waiting == 0
    assert registry.snapshot(OutboundPoolName.LLM).cancelled == 1
    release.set()
    await holder_task


@pytest.mark.asyncio
async def test_in_flight_cancellation_releases_the_pool() -> None:
    """Cancellation inside a lease releases its capacity before propagating."""
    registry = OutboundPoolRegistry(_capacities(llm=1), wait_warn_seconds=0.01)
    entered = asyncio.Event()

    async def borrower() -> None:
        """Acquire one slot and wait indefinitely for cancellation."""
        async with registry.lease(OutboundPoolName.LLM):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(borrower())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    snapshot = registry.snapshot(OutboundPoolName.LLM)
    assert snapshot.in_use == 0
    assert snapshot.cancelled == 1


@pytest.mark.asyncio
async def test_exception_releases_the_pool() -> None:
    """A borrower exception cannot leak a logical pool slot."""
    registry = OutboundPoolRegistry(_capacities(llm=1), wait_warn_seconds=0.01)

    with pytest.raises(RuntimeError, match="boom"):
        async with registry.lease(OutboundPoolName.LLM):
            raise RuntimeError("boom")

    snapshot = registry.snapshot(OutboundPoolName.LLM)
    assert snapshot.in_use == 0
    assert snapshot.failed == 1


@pytest.mark.asyncio
async def test_counters_only_increase_across_leases() -> None:
    """Attempt and wait counters retain monotonically increasing totals."""
    registry = OutboundPoolRegistry(_capacities(llm=1), wait_warn_seconds=0.01)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        """Hold the first slot so the next borrower records a wait."""
        async with registry.lease(OutboundPoolName.LLM):
            entered.set()
            await release.wait()

    async def waiter() -> None:
        """Acquire after the holder releases its capacity."""
        async with registry.lease(OutboundPoolName.LLM):
            return None

    holder_task = asyncio.create_task(holder())
    await entered.wait()
    waiter_task = asyncio.create_task(waiter())
    await _wait_for_waiters(registry, OutboundPoolName.LLM, 1)
    before = registry.snapshot(OutboundPoolName.LLM)
    release.set()
    await asyncio.gather(holder_task, waiter_task)
    after = registry.snapshot(OutboundPoolName.LLM)
    assert after.started > before.started
    assert after.completed > before.completed
    assert after.total_wait_seconds >= before.total_wait_seconds
    assert after.max_wait_seconds >= before.max_wait_seconds


@pytest.mark.asyncio
async def test_terminal_logs_publish_safe_cumulative_outcome_counters(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminal observations expose counters without caller-owned values."""
    registry = OutboundPoolRegistry(_capacities(llm=1), wait_warn_seconds=0.01)
    logger_name = "mcp_server_phytomni.runtime.outbound.registry"
    caplog.set_level(logging.INFO, logger=logger_name)
    monkeypatch.setattr(
        logging.getLogger("mcp_server_phytomni"), "propagate", True
    )

    async with registry.lease(OutboundPoolName.LLM):
        pass

    marker = "https://secret.invalid/query-user-run-task"
    with pytest.raises(RuntimeError, match="secret.invalid"):
        async with registry.lease(OutboundPoolName.LLM):
            raise RuntimeError(marker)

    entered = asyncio.Event()

    async def cancel_in_flight() -> None:
        """Hold one lease until the caller cancels the task."""
        async with registry.lease(OutboundPoolName.LLM):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(cancel_in_flight(), name=marker)
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    terminal = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("outbound pool terminal ")
    ]
    assert terminal == [
        "outbound pool terminal pool=llm capacity=1 in_use=1 waiting=0 "
        "outcome=completed started=1 completed=1 failed=0 cancelled=0",
        "outbound pool terminal pool=llm capacity=1 in_use=1 waiting=0 "
        "outcome=failed started=2 completed=1 failed=1 cancelled=0",
        "outbound pool terminal pool=llm capacity=1 in_use=1 waiting=0 "
        "outcome=cancelled started=3 completed=1 failed=1 cancelled=1",
    ]
    assert all(marker not in message for message in terminal)


@pytest.mark.asyncio
async def test_wait_warning_uses_only_fixed_pool_and_counter_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Wait diagnostics are value-safe and contain no caller-provided data."""
    registry = OutboundPoolRegistry(
        _capacities(llm=1), wait_warn_seconds=1e-12
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        """Hold the pool until the queued waiter has been observed."""
        async with registry.lease(OutboundPoolName.LLM):
            entered.set()
            await release.wait()

    async def waiter() -> None:
        """Acquire behind the holder and trigger the wait diagnostic."""
        async with registry.lease(OutboundPoolName.LLM):
            return None

    caplog.set_level(logging.WARNING)
    logger = logging.getLogger("mcp_server_phytomni.runtime.outbound.registry")
    logger.addHandler(caplog.handler)
    try:
        holder_task = asyncio.create_task(holder())
        await entered.wait()
        waiter_task = asyncio.create_task(waiter())
        await _wait_for_waiters(registry, OutboundPoolName.LLM, 1)
        release.set()
        await asyncio.gather(holder_task, waiter_task)
    finally:
        logger.removeHandler(caplog.handler)
    messages = [record.getMessage() for record in caplog.records]
    assert any("pool=llm" in message for message in messages)
    assert all("https://" not in message for message in messages)
    assert all("secret" not in message for message in messages)


@pytest.mark.asyncio
async def test_aclose_wakes_waiters_and_waits_for_active_borrowers() -> None:
    """Closing rejects queued work but lets active leases settle cleanly."""
    registry = OutboundPoolRegistry(_capacities(llm=1), wait_warn_seconds=0.01)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        """Keep an active lease open until close has begun."""
        async with registry.lease(OutboundPoolName.LLM):
            entered.set()
            await release.wait()

    async def waiter() -> None:
        """Prove close wakes queued borrowers with the lifecycle error."""
        async with registry.lease(OutboundPoolName.LLM):
            pytest.fail("closed registry unexpectedly granted a queued lease")

    holder_task = asyncio.create_task(holder())
    await entered.wait()
    waiter_task = asyncio.create_task(waiter())
    await _wait_for_waiters(registry, OutboundPoolName.LLM, 1)
    close_task = asyncio.create_task(registry.aclose())
    with pytest.raises(OutboundRuntimeClosedError):
        await waiter_task
    assert not close_task.done()
    release.set()
    await asyncio.gather(holder_task, close_task)
    assert registry.snapshot(OutboundPoolName.LLM).in_use == 0
    assert tuple(snapshot.name for snapshot in registry.snapshots()) == tuple(
        OutboundPoolName
    )


@pytest.mark.asyncio
async def test_closed_registry_rejects_new_acquisitions() -> None:
    """No new lease begins after the close lifecycle has started."""
    registry = OutboundPoolRegistry(_capacities(), wait_warn_seconds=0.01)
    await registry.aclose()

    with pytest.raises(OutboundRuntimeClosedError):
        async with registry.lease(OutboundPoolName.LLM):
            pytest.fail("closed registry unexpectedly granted a lease")
