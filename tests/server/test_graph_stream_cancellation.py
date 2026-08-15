# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Cancellation ownership tests for streamed LangGraph agents."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, TypedDict, cast

import anyio
import pytest
from langgraph.graph import END, START, StateGraph
from tests.support.outbound_fakes import (
    ControlledByteStream,
    QueueTransport,
    RecordingResources,
    recording_outbound_runtime,
)

from mcp_server_phytomni.config.defaults import ServerConfig
from mcp_server_phytomni.mcp import app as mcp_app
from mcp_server_phytomni.mcp.result_formatting import AguiEvent
from mcp_server_phytomni.runtime.outbound import OutboundPoolName


class _GraphState(TypedDict, total=False):
    """Minimal state accepted by the real LangGraph test graph."""

    marker: str


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


async def _consume(events: AsyncIterator[AguiEvent]) -> None:
    async for _event in events:
        pass


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
