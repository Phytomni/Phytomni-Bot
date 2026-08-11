# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Static ownership and privacy guards for outbound resource pooling."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from mcp_server_phytomni.config.required_env import REQUIRED_OUTBOUND_FIELDS
from mcp_server_phytomni.runtime.outbound import (
    OutboundPoolName,
    OutboundPoolSnapshot,
)
from mcp_server_phytomni.runtime.outbound.lifecycle import _CAPACITY_FIELDS

pytestmark = pytest.mark.unit

_SOURCE_ROOT = Path(__file__).parents[2] / "src" / "mcp_server_phytomni"
_HTTP_OWNER = _SOURCE_ROOT / "runtime" / "outbound" / "http.py"
_LIFECYCLE_OWNER = _SOURCE_ROOT / "runtime" / "outbound" / "lifecycle.py"
_INTEROP_OWNER = _SOURCE_ROOT / "interop" / "http_transport.py"
_INTEROP_RUNTIME_OWNER = _SOURCE_ROOT / "interop" / "runtime.py"
_OBS_OWNER = _SOURCE_ROOT / "runtime" / "outbound" / "obs.py"
_GAUSS_OWNER = _SOURCE_ROOT / "agents" / "shared" / "gauss.py"
_POOL_VALUE_TYPES = _SOURCE_ROOT / "runtime" / "outbound" / "models.py"
_POOL_REGISTRY = _SOURCE_ROOT / "runtime" / "outbound" / "registry.py"


def _production_sources() -> tuple[Path, ...]:
    """Return every server production Python source file."""
    return tuple(_SOURCE_ROOT.rglob("*.py"))


def test_pool_enum_and_capacity_map_are_one_to_one() -> None:
    """Every typed pool has exactly one required configuration field."""
    assert set(_CAPACITY_FIELDS) == set(OutboundPoolName)
    assert len(_CAPACITY_FIELDS) == len(OutboundPoolName)
    assert len(set(_CAPACITY_FIELDS.values())) == len(_CAPACITY_FIELDS)
    assert set(REQUIRED_OUTBOUND_FIELDS) == {
        *_CAPACITY_FIELDS.values(),
        "OUTBOUND_POOL_WAIT_WARN_SECONDS",
    }


def test_native_pool_and_resource_constructors_have_one_owner() -> None:
    """Every persistent outbound constructor belongs to a fixed owner."""
    allowed_async_client_calls = {_LIFECYCLE_OWNER, _INTEROP_OWNER}
    allowed_asyncpg_calls = {_GAUSS_OWNER}
    obs_import_owners = {_LIFECYCLE_OWNER, _OBS_OWNER}
    interop_session_owners = {_INTEROP_RUNTIME_OWNER}
    for path in _production_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func_name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id if isinstance(node.func, ast.Name) else None
            )
            if func_name in {"AsyncClient", "_InteropAsyncClient"}:
                assert path in allowed_async_client_calls
            if func_name == "create_pool":
                assert path in allowed_asyncpg_calls
        source = path.read_text(encoding="utf-8")
        if "from ...storage.obs_client import ObsClient" in source:
            assert path in obs_import_owners
        if any(
            marker in source
            for marker in (
                "from mcp import ClientSession",
                "from mcp.client.stdio import",
                "from mcp.client.streamable_http import",
            )
        ):
            assert path in interop_session_owners


def test_legacy_transport_and_rerank_paths_are_absent() -> None:
    """Removed per-request and legacy rerank controls stay removed."""
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in _production_sources()
    )
    for marker in (
        "get_async_client",
        "init_shared_client",
        "aclose_shared_client",
        "for_direct_upstream",
    ):
        assert marker not in source
    assert not re.search(r"(?<!OUTBOUND_)RERANK_CONCURRENCY", source)


def test_pool_value_types_and_registry_need_no_inline_waivers() -> None:
    """Core pool structures stay within the default static policy."""
    for path in (_POOL_VALUE_TYPES, _POOL_REGISTRY):
        assert "pylint: disable" not in path.read_text(encoding="utf-8")


def test_httpx_and_openai_constructors_have_one_owner() -> None:
    """Persistent HTTPX/OpenAI construction is confined to runtime owners."""
    for path in _production_sources():
        source = path.read_text(encoding="utf-8")
        if "httpx.AsyncClient(" in source:
            assert path in {_HTTP_OWNER, _LIFECYCLE_OWNER, _INTEROP_OWNER}
        if "AsyncOpenAI" in source:
            assert path == _LIFECYCLE_OWNER


def test_pool_binding_never_accepts_literal_or_url_derived_names() -> None:
    """All production pool bindings use typed enum members at the call site."""
    for path in _production_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "for_pool" or not node.args:
                continue
            assert not isinstance(node.args[0], ast.Constant)
            assert not (
                isinstance(node.args[0], ast.Call)
                and isinstance(node.args[0].func, ast.Name)
                and node.args[0].func.id in {"str", "urlparse"}
            )


def test_snapshot_repr_contains_only_fixed_observability_fields() -> None:
    """Pool snapshots cannot retain caller-controlled markers."""
    snapshot = OutboundPoolSnapshot(
        name=OutboundPoolName.LLM,
        capacity=1,
        in_use=0,
        waiting=0,
        max_in_use=1,
        started=2,
        completed=1,
        failed=1,
        cancelled=0,
        total_wait_seconds=0.25,
        max_wait_seconds=0.25,
    )
    rendered = repr(snapshot)
    assert "https://marker.invalid" not in rendered
    assert "secret-token-marker" not in rendered
    assert "prompt-marker" not in rendered
    assert "url" not in rendered
