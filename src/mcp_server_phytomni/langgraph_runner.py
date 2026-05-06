# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared helpers for invoking LangGraph applications."""

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Generic, Optional, Protocol, TypeVar
from uuid import uuid1

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from pydantic import SecretStr

ResultT_co = TypeVar("ResultT_co", covariant=True)
GraphT = TypeVar("GraphT")

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


class AsyncGraph(Protocol[ResultT_co]):
    """Protocol for the async subset used from compiled LangGraph apps."""

    def ainvoke(self, *args: Any, **kwargs: Any) -> Awaitable[ResultT_co]:
        """Invoke a compiled graph asynchronously."""
        raise NotImplementedError


def ensure_thread_id(thread_id: Optional[str] = None) -> str:
    """Return an existing thread id or create a new one."""
    if thread_id:
        return thread_id
    return str(uuid1())


def build_runnable_config(thread_id: Optional[str] = None) -> RunnableConfig:
    """Build the LangGraph RunnableConfig used for checkpointing."""
    config: RunnableConfig = {
        "configurable": {"thread_id": ensure_thread_id(thread_id)}
    }
    return config


def ensure_checkpointer(
    checkpointer: Optional[MemorySaver] = None,
) -> MemorySaver:
    """Return a caller-provided checkpointer or create a fresh one."""
    if checkpointer is not None:
        return checkpointer
    return MemorySaver()


async def ainvoke_graph(
    app: AsyncGraph[Any],
    initial_state: Any,
    thread_id: Optional[str] = None,
) -> Any:
    """Invoke a compiled graph with a standard RunnableConfig."""
    return await app.ainvoke(
        initial_state,
        config=build_runnable_config(thread_id),
    )


def config_fingerprint(values: Optional[Mapping[str, Any]] = None) -> str:
    """Return a stable fingerprint for non-secret config values."""
    safe_values = _strip_secret_values(values or {})
    return json.dumps(safe_values, sort_keys=True, separators=(",", ":"))


@dataclass
class GraphRegistry(Generic[GraphT]):
    """Cache compiled graphs by an explicit non-secret fingerprint."""

    _graphs: dict[tuple[str, str], GraphT] = field(default_factory=dict)

    def get_or_create(
        self,
        name: str,
        factory: Callable[[], GraphT],
        fingerprint_values: Optional[Mapping[str, Any]] = None,
    ) -> GraphT:
        """Return a cached graph or create one for this fingerprint."""
        key = (name, config_fingerprint(fingerprint_values))
        if key not in self._graphs:
            self._graphs[key] = factory()
        return self._graphs[key]

    def clear(self, name: Optional[str] = None) -> None:
        """Clear all cached graphs, or only entries for one graph name."""
        if name is None:
            self._graphs.clear()
            return

        for key in list(self._graphs):
            if key[0] == name:
                del self._graphs[key]

    def count(self) -> int:
        """Return the number of cached graph entries."""
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
