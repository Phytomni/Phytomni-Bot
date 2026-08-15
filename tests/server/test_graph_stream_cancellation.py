# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Cancellation ownership tests for streamed LangGraph agents."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, TypedDict, cast

import anyio
import pytest
from langgraph.graph import END, START, StateGraph
from tests.support.logging_helpers import capture_non_propagating_logger
from tests.support.outbound_fakes import (
    ControlledByteStream,
    QueueTransport,
    RecordingResources,
    recording_outbound_runtime,
)

from mcp_server_phytomni.config.defaults import ServerConfig
from mcp_server_phytomni.mcp import app as mcp_app
from mcp_server_phytomni.mcp.result_formatting import AguiEvent
from mcp_server_phytomni.mcp.streaming_phases import (
    close_async_iterator,
    iterate_owned,
)
from mcp_server_phytomni.runtime import cleanup as cleanup_runtime
from mcp_server_phytomni.runtime.outbound import OutboundPoolName


@pytest.fixture(autouse=True)
def _attach_cleanup_log_handler(
    caplog: pytest.LogCaptureFixture,
) -> Iterator[None]:
    """Capture cleanup logs after package logging disables propagation."""
    with capture_non_propagating_logger(
        cleanup_runtime.__name__, caplog.handler
    ):
        yield


class _GraphState(TypedDict, total=False):
    """Minimal state accepted by the real LangGraph test graph."""

    marker: str
    final_response: dict[str, Any]


@dataclass
class _UpstreamState:
    """Track two request-owned iterators without retaining payload data."""

    active: int = 0
    cancelled: int = 0
    both_started: asyncio.Event = field(default_factory=asyncio.Event)
    tasks: set[asyncio.Task[None]] = field(default_factory=set)

    async def wait_forever(self) -> None:
        """Remain active until graph cancellation reaches this task."""
        task = asyncio.current_task()
        assert task is not None
        self.tasks.add(task)
        self.active += 1
        if self.active == 2:
            self.both_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.active -= 1


class _CheckpointCloseStream(ControlledByteStream):
    """Expose response-close cancellation through one async checkpoint."""

    async def aclose(self) -> None:
        await asyncio.sleep(0)
        await super().aclose()

    def is_closed(self) -> bool:
        """Return whether the transport reached its close callback."""
        return self.closed


class _CleanupFailureIterator:
    """Raise secret-bearing cleanup detail after an in-flight cancellation."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    def __aiter__(self) -> _CleanupFailureIterator:
        """Return this request-owned iterator."""
        return self

    async def __anext__(self) -> Any:
        """Raise a cleanup failure after cancellation reaches the next call."""
        self.started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError as exc:
            raise RuntimeError("synthetic-provider-secret") from exc

    async def aclose(self) -> None:
        """Close without introducing another test failure."""
        return None


class _CancellationResistantIterator:
    """Ignore cancellation until released to model a stuck graph iterator."""

    def __init__(self, *, resistant_close: bool = False) -> None:
        self.started = asyncio.Event()
        self.release_next = asyncio.Event()
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()
        self.resistant_close = resistant_close

    def __aiter__(self) -> _CancellationResistantIterator:
        """Return this request-owned iterator."""
        return self

    async def __anext__(self) -> Any:
        """Ignore cancellation until the test releases the next call."""
        self.started.set()
        while not self.release_next.is_set():
            try:
                await self.release_next.wait()
            except asyncio.CancelledError:
                continue
        raise StopAsyncIteration

    async def aclose(self) -> None:
        """Optionally resist cancellation until the test releases close."""
        self.close_started.set()
        if not self.resistant_close:
            return
        while not self.release_close.is_set():
            try:
                await self.release_close.wait()
            except asyncio.CancelledError:
                continue


@dataclass
class _IsolatedStreamState:
    """Track one graph stream independently from every sibling stream."""

    started: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    active: int = 0
    cancelled: int = 0
    completed: int = 0

    async def wait(self) -> None:
        """Wait for this stream's release or record its own cancellation."""
        self.active += 1
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        else:
            self.completed += 1
        finally:
            self.active -= 1


