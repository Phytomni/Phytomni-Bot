# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared registry for reusable agent instances.

Classes: GraphRegistry (via langgraph_runner).
Functions: get_cached_agent, clear_agent_registry, agent_fingerprint_values.
"""

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel

from .langgraph_runner import GraphRegistry

_AGENT_REGISTRY: GraphRegistry[Any] = GraphRegistry()


def get_cached_agent[AgentT](
    name: str,
    factory: Callable[[], AgentT],
    fingerprint_values: Mapping[str, Any] | None = None,
) -> AgentT:
    """Return a cached agent for an explicit non-secret fingerprint.

    Args:
        name: Agent name identifier.
        factory: Callable that creates a new agent instance.
        fingerprint_values: Optional config values for cache key.

    Returns:
        AgentT: Cached or newly created agent instance.
    """
    return _AGENT_REGISTRY.get_or_create(name, factory, fingerprint_values)


def clear_agent_registry(name: str | None = None) -> None:
    """Clear cached agents, optionally only one agent name.

    Args:
        name: Optional agent name to clear (clears all if None).

    Returns:
        None. Matching cached agents are removed from the registry.
    """
    _AGENT_REGISTRY.clear(name)


def agent_fingerprint_values(**values: Any) -> dict[str, Any]:
    """Build registry fingerprint values from configs and plain values.

    Args:
        **values: Keyword config values to include in fingerprint.

    Returns:
        dict[str, Any]: Processed values with BaseModel objects dumped to dict.
    """
    return {key: _dump_value(value) for key, value in values.items()}


def _dump_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _dump_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_dump_value(item) for item in value]
    if isinstance(value, tuple):
        return [_dump_value(item) for item in value]
    return value
