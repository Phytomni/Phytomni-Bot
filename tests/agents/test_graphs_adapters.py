# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for ``mcp_server_phytomni.graphs.adapters.adapter_node``.

A minimal fake compiled subgraph stands in for ``CompiledStateGraph``:
it exposes ``ainvoke`` returning a preset mapping and records the
input it received, so the tests verify the ``map_in`` / ``map_out``
contract without compiling a real LangGraph.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.graphs.adapters import adapter_node


def _make_fake_subgraph(output: Mapping[str, Any]) -> SimpleNamespace:
    """Return a ``SimpleNamespace`` that mimics a compiled LangGraph app.

    The namespace exposes an ``ainvoke`` coroutine returning the preset
    output and a ``last_input`` slot updated on each call so tests can
    assert what reached the subgraph boundary. SimpleNamespace mirrors
    the test-fake pattern set by ``tests/agents/_analyst_fakes.py`` and
    avoids the R0903 too-few-public-methods ratchet that plain stub
    classes would trip.
    """
    namespace = SimpleNamespace(output=output, last_input=None)

    async def ainvoke(sub_input: Mapping[str, Any]) -> dict[str, Any]:
        namespace.last_input = sub_input
        return dict(namespace.output)

    namespace.ainvoke = ainvoke
    return namespace


@pytest.mark.asyncio
async def test_map_in_output_reaches_subgraph_ainvoke() -> None:
    """``map_in`` result is forwarded to ``compiled_subgraph.ainvoke``."""
    subgraph = _make_fake_subgraph(output={"sub_out": "value"})

    def map_in(state: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"sub_in": state["parent_field"]}

    def map_out(sub_output: Mapping[str, Any]) -> dict[str, Any]:
        return {"parent_back": sub_output["sub_out"]}

    node = adapter_node(map_in, subgraph, map_out)
    result = await node({"parent_field": 42, "irrelevant": "skipped"})

    assert subgraph.last_input == {"sub_in": 42}
    assert result == {"parent_back": "value"}


@pytest.mark.asyncio
async def test_map_out_only_returns_declared_parent_fields() -> None:
    """The adapter returns exactly what ``map_out`` produced."""
    subgraph = _make_fake_subgraph(
        output={
            "internal_field_a": 1,
            "internal_field_b": 2,
            "public_field": "x",
        },
    )

    def map_in(_state: Mapping[str, Any]) -> Mapping[str, Any]:
        return {}

    def map_out(sub_output: Mapping[str, Any]) -> dict[str, Any]:
        # Only forward one of the three subgraph output fields.
        return {"parent_public": sub_output["public_field"]}

    node = adapter_node(map_in, subgraph, map_out)
    result = await node({})

    assert result == {"parent_public": "x"}


@pytest.mark.asyncio
async def test_map_in_exception_propagates_unchanged() -> None:
    """Exceptions raised inside ``map_in`` are not swallowed."""
    subgraph = _make_fake_subgraph(output={})

    def map_in(_state: Mapping[str, Any]) -> Mapping[str, Any]:
        raise ValueError("map_in failed")

    def map_out(_sub_output: Mapping[str, Any]) -> dict[str, Any]:
        return {}

    node = adapter_node(map_in, subgraph, map_out)
    with pytest.raises(ValueError, match="map_in failed"):
        await node({})
    assert subgraph.last_input is None  # ainvoke never reached.


@pytest.mark.asyncio
async def test_subgraph_exception_propagates_unchanged() -> None:
    """Exceptions raised by the subgraph propagate to the parent."""

    async def raising_ainvoke(_sub_input: Mapping[str, Any]) -> None:
        raise RuntimeError("subgraph failed")

    raising_subgraph = SimpleNamespace(ainvoke=raising_ainvoke)

    def map_in(_state: Mapping[str, Any]) -> Mapping[str, Any]:
        return {}

    def map_out(_sub_output: Mapping[str, Any]) -> dict[str, Any]:
        return {}

    node = adapter_node(map_in, raising_subgraph, map_out)
    with pytest.raises(RuntimeError, match="subgraph failed"):
        await node({})


@pytest.mark.asyncio
async def test_map_out_exception_propagates_unchanged() -> None:
    """Exceptions raised inside ``map_out`` are not swallowed."""
    subgraph = _make_fake_subgraph(output={"x": 1})

    def map_in(_state: Mapping[str, Any]) -> Mapping[str, Any]:
        return {}

    def map_out(_sub_output: Mapping[str, Any]) -> dict[str, Any]:
        raise KeyError("map_out failed")

    node = adapter_node(map_in, subgraph, map_out)
    with pytest.raises(KeyError, match="map_out failed"):
        await node({})
    assert subgraph.last_input == {}  # ainvoke ran before map_out failed.


@pytest.mark.asyncio
async def test_adapter_is_pure_per_call() -> None:
    """Each invocation runs the full map_in/ainvoke/map_out chain afresh."""
    subgraph = _make_fake_subgraph(output={"out": "v"})
    seen_states: list[Mapping[str, Any]] = []

    def map_in(state: Mapping[str, Any]) -> Mapping[str, Any]:
        seen_states.append(state)
        return {"sub_in": state["n"]}

    def map_out(sub_output: Mapping[str, Any]) -> dict[str, Any]:
        return {"echo": sub_output["out"]}

    node = adapter_node(map_in, subgraph, map_out)
    first = await node({"n": 1})
    second = await node({"n": 2})

    assert first == {"echo": "v"}
    assert second == {"echo": "v"}
    assert [state["n"] for state in seen_states] == [1, 2]
    assert subgraph.last_input == {"sub_in": 2}