def _blocking_graph(upstream_state: _UpstreamState) -> Any:
    """Build one real graph node that owns two concurrent upstream tasks."""

    async def retrieve_node(state: _GraphState) -> _GraphState:
        del state
        await asyncio.gather(
            upstream_state.wait_forever(), upstream_state.wait_forever()
        )
        return {}

    builder = StateGraph(_GraphState)
    builder.add_node("retrieve_node", retrieve_node)
    builder.add_edge(START, "retrieve_node")
    builder.add_edge("retrieve_node", END)
    return builder.compile()


def _isolated_graph(stream_state: _IsolatedStreamState) -> Any:
    """Build one graph whose lifecycle belongs to exactly one consumer."""

    async def retrieve_node(state: _GraphState) -> _GraphState:
        del state
        await stream_state.wait()
        return {
            "final_response": {
                "choices": [{"message": {"content": "answer", "doc_list": []}}]
            }
        }

    builder = StateGraph(_GraphState)
    builder.add_node("retrieve_node", retrieve_node)
    builder.add_edge(START, "retrieve_node")
    builder.add_edge("retrieve_node", END)
    return builder.compile()


async def _consume(events: AsyncIterator[AguiEvent]) -> None:
    async for _event in events:
        pass


async def _consume_owned(stream: AsyncIterator[Any]) -> None:
    """Exhaust one request-owned iterator."""
    try:
        async for _item in iterate_owned(stream):
            pass
    finally:
        await close_async_iterator(stream)


async def test_next_cleanup_failure_preserves_original_cancellation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed pending-next cleanup cannot become an ordinary stream error."""
    stream = _CleanupFailureIterator()
    task = asyncio.create_task(_consume_owned(stream))
    await stream.started.wait()

    caplog.set_level(logging.WARNING, logger=cleanup_runtime.__name__)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert "stream cleanup failed operation=iterator_next" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "synthetic-provider-secret" not in caplog.text


async def test_pending_next_cleanup_has_a_finite_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancellation-resistant pending next call cannot hang its consumer."""
    stream = _CancellationResistantIterator()
    task = asyncio.create_task(_consume_owned(stream))
    await stream.started.wait()
    monkeypatch.setattr(cleanup_runtime, "CLEANUP_TIMEOUT_SECONDS", 0.05)
    started_at = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - started_at < 0.15

    await asyncio.sleep(0.2)
    assert cleanup_runtime.pending_cleanup_count() == 1
    monkeypatch.setattr(
        cleanup_runtime,
        "CLEANUP_SHUTDOWN_TIMEOUT_SECONDS",
        0.05,
    )
    with pytest.raises(cleanup_runtime.CleanupLifecycleError):
        await cleanup_runtime.aclose_cleanup_runtime()
    assert cleanup_runtime.pending_cleanup_count() == 1

    stream.release_next.set()
    for _ in range(100):
        if cleanup_runtime.pending_cleanup_count() == 0:
            break
        await asyncio.sleep(0)
    assert cleanup_runtime.pending_cleanup_count() == 0
    await asyncio.sleep(0)


