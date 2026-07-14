# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared helpers for invoking LangGraph applications.

Classes: GraphRegistry.
Functions: ensure_thread_id, build_runnable_config, ensure_checkpointer,
    ainvoke_graph, capture_workflow_boundary, config_fingerprint.
"""

import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from pydantic import SecretStr

from ..storage.path_policy import IdFactory
from .checkpoint_backend import build_default_checkpointer
from .memory import (
    MemoryAccessor,
    current_memory_accessor,
    memory_accessor_context,
)

# Workflow boundary: every non-system failure produced inside a LangGraph
# action must be converted to a structured failure state rather than
# propagating, otherwise a partial node failure tears down the parent
# dispatch graph (Send fan-out workers lose their per-task isolation).
# Bound through a module-level tuple so the `except` clause references a
# variable rather than the bare ``Exception`` class — that distinction is
# what carries the design intent through static analysis.
_WORKFLOW_CAPTURED_EXCEPTIONS: tuple[type[Exception], ...] = (Exception,)

SECRET_FIELD_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "access_key",
        "access_token",
        "auth_token",
        "bearer_token",
        "client_secret",
        "credential",
        "password",
        "secret",
        "secret_key",
        "token",
    }
)


def ensure_thread_id(thread_id: str | None = None) -> str:
    """Return an existing thread id or create a new one.

    Args:
        thread_id: Existing thread ID to reuse, or None to generate new.

    Returns:
        str: Existing thread_id or newly generated ID.
    """
    if thread_id:
        return thread_id
    return IdFactory().new_id("thread")


def build_runnable_config(thread_id: str | None = None) -> RunnableConfig:
    """Build the LangGraph RunnableConfig used for checkpointing.

    Args:
        thread_id: Optional thread ID for conversation continuity.

    Returns:
        RunnableConfig: Configuration dict with thread_id in configurable.
    """
    config: RunnableConfig = {
        "configurable": {"thread_id": ensure_thread_id(thread_id)}
    }
    return config


def ensure_checkpointer(
    checkpointer: BaseCheckpointSaver | None = None,
) -> BaseCheckpointSaver:
    """Return a caller-provided checkpointer or the SQLite default.

    Tests inject a ``MemorySaver`` to stay offline; production callers
    pass ``None`` and receive the persistent ``AsyncSqliteSaver`` built
    by :func:`build_default_checkpointer`, so a graph pause point
    survives a process restart. Under ``PHYTOMNI_TESTING=1`` (root
    conftest), always fall back to ``MemorySaver`` even when an async
    event loop is running — otherwise agent constructors that omit an
    explicit checkpointer open unclosed ``aiosqlite`` connections and
    poison the suite with ``PytestUnraisableExceptionWarning``. When
    no async event loop is running outside tests, also fall back to
    ``MemorySaver`` so sync fixtures stay offline without hitting the
    SQLite backend's loop requirement.

    Args:
        checkpointer: Optional existing checkpointer to reuse.

    Returns:
        The provided checkpointer, or a fresh SQLite-backed default
        (``MemorySaver`` under testing / outside an async context).
    """
    if checkpointer is not None:
        return checkpointer
    if os.environ.get("PHYTOMNI_TESTING") == "1":
        return MemorySaver()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return MemorySaver()
    return build_default_checkpointer()


def make_async_router(
    router: Callable[[Any], Any],
) -> Callable[[Any], Awaitable[Any]]:
    """Adapt a synchronous conditional router for ``ainvoke`` graphs."""

    async def _router(state: Any) -> Any:
        return router(state)

    return _router


async def ainvoke_graph(
    app: Any,
    initial_state: Any,
    thread_id: str | None = None,
    memory_accessor: MemoryAccessor | None = None,
) -> Any:
    """Invoke a compiled graph with a standard RunnableConfig.

    Args:
        app: Compiled LangGraph application.
        initial_state: Initial state dict to pass to the graph.
        thread_id: Optional thread ID for checkpointing continuity.
        memory_accessor: Optional graph-facing accessor.  When omitted, the
            configured lazy accessor is injected at runtime.

    Returns:
        Any: Final state after graph invocation.
    """
    accessor = memory_accessor or current_memory_accessor()
    with memory_accessor_context(accessor):
        invoke_kwargs: dict[str, Any] = {
            "config": build_runnable_config(thread_id),
        }
        if getattr(app, "context_schema", None) is not None:
            invoke_kwargs["context"] = {"memory_accessor": accessor}
        return await app.ainvoke(
            initial_state,
            **invoke_kwargs,
        )


async def capture_workflow_boundary(
    action: Callable[[], Awaitable[dict[str, Any]]],
    failure_result: Callable[[Exception], dict[str, Any]],
) -> dict[str, Any]:
    """Run a workflow action and convert graph/node failures to state.

    Args:
        action: Async callable that executes the workflow.
        failure_result: Callable that converts exceptions
            to failure state dict.

    Returns:
        dict[str, Any]: Workflow result on success,
            or failure state on exception.
    """
    try:
        return await action()
    except _WORKFLOW_CAPTURED_EXCEPTIONS as exc:
        return failure_result(exc)


def config_fingerprint(values: Mapping[str, Any] | None = None) -> str:
    """Return a stable fingerprint for non-secret config values.

    Args:
        values: Mapping of configuration values to fingerprint.

    Returns:
        str: JSON string fingerprint with secrets stripped.
    """
    safe_values = _strip_secret_values(values or {})
    return json.dumps(safe_values, sort_keys=True, separators=(",", ":"))


@dataclass
class GraphRegistry[GraphT]:
    """Cache compiled graphs by an explicit non-secret fingerprint.

    Attributes:
        _graphs: Mapping of graph names and fingerprints to compiled graphs.
    """

    _graphs: dict[tuple[str, str], GraphT] = field(default_factory=dict)

    def get_or_create(
        self,
        name: str,
        factory: Callable[[], GraphT],
        fingerprint_values: Mapping[str, Any] | None = None,
    ) -> GraphT:
        """Return a cached graph or create one for this fingerprint.

        Args:
            name: Graph name identifier.
            factory: Callable that creates a new GraphT instance.
            fingerprint_values: Optional config values for cache key.

        Returns:
            GraphT: Cached or newly created graph instance.
        """
        key = (name, config_fingerprint(fingerprint_values))
        if key not in self._graphs:
            self._graphs[key] = factory()
        return self._graphs[key]

    def clear(self, name: str | None = None) -> None:
        """Clear all cached graphs, or only entries for one graph name.

        Args:
            name: Optional graph name to clear. When omitted, all entries are
                removed.

        Returns:
            None. Matching cached graphs are removed from the registry.
        """
        if name is None:
            self._graphs.clear()
            return

        for key in list(self._graphs):
            if key[0] == name:
                del self._graphs[key]

    def count(self) -> int:
        """Return the number of cached graph entries.

        Returns:
            Number of graph entries currently stored in the registry.
        """
        return len(self._graphs)


def _strip_secret_values(value: Any) -> Any:
    """Remove secret values before a config is used as a cache key."""
    if isinstance(value, SecretStr):
        return "<secret>"
    if isinstance(value, Mapping):
        return {
            str(key): _strip_secret_values(item)
            for key, item in value.items()
            if not _is_secret_field(str(key))
        }
    if isinstance(value, list):
        return [_strip_secret_values(item) for item in value]
    if isinstance(value, tuple):
        return [_strip_secret_values(item) for item in value]
    if isinstance(value, set):
        return sorted(_strip_secret_values(item) for item in value)
    return value


def _is_secret_field(name: str) -> bool:
    lowered = name.lower()
    return lowered in SECRET_FIELD_NAMES or any(
        marker in lowered
        for marker in ("password", "secret", "token", "api_key")
    )
