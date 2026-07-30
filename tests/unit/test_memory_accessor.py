# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the bounded graph-facing memory accessor."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict, cast

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from mcp_server_phytomni.config.defaults import ApiConfig
from mcp_server_phytomni.runtime.langgraph_runner import ainvoke_graph
from mcp_server_phytomni.runtime.memory.accessor import (
    MemoryAccessor,
    MemoryGraphContext,
    current_memory_accessor,
    memory_accessor_context,
    memory_policy_from_config,
    resolve_memory_accessor,
)
from mcp_server_phytomni.runtime.memory.models import MemoryRecord, MemoryWrite
from mcp_server_phytomni.runtime.memory.sqlite import (
    MemoryListOptions,
    MemoryStore,
)
from mcp_server_phytomni.runtime.request_context import request_context

pytestmark = pytest.mark.unit


def _now(hour: int = 8) -> datetime:
    """Return a deterministic aware timestamp."""
    return datetime(2026, 7, 14, hour, 0, tzinfo=UTC)


def _write(content: str, *, user_id: str = "alice") -> MemoryWrite:
    """Build one valid memory payload."""
    return MemoryWrite(
        user_id=user_id,
        kind="preference",
        content=content,
    )


def test_memory_policy_from_config_keeps_store_and_graph_bounds_aligned() -> (
    None
):
    """The API knobs produce one shared policy for writes and graph reads."""
    config_cls = cast(Any, ApiConfig)
    config = config_cls(
        _env_file=None,
        MEMORY_MAX_ITEMS=3,
        MEMORY_MAX_CONTENT_BYTES=1024,
        MEMORY_MAX_TOTAL_BYTES=4096,
        MEMORY_MAX_RETRIEVAL=2,
    )

    policy = memory_policy_from_config(config)

    assert policy.max_items == 3
    assert policy.max_content_bytes == 1024
    assert policy.max_total_bytes == 4096
    assert policy.max_retrieval == 2


def test_anonymous_or_disabled_reads_never_open_store() -> None:
    """MCP/flag-off paths return empty without touching a store factory."""
    opened: list[bool] = []

    def factory() -> MemoryStore:
        opened.append(True)
        raise AssertionError("disabled or anonymous read opened SQLite")

    accessor = MemoryAccessor(enabled=False, store_factory=factory)
    assert not accessor.retrieve()
    assert not opened

    enabled = MemoryAccessor(enabled=True, store_factory=factory)
    with request_context(None, None):
        assert not enabled.retrieve()
    assert not opened


def test_retrieve_is_user_scoped_expiry_filtered_and_byte_bounded(
    tmp_path: Path,
) -> None:
    """Reads return a newest-first prefix under item and byte bounds."""
    store = MemoryStore(str(tmp_path / "memory.sqlite"))
    store.create(_write("old"), memory_id="old", now=_now(8))
    store.create(_write("newest"), memory_id="newest", now=_now(9))
    store.create(
        _write("expired"),
        memory_id="expired",
        now=_now(10),
    )
    store.update(
        "alice",
        "expired",
        MemoryWrite(
            user_id="alice",
            kind="preference",
            content="expired",
            expires_at=_now(10) + timedelta(minutes=1),
        ),
        expected_revision=1,
        now=_now(10),
    )
    accessor = MemoryAccessor(store, max_bytes=len(b"newest"))

    with request_context("alice", "req-1"):
        records = accessor.retrieve(limit=10, now=_now(11))
    assert [record.id for record in records] == ["newest"]
    assert not accessor.retrieve(user_id="bob")


def test_retrieve_context_can_inject_accessor_into_graph_runtime(
    tmp_path: Path,
) -> None:
    """The context override is isolated and resolver falls back safely."""
    accessor = MemoryAccessor(MemoryStore(str(tmp_path / "memory.sqlite")))
    with memory_accessor_context(accessor):
        assert current_memory_accessor() is accessor
        assert (
            resolve_memory_accessor({"memory_accessor": accessor}) is accessor
        )
        assert resolve_memory_accessor({}) is accessor
    assert current_memory_accessor() is not accessor


@pytest.mark.asyncio
async def test_ainvoke_graph_injects_memory_accessor_in_runtime_context() -> (
    None
):
    """Graph invocation carries the accessor outside persisted state."""

    class State(TypedDict):
        """Minimal state for the runtime injection graph."""

        seen: bool

    accessor = MemoryAccessor(enabled=False)

    async def node(
        state: State,
        runtime: Runtime[MemoryGraphContext],
    ) -> State:
        assert runtime.context.get("memory_accessor") is accessor
        return {"seen": state["seen"]}

    graph = StateGraph(State, context_schema=MemoryGraphContext)
    graph.add_node("node", node)
    graph.add_edge(START, "node")
    graph.add_edge("node", END)

    result = await ainvoke_graph(
        graph.compile(), {"seen": True}, memory_accessor=accessor
    )
    assert result == {"seen": True}


def test_store_failure_degrades_to_empty_without_raw_data_in_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Read failures are observable but do not expose namespace/content."""

    class BrokenStore(MemoryStore):
        """Store double that simulates a local SQLite outage."""

        def __init__(self) -> None:
            """Initialize an in-memory store for the accessor contract."""
            super().__init__(":memory:")

        def list(
            self,
            user_id: str,
            *,
            options: MemoryListOptions | None = None,
            **legacy: Any,
        ) -> list[MemoryRecord]:
            """Raise the storage error the accessor must redact."""
            _ = (user_id, options, legacy)
            raise OSError("backend unavailable")

    store = BrokenStore()
    try:
        accessor = MemoryAccessor(store)
        with request_context("alice", "req-2"):
            assert not accessor.retrieve()
        assert accessor.degraded_reads == 1
        assert "alice" not in caplog.text
        assert "backend unavailable" not in caplog.text
    finally:
        store.close()
