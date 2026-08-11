# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Cancellation-safe, process-local logical outbound request pools."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import anyio

from .models import (
    OutboundPoolName,
    OutboundPoolSnapshot,
    OutboundRuntimeClosedError,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _PoolState:  # pylint: disable=too-many-instance-attributes
    """Mutable accounting and limiter for one logical pool."""

    capacity: int
    limiter: anyio.CapacityLimiter | None
    in_use: int = 0
    max_in_use: int = 0
    started: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    total_wait_seconds: float = 0.0
    max_wait_seconds: float = 0.0
    waiters: deque[object] = field(default_factory=deque)


class OutboundPoolRegistry:
    """Own fixed service pools without coupling them to HTTP clients."""

    def __init__(
        self,
        capacities: Mapping[OutboundPoolName, int],
        *,
        wait_warn_seconds: float,
    ) -> None:
        """Create every finite pool and validate local accounting inputs."""
        if wait_warn_seconds <= 0 or not math.isfinite(wait_warn_seconds):
            raise ValueError("wait_warn_seconds must be positive and finite")
        self._states = {
            name: _PoolState(
                capacity=self._capacity_for(capacities, name),
                limiter=(
                    anyio.CapacityLimiter(self._capacity_for(capacities, name))
                    if self._capacity_for(capacities, name) > 0
                    else None
                ),
            )
            for name in OutboundPoolName
        }
        self._wait_warn_seconds = wait_warn_seconds
        self._condition = anyio.Condition()
        self._closing = False

    @staticmethod
    def _capacity_for(
        capacities: Mapping[OutboundPoolName, int], name: OutboundPoolName
    ) -> int:
        """Validate and return one required non-negative pool capacity."""
        try:
            capacity = capacities[name]
        except KeyError as exc:
            raise ValueError(
                f"missing outbound capacity for {name.value}"
            ) from exc
        if (
            isinstance(capacity, bool)
            or not isinstance(capacity, int)
            or capacity < 0
        ):
            raise ValueError(
                f"outbound capacity for {name.value} must be >= 0"
            )
        return capacity

    @asynccontextmanager
    async def lease(self, name: OutboundPoolName) -> AsyncIterator[None]:
        """Acquire one named logical pool lease and release it reliably."""
        state = self._states[name]
        acquired = False
        borrower: object | None = None
        try:
            borrower = await self._acquire(name, state)
            acquired = True
        except asyncio.CancelledError:
            await self._record_cancelled(state)
            raise
        try:
            yield
        except asyncio.CancelledError:
            await self._record_outcome(state, "cancelled")
            raise
        except BaseException:
            await self._record_outcome(state, "failed")
            raise
        else:
            await self._record_outcome(state, "completed")
        finally:
            if acquired:
                assert borrower is not None
                await self._release(state, borrower)

    async def _acquire(
        self, name: OutboundPoolName, state: _PoolState
    ) -> object:
        """Wait in FIFO order, then reserve one logical capacity slot."""
        token = object()
        queued = False
        waited = False
        started_at = time.perf_counter()
        try:
            async with self._condition:
                if self._closing:
                    raise OutboundRuntimeClosedError(
                        "outbound runtime is closing"
                    )
                state.waiters.append(token)
                queued = True
                while True:
                    if self._closing:
                        raise OutboundRuntimeClosedError(
                            "outbound runtime is closing"
                        )
                    has_capacity = (
                        state.capacity == 0 or state.in_use < state.capacity
                    )
                    if state.waiters[0] is token and has_capacity:
                        state.waiters.popleft()
                        queued = False
                        state.in_use += 1
                        break
                    waited = True
                    await self._condition.wait()
            try:
                if state.limiter is not None:
                    # Use an explicit borrower token: a stream can be closed
                    # by a different task than the one that opened it.
                    await state.limiter.acquire_on_behalf_of(token)
            except BaseException:
                with anyio.CancelScope(shield=True):
                    async with self._condition:
                        state.in_use -= 1
                        self._condition.notify_all()
                raise
            if waited:
                waited_seconds = time.perf_counter() - started_at
                state.total_wait_seconds += waited_seconds
                state.max_wait_seconds = max(
                    state.max_wait_seconds, waited_seconds
                )
                self._warn_if_waited(name, waited_seconds, state)
            state.started += 1
            state.max_in_use = max(state.max_in_use, state.in_use)
            return token
        except BaseException:
            if queued:
                await self._remove_waiter(state, token)
            raise

    async def _remove_waiter(self, state: _PoolState, token: object) -> None:
        """Remove a cancelled or closed waiter without cancellation leakage."""
        with anyio.CancelScope(shield=True):
            async with self._condition:
                try:
                    state.waiters.remove(token)
                except ValueError:
                    return
                self._condition.notify_all()

    def _warn_if_waited(
        self,
        name: OutboundPoolName,
        waited_seconds: float,
        state: _PoolState,
    ) -> None:
        """Emit a value-safe warning past the configured threshold."""
        if waited_seconds < self._wait_warn_seconds:
            return
        _LOGGER.warning(
            "outbound pool wait exceeded pool=%s wait_seconds=%.6f waiting=%d",
            name.value,
            waited_seconds,
            len(state.waiters),
        )

    async def _release(self, state: _PoolState, borrower: object) -> None:
        """Return one lease even when the borrower is being cancelled."""
        with anyio.CancelScope(shield=True):
            async with self._condition:
                if state.limiter is not None:
                    state.limiter.release_on_behalf_of(borrower)
                state.in_use -= 1
                self._condition.notify_all()

    async def _record_cancelled(self, state: _PoolState) -> None:
        """Count cancellation of a waiter without acquiring capacity."""
        with anyio.CancelScope(shield=True):
            async with self._condition:
                state.cancelled += 1

    async def _record_outcome(
        self,
        state: _PoolState,
        outcome: str,
    ) -> None:
        """Count one terminal outcome without exposing exception details."""
        with anyio.CancelScope(shield=True):
            async with self._condition:
                if outcome == "completed":
                    state.completed += 1
                elif outcome == "failed":
                    state.failed += 1
                else:
                    state.cancelled += 1

    def snapshot(self, name: OutboundPoolName) -> OutboundPoolSnapshot:
        """Return value-safe accounting without exposing request data."""
        state = self._states[name]
        return OutboundPoolSnapshot(
            name=name,
            capacity=state.capacity,
            in_use=state.in_use,
            waiting=len(state.waiters),
            max_in_use=state.max_in_use,
            started=state.started,
            completed=state.completed,
            failed=state.failed,
            cancelled=state.cancelled,
            total_wait_seconds=state.total_wait_seconds,
            max_wait_seconds=state.max_wait_seconds,
        )

    def snapshots(self) -> tuple[OutboundPoolSnapshot, ...]:
        """Return immutable snapshots in the fixed enum order."""
        return tuple(self.snapshot(name) for name in OutboundPoolName)

    async def aclose(self) -> None:
        """Reject queued work and await active borrower releases."""
        async with self._condition:
            self._closing = True
            self._condition.notify_all()
            while any(state.in_use for state in self._states.values()):
                await self._condition.wait()
