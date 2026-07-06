# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared helpers for invoking LangGraph applications.

Classes: GraphRegistry.
Functions: ensure_thread_id, build_runnable_config, ensure_checkpointer,
    ainvoke_graph, capture_workflow_boundary, config_fingerprint.
"""

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from pydantic import SecretStr

from ..storage.path_policy import IdFactory

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
    checkpointer: MemorySaver | None = None,
) -> MemorySaver:
    """Return a caller-provided checkpointer or create a fresh one.

    Args:
        checkpointer: Optional existing MemorySaver to reuse.

    Returns:
        MemorySaver: Provided checkpointer or newly created instance.
    """
    if checkpointer is not None:
        return checkpointer
    return MemorySaver()


async def ainvoke_graph(
    app: Any,
    initial_state: Any,
    thread_id: str | None = None,
) -> Any:
    """Invoke a compiled graph with a standard RunnableConfig.

    Args:
        app: Compiled LangGraph application.
        initial_state: Initial state dict to pass to the graph.
        thread_id: Optional thread ID for checkpointing continuity.

    Returns:
        Any: Final state after graph invocation.
    """
    return await app.ainvoke(
        initial_state,
        config=build_runnable_config(thread_id),
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
