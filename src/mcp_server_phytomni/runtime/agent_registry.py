# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared registry for reusable agent instances."""

from collections.abc import Callable, Mapping
from typing import Any, Optional, TypeVar

from pydantic import BaseModel

from .langgraph_runner import GraphRegistry

AgentT = TypeVar("AgentT")

_AGENT_REGISTRY: GraphRegistry[Any] = GraphRegistry()


def get_cached_agent(
    name: str,
    factory: Callable[[], AgentT],
    fingerprint_values: Optional[Mapping[str, Any]] = None,
) -> AgentT:
    """Return a cached agent for an explicit non-secret fingerprint."""
    return _AGENT_REGISTRY.get_or_create(name, factory, fingerprint_values)


def clear_agent_registry(name: Optional[str] = None) -> None:
    """Clear cached agents, optionally only one agent name."""
    _AGENT_REGISTRY.clear(name)


def agent_fingerprint_values(**values: Any) -> dict[str, Any]:
    """Build registry fingerprint values from configs and plain values."""
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