async def test_graph_iterator_close_has_a_finite_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancellation-resistant iterator close cannot hang graph shutdown."""
    stream = _CancellationResistantIterator(resistant_close=True)
    monkeypatch.setattr(cleanup_runtime, "CLEANUP_TIMEOUT_SECONDS", 0.05)
    task = asyncio.create_task(_consume_owned(stream))
    await stream.started.wait()
    stream.release_next.set()
    await stream.close_started.wait()
    started_at = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - started_at < 0.15

    await asyncio.sleep(0.2)
    assert cleanup_runtime.pending_cleanup_count() == 1

    stream.release_close.set()
    for _ in range(100):
        if cleanup_runtime.pending_cleanup_count() == 0:
            break
        await asyncio.sleep(0)
    assert cleanup_runtime.pending_cleanup_count() == 0


async def test_disconnect_cancels_all_inflight_graph_upstreams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An AnyIO response cancellation closes both graph-owned leaves."""
    state = _UpstreamState()
    monkeypatch.setattr(
        mcp_app,
        "_build_graph_stream_target",
        lambda *_args, **_kwargs: (_blocking_graph(state), {}),
    )
    events = mcp_app.prepare_tool_stream(
        "KnowledgeAgent",
        {"user_query": "cancel", "obs_file_list": []},
        run_id="run-cancel",
        dialogue_id=None,
    )

    try:
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(_consume, events)
            await asyncio.wait_for(state.both_started.wait(), timeout=1.0)
            assert state.active == 2
            task_group.cancel_scope.cancel()
        await asyncio.sleep(0)

        assert state.active == 0
        assert state.cancelled == 2
    finally:
        for task in state.tasks:
            if not task.done():
                task.cancel()
        if state.tasks:
            await asyncio.gather(*state.tasks, return_exceptions=True)
        with suppress(RuntimeError):
            await cast(AsyncGenerator[AguiEvent, None], events).aclose()


async def test_cancelling_one_of_two_simultaneous_streams_is_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling stream A cannot cancel or settle simultaneous stream B."""
    states = {
        "stream-a": _IsolatedStreamState(),
        "stream-b": _IsolatedStreamState(),
    }

    def build_target(_tool_name: str, args: Any, **_kwargs: Any) -> Any:
        state = states[args.user_query]
        return _isolated_graph(state), {}

    monkeypatch.setattr(mcp_app, "_build_graph_stream_target", build_target)
    stream_a = mcp_app.prepare_tool_stream(
        "KnowledgeAgent",
        {"user_query": "stream-a", "obs_file_list": []},
        run_id="run-a",
        dialogue_id=None,
    )
    stream_b = mcp_app.prepare_tool_stream(
        "KnowledgeAgent",
        {"user_query": "stream-b", "obs_file_list": []},
        run_id="run-b",
        dialogue_id=None,
    )
    task_a = asyncio.create_task(_consume(stream_a))
    task_b = asyncio.create_task(_consume(stream_b))

    try:
        await asyncio.gather(
            states["stream-a"].started.wait(),
            states["stream-b"].started.wait(),
        )
        assert states["stream-a"].active == 1
        assert states["stream-b"].active == 1

        task_a.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task_a

        assert states["stream-a"].active == 0
        assert states["stream-a"].cancelled == 1
        assert states["stream-b"].active == 1
        assert states["stream-b"].cancelled == 0
        assert states["stream-b"].completed == 0
        assert task_b.done() is False

        states["stream-b"].release.set()
        await task_b
        assert states["stream-b"].active == 0
        assert states["stream-b"].cancelled == 0
        assert states["stream-b"].completed == 1
    finally:
        states["stream-a"].release.set()
        states["stream-b"].release.set()
        for task in (task_a, task_b):
            if not task.done():
                task.cancel()
        await asyncio.gather(task_a, task_b, return_exceptions=True)
        for stream in (stream_a, stream_b):
            with suppress(RuntimeError):
                await cast(AsyncGenerator[AguiEvent, None], stream).aclose()


async def test_cancel_scope_closes_buffered_upstream_response() -> None:
    """Level cancellation cannot interrupt request-owned response close."""
    entered = asyncio.Event()
    release = asyncio.Event()
    stream = _CheckpointCloseStream(entered=entered, release=release)
    transport = QueueTransport()
    transport.enqueue(stream=stream)
    resources = RecordingResources(transport=transport)

    async with recording_outbound_runtime(
        config=ServerConfig(),
        resources=resources,
    ) as runtime:
        client = runtime.http.for_pool(OutboundPoolName.RETRIEVAL)

        async def request() -> None:
            await client.request("GET", "https://upstream.invalid/body")

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(request)
            await entered.wait()
            task_group.cancel_scope.cancel()

        assert stream.is_closed() is True
