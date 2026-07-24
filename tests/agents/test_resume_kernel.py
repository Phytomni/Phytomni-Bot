# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for the protocol-agnostic resume kernel."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver

from mcp_server_phytomni.runtime.langgraph_runner import (
    build_runnable_config,
)
from mcp_server_phytomni.runtime.resume import (
    NoCheckpointError,
    ahas_checkpoint,
    aresume_graph,
    detect_interrupt,
)
from tests.support.resume_graph import build_resume_app


@pytest.mark.asyncio
async def test_detect_interrupt_reads_paused_draft() -> None:
    """detect_interrupt surfaces the draft payload from a paused run."""
    app = build_resume_app(MemorySaver())
    final = await app.ainvoke(
        {"value": "draft-text"},
        config=build_runnable_config("t-1"),
    )
    info = detect_interrupt(final)
    assert info is not None
    assert info["draft"] == {"draft": "draft-text"}


@pytest.mark.asyncio
async def test_aresume_graph_finalizes_on_approval() -> None:
    """aresume_graph drives the paused graph to its terminal state."""
    app = build_resume_app(MemorySaver())
    await app.ainvoke(
        {"value": "draft-text"},
        config=build_runnable_config("t-2"),
    )
    result = await aresume_graph(app, "t-2", {"approved": True})
    assert result["final"] == "ok"


@pytest.mark.asyncio
async def test_ahas_checkpoint_distinguishes_durable_pause() -> None:
    """The shared probe fails closed and sees a persisted pause."""
    app = build_resume_app(MemorySaver())
    assert not await ahas_checkpoint(app, "t-probe")
    await app.ainvoke(
        {"value": "draft-text"},
        config=build_runnable_config("t-probe"),
    )
    assert await ahas_checkpoint(app, "t-probe")
    assert not await ahas_checkpoint(object(), "t-probe")


@pytest.mark.asyncio
async def test_aresume_graph_unknown_thread_raises() -> None:
    """Resuming a thread with no stored pause point raises cleanly."""
    app = build_resume_app(MemorySaver())
    with pytest.raises(NoCheckpointError):
        await aresume_graph(app, "never-started", {"approved": True})
