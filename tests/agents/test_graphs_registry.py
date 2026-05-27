# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for ``mcp_server_phytomni.graphs.registry.SubgraphRegistry``.

These tests exercise the (id, fingerprint) cache contract and the
clear / get_spec / names surface. The factory returns plain ``object``
instances so cache hits and misses are verified by Python identity,
matching how compiled LangGraph apps are reused at runtime.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_server_phytomni.graphs import SubgraphRegistry, SubgraphSpec


def _counting_factory(counter: dict[str, int], key: str) -> Any:
    """Return a factory that increments ``counter[key]`` on each call."""

    def factory() -> object:
        counter[key] = counter.get(key, 0) + 1
        return object()  # New identity per call; identity verifies caching.

    return factory


def test_register_rejects_duplicate_id() -> None:
    """Registering the same id twice raises ``ValueError``."""
    registry = SubgraphRegistry()
    counter: dict[str, int] = {}
    spec = SubgraphSpec(id="chat", factory=_counting_factory(counter, "chat"))
    registry.register(spec)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(spec)


def test_cache_hit_same_fingerprint() -> None:
    """Same id + identical fingerprint returns the same compiled object."""
    registry = SubgraphRegistry()
    counter: dict[str, int] = {}
    registry.register(
        SubgraphSpec(id="chat", factory=_counting_factory(counter, "chat")),
    )

    first = registry.get_or_compile("chat", fingerprint_values={"model": "x"})
    second = registry.get_or_compile("chat", fingerprint_values={"model": "x"})
    assert first is second
    assert counter["chat"] == 1


def test_cache_miss_different_fingerprint() -> None:
    """Different fingerprint values yield a new compiled object."""
    registry = SubgraphRegistry()
    counter: dict[str, int] = {}
    registry.register(
        SubgraphSpec(id="chat", factory=_counting_factory(counter, "chat")),
    )

    first = registry.get_or_compile("chat", fingerprint_values={"model": "x"})
    second = registry.get_or_compile("chat", fingerprint_values={"model": "y"})
    assert first is not second
    assert counter["chat"] == 2


def test_get_or_compile_unknown_id_raises() -> None:
    """Querying an unregistered id raises ``KeyError``."""
    registry = SubgraphRegistry()
    with pytest.raises(KeyError, match="unknown subgraph"):
        registry.get_or_compile("missing")


def test_clear_by_id_only_drops_that_id() -> None:
    """``clear(id)`` removes one id's cache while leaving siblings intact."""
    registry = SubgraphRegistry()
    chat_counter: dict[str, int] = {}
    knowledge_counter: dict[str, int] = {}
    registry.register(
        SubgraphSpec(
            id="chat",
            factory=_counting_factory(chat_counter, "c"),
        ),
    )
    registry.register(
        SubgraphSpec(
            id="knowledge",
            factory=_counting_factory(knowledge_counter, "k"),
        ),
    )

    chat_first = registry.get_or_compile(
        "chat",
        fingerprint_values={"model": 1},
    )
    knowledge_first = registry.get_or_compile(
        "knowledge",
        fingerprint_values={"model": 1},
    )
    registry.clear("chat")
    chat_second = registry.get_or_compile(
        "chat",
        fingerprint_values={"model": 1},
    )
    knowledge_second = registry.get_or_compile(
        "knowledge",
        fingerprint_values={"model": 1},
    )

    assert chat_first is not chat_second  # Recompiled after clear.
    assert knowledge_first is knowledge_second  # Untouched.
    assert chat_counter["c"] == 2
    assert knowledge_counter["k"] == 1


def test_clear_all_drops_every_entry() -> None:
    """``clear()`` with no args drops every cached entry."""
    registry = SubgraphRegistry()
    counter: dict[str, int] = {}
    registry.register(
        SubgraphSpec(id="chat", factory=_counting_factory(counter, "c")),
    )

    registry.get_or_compile("chat", fingerprint_values={"model": 1})
    registry.clear()
    registry.get_or_compile("chat", fingerprint_values={"model": 1})

    assert counter["c"] == 2


def test_names_returns_sorted_ids() -> None:
    """``names()`` lists registered ids in alphabetical order."""
    registry = SubgraphRegistry()

    def noop() -> None:
        return None

    registry.register(SubgraphSpec(id="zeta", factory=noop))
    registry.register(SubgraphSpec(id="alpha", factory=noop))
    registry.register(SubgraphSpec(id="mu", factory=noop))

    assert registry.names() == ("alpha", "mu", "zeta")


def test_spec_default_fingerprint_used_when_values_omitted() -> None:
    """When ``fingerprint_values=None``, the spec's defaults are applied."""
    registry = SubgraphRegistry()
    counter: dict[str, int] = {}
    registry.register(
        SubgraphSpec(
            id="chat",
            factory=_counting_factory(counter, "c"),
            fingerprint_fields={"model": "default"},
        ),
    )

    first = registry.get_or_compile("chat")
    second = registry.get_or_compile("chat")
    assert first is second
    assert counter["c"] == 1


def test_get_spec_returns_registered_instance() -> None:
    """``get_spec`` returns the same spec object that was registered."""
    registry = SubgraphRegistry()

    def noop() -> None:
        return None

    registered = SubgraphSpec(id="chat", factory=noop)
    registry.register(registered)
    assert registry.get_spec("chat") is registered


def test_get_spec_unknown_id_raises() -> None:
    """``get_spec`` on an unregistered id raises ``KeyError``."""
    registry = SubgraphRegistry()
    with pytest.raises(KeyError, match="unknown subgraph"):
        registry.get_spec("missing")


def test_spec_carries_optional_schema_fields() -> None:
    """``SubgraphSpec`` stores informational state / input / output schemas."""

    def noop() -> None:
        return None

    spec = SubgraphSpec(
        id="chat",
        factory=noop,
        state_schema=str,
        input_schema=int,
        output_schema=bytes,
    )
    assert spec.state_schema is str
    assert spec.input_schema is int
    assert spec.output_schema is bytes
