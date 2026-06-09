# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the declarative graph manifest loader.

Covers four contracts: default-off construction raises so the
surface is inert; flag-on construction round-trips every committed
``graphs/manifests/*.graph.json`` snapshot; a subgraph node
referencing a non-allowlist id raises validation; and the exported
schema file stays aligned with the Pydantic source-of-truth.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server_phytomni.config.defaults import ServerConfig
from mcp_server_phytomni.graphs.allowlist import (
    default_subgraph_allowlist,
)
from mcp_server_phytomni.graphs.defaults import build_default_registry
from mcp_server_phytomni.graphs.loader import (
    GraphLoader,
    GraphLoaderDisabledError,
    GraphLoaderValidationError,
)
from mcp_server_phytomni.graphs.manifest import GraphManifest
from mcp_server_phytomni.graphs.schema import GRAPH_MANIFEST_SCHEMA_PATH

MANIFESTS_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "mcp_server_phytomni"
    / "graphs"
    / "manifests"
)


def _enabled_config() -> ServerConfig:
    """Return a ServerConfig with the loader flag flipped on."""
    return ServerConfig(GRAPH_LOADER_ENABLED=True)


def test_loader_disabled_by_default() -> None:
    """Default-off ``ServerConfig`` makes ``GraphLoader()`` raise.

    Pins the production-safety contract: importing the loader module
    does not enable anything; only an explicit flag-on config (env or
    constructor override) unblocks construction.
    """
    assert ServerConfig().GRAPH_LOADER_ENABLED is False
    with pytest.raises(GraphLoaderDisabledError):
        GraphLoader()


def test_loader_constructs_when_flag_on() -> None:
    """Flag-on config produces a loader carrying the default allowlist."""
    loader = GraphLoader(config=_enabled_config())
    assert loader.allowlist == default_subgraph_allowlist()


@pytest.mark.parametrize(
    "manifest_path",
    sorted(MANIFESTS_DIR.glob("*.graph.json")),
    ids=lambda p: p.stem,
)
def test_loader_round_trips_every_committed_manifest(
    manifest_path: Path,
) -> None:
    """Every committed manifest snapshot loads and round-trips identically.

    Loads each ``graphs/manifests/<agent>.graph.json`` through the
    enabled loader and asserts the resulting ``GraphManifest``
    serializes back to a payload structurally equal to the on-disk
    JSON. The structural equality check excludes whitespace and key
    order so the loader stays compatible with both the snapshot's
    historic indent=2 form and any future canonicalization that
    sorts keys.
    """
    loader = GraphLoader(config=_enabled_config())
    manifest = loader.load(manifest_path)
    expected = json.loads(manifest_path.read_text(encoding="utf-8"))
    # ``mode='json'`` coerces the model's tuple fields back to JSON
    # arrays so the round-trip equals the on-disk shape.
    round_trip = manifest.model_dump(mode="json")
    assert round_trip == expected


def test_loader_rejects_subgraph_outside_allowlist(
    tmp_path: Path,
) -> None:
    """A subgraph node with a non-allowlist id raises validation.

    Pins the safety constraint: even a structurally valid manifest
    (the JSON parses, the Pydantic shape matches) must fail when its
    ``subgraph`` nodes reference an id outside the registered set.
    """
    bogus = {
        "nodes": [
            {"name": "__start__", "kind": "boundary"},
            {"name": "definitely_not_a_real_subgraph", "kind": "subgraph"},
            {"name": "__end__", "kind": "boundary"},
        ],
        "edges": [
            {
                "source": "__start__",
                "target": "definitely_not_a_real_subgraph",
                "conditional": False,
            },
            {
                "source": "definitely_not_a_real_subgraph",
                "target": "__end__",
                "conditional": False,
            },
        ],
    }
    bogus_path = tmp_path / "bogus.graph.json"
    bogus_path.write_text(json.dumps(bogus), encoding="utf-8")
    loader = GraphLoader(config=_enabled_config())
    with pytest.raises(GraphLoaderValidationError) as exc_info:
        loader.load(bogus_path)
    assert "definitely_not_a_real_subgraph" in str(exc_info.value)


def test_loader_rejects_schema_violation(tmp_path: Path) -> None:
    """Malformed manifest (missing required field) raises validation.

    Pins the schema check: a node missing its required ``name`` falls
    through Pydantic's ``model_validate_json`` and gets re-raised as
    a ``GraphLoaderValidationError`` carrying the offending path.
    """
    malformed = {"nodes": [{"kind": "node"}], "edges": []}
    bad_path = tmp_path / "bad.graph.json"
    bad_path.write_text(json.dumps(malformed), encoding="utf-8")
    loader = GraphLoader(config=_enabled_config())
    with pytest.raises(GraphLoaderValidationError):
        loader.load(bad_path)


def test_exported_schema_matches_pydantic_model() -> None:
    """The on-disk schema file stays aligned with the Pydantic model.

    Pins the documentation/runtime drift guard: tooling that consumes
    ``graph_manifest.schema.json`` (LLM prompt assemblers, future
    declarative graph editors, jsonschema-based validators) reads the
    same shape the loader enforces at runtime via Pydantic.
    """
    exported = json.loads(
        GRAPH_MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8")
    )
    generated = GraphManifest.model_json_schema()
    # Strip the wrapper-only metadata before comparing payload shape.
    for extra_key in ("$schema", "$id"):
        exported.pop(extra_key, None)
    assert exported == generated


def test_allowlist_matches_default_registry() -> None:
    """``default_subgraph_allowlist`` mirrors the default registry.

    Pins the single-source-of-truth contract: any future agent that
    lands in ``build_default_registry()`` becomes loader-allowed
    without a parallel catalog edit.
    """
    assert default_subgraph_allowlist() == frozenset(
        build_default_registry().names()
    )
