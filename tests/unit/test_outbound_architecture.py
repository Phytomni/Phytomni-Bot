# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Static ownership and privacy guards for outbound resource pooling."""

from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path
from typing import Literal

import httpx
import pytest
from mcp.shared.exceptions import McpError
from tests.support.outbound_pool_contracts import (
    RELAY_GENERIC_CALLS,
    RelayGenericCall,
)

from mcp_server_phytomni.common.http import (
    JsonPostRequest,
    JsonPostRetry,
    post_json_with_retries,
)
from mcp_server_phytomni.config.required_env import REQUIRED_OUTBOUND_FIELDS
from mcp_server_phytomni.runtime.outbound import (
    OutboundPoolName,
    OutboundPoolSnapshot,
    OutboundRuntimeClosedError,
)
from mcp_server_phytomni.runtime.outbound.http import BoundAsyncRequestClient
from mcp_server_phytomni.runtime.outbound.lifecycle import _CAPACITY_FIELDS
from mcp_server_phytomni.runtime.outbound.registry import OutboundPoolRegistry

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
_RELAY_CLIENT = _SOURCE_ROOT / "common" / "relay_client.py"
_PRIVATE_MARKERS = (
    "url-marker",
    "header-marker",
    "credential-marker",
    "body-marker",
    "prompt-marker",
    "query-marker",
    "obs-key-marker",
    "user-marker",
    "run-marker",
    "task-marker",
    "request-marker",
    "provider-body-marker",
    "exception-marker",
)


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


def _relay_client_methods() -> dict[str, ast.AsyncFunctionDef]:
    """Return async methods declared directly on ``RelayClient``."""
    relay_source = _RELAY_CLIENT.read_text(encoding="utf-8")
    relay_tree = ast.parse(relay_source, str(_RELAY_CLIENT))
    relay_class = next(
        node
        for node in relay_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "RelayClient"
    )
    return {
        node.name: node
        for node in relay_class.body
        if isinstance(node, ast.AsyncFunctionDef)
    }


def _relay_path_template(node: ast.expr) -> str:
    """Render one bounded relay path without caller-controlled values."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                assert isinstance(value.value, str)
                parts.append(value.value)
            else:
                assert isinstance(value, ast.FormattedValue)
                parts.append("{}")
        return "".join(parts)
    raise AssertionError("relay path must be a literal or bounded template")


def _contains_relay_client_factory(node: ast.AST) -> bool:
    """Return whether an expression obtains a concrete RelayClient."""
    factories = {"RelayClient", "build_relay_client", "current_relay_client"}
    return any(
        isinstance(child, ast.Call)
        and (
            isinstance(child.func, ast.Name)
            and child.func.id in factories
            or isinstance(child.func, ast.Attribute)
            and child.func.attr in factories
        )
        for child in ast.walk(node)
    )


def _scope_nodes(function: ast.AST) -> tuple[ast.AST, ...]:
    """Return nodes owned by one function, excluding nested definitions."""
    body = getattr(function, "body")
    pending = list(body)
    owned: list[ast.AST] = []
    while pending:
        node = pending.pop()
        owned.append(node)
        if isinstance(
            node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        pending.extend(ast.iter_child_nodes(node))
    return tuple(owned)


def _relay_aliases(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[str]:
    """Resolve local names built from or annotated as RelayClient."""
    arguments = (
        *function.args.posonlyargs,
        *function.args.args,
        *function.args.kwonlyargs,
    )
    aliases = {
        argument.arg
        for argument in arguments
        if argument.annotation is not None
        and ast.unparse(argument.annotation).rsplit(".", maxsplit=1)[-1]
        == "RelayClient"
    }
    assignments = [
        node
        for node in _scope_nodes(function)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]
    changed = True
    while changed:
        changed = False
        for assignment in assignments:
            value = assignment.value
            if value is None:
                continue
            names = {
                child.id
                for child in ast.walk(value)
                if isinstance(child, ast.Name)
            }
            if not (
                _contains_relay_client_factory(value)
                or names.intersection(aliases)
            ):
                continue
            targets = (
                assignment.targets
                if isinstance(assignment, ast.Assign)
                else [assignment.target]
            )
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in aliases:
                    aliases.add(target.id)
                    changed = True
    return aliases


def _generic_relay_calls(path: Path) -> set[RelayGenericCall]:
    """Extract only calls whose receiver is statically one RelayClient."""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    calls: set[RelayGenericCall] = set()
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or node.func.attr not in {"get_json", "post_json"}
        ):
            continue
        parent = parents.get(node)
        while parent is not None and not isinstance(
            parent, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            parent = parents.get(parent)
        assert isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef))
        classes: list[str] = []
        owner_node: ast.AST | None = parent
        while owner_node is not None:
            if isinstance(owner_node, ast.ClassDef):
                classes.append(owner_node.name)
            owner_node = parents.get(owner_node)
        owner = ".".join((*reversed(classes), parent.name))
        receiver = node.func.value
        aliases = _relay_aliases(parent)
        is_relay_receiver = (
            isinstance(receiver, ast.Call)
            and _contains_relay_client_factory(receiver)
            or isinstance(receiver, ast.Name)
            and receiver.id in aliases
            or isinstance(receiver, ast.Name)
            and receiver.id == "self"
            and classes == ["RelayClient"]
        )
        if not is_relay_receiver:
            continue
        assert node.args
        pool_values = [
            keyword.value for keyword in node.keywords if keyword.arg == "pool"
        ]
        assert len(pool_values) == 1, f"{path}:{node.lineno}"
        pool = pool_values[0]
        assert isinstance(pool, ast.Attribute), f"{path}:{node.lineno}"
        assert isinstance(pool.value, ast.Name), f"{path}:{node.lineno}"
        assert pool.value.id == "OutboundPoolName", f"{path}:{node.lineno}"
        method: Literal["get_json", "post_json"] = (
            "get_json" if node.func.attr == "get_json" else "post_json"
        )
        calls.add(
            RelayGenericCall(
                str(path.relative_to(_SOURCE_ROOT)),
                owner,
                method,
                _relay_path_template(node.args[0]),
                OutboundPoolName[pool.attr],
            )
        )
    return calls


def test_every_production_generic_relay_call_matches_typed_inventory() -> None:
    """All concrete RelayClient JSON calls have one exact owned mapping."""
    actual: set[RelayGenericCall] = set()
    for path in _production_sources():
        actual.update(_generic_relay_calls(path))
    assert len(RELAY_GENERIC_CALLS) == len(set(RELAY_GENERIC_CALLS))
    assert actual == set(RELAY_GENERIC_CALLS)


def test_relay_client_has_one_narrow_typed_pool_contract() -> None:
    """Generic relay JSON calls pass their required enum through unchanged."""
    methods = _relay_client_methods()
    assert "post_data" not in methods
    for method_name in ("post_json", "get_json"):
        method = methods[method_name]
        keyword_only = dict(
            zip(method.args.kwonlyargs, method.args.kw_defaults, strict=True)
        )
        pool_arg = next(arg for arg in keyword_only if arg.arg == "pool")
        assert keyword_only[pool_arg] is None
        assert pool_arg.annotation is not None
        assert ast.unparse(pool_arg.annotation) == "OutboundPoolName"
        options_arg = next(arg for arg in keyword_only if arg.arg == "options")
        assert keyword_only[options_arg] is None
        assert options_arg.annotation is not None
        assert ast.unparse(options_arg.annotation) == "RelayRequestOptions"

        request_calls = [
            node
            for node in ast.walk(method)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_request_json"
        ]
        assert len(request_calls) == 1
        pool_value = next(
            keyword.value
            for keyword in request_calls[0].keywords
            if keyword.arg == "pool"
        )
        assert isinstance(pool_value, ast.Name)
        assert pool_value.id == "pool"

        docstring = ast.get_docstring(method) or ""
        assert "Args:" in docstring
        assert "pool:" in docstring
        assert "options:" in docstring


def test_relay_client_never_infers_pool_from_path_or_method() -> None:
    """The typed relay client has no obsolete or inferred pool selector."""
    relay_tree = ast.parse(
        _RELAY_CLIENT.read_text(encoding="utf-8"), str(_RELAY_CLIENT)
    )
    assert "_relay_pool" not in {
        node.name
        for node in ast.walk(relay_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    request_method = _relay_client_methods()["_request_json"]
    for_pool_calls = [
        node
        for node in ast.walk(request_method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "for_pool"
    ]
    assert len(for_pool_calls) == 1
    assert len(for_pool_calls[0].args) == 1
    assert isinstance(for_pool_calls[0].args[0], ast.Name)
    assert for_pool_calls[0].args[0].id == "pool"


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


async def test_real_marked_attempt_keeps_pool_observability_and_errors_safe(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Request-controlled data never enters pool snapshots or pool logs."""
    capacities = {name: 0 for name in OutboundPoolName}
    capacities[OutboundPoolName.LLM] = 1
    pools = OutboundPoolRegistry(capacities, wait_warn_seconds=0.000001)

    def fail_attempt(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            request=request,
            content=b"provider-body-marker exception-marker",
        )

    profile = httpx.AsyncClient(transport=httpx.MockTransport(fail_attempt))
    client = BoundAsyncRequestClient(pools, OutboundPoolName.LLM, profile)
    request = JsonPostRequest(
        url="https://url-marker.invalid/query-marker",
        headers={
            "Authorization": "Bearer credential-marker",
            "X-Trace": "header-marker request-marker",
            "X-User": "user-marker",
            "X-Run": "run-marker",
            "X-Task": "task-marker",
        },
        json_body={
            "body": "body-marker",
            "prompt": "prompt-marker",
            "obs_key": "obs-key-marker",
        },
    )
    retry = JsonPostRetry(
        timeout=1.0,
        max_retries=0,
        retriable_codes=(),
        message="safe outbound failure",
    )
    caplog.set_level("WARNING", logger="mcp_server_phytomni.runtime.outbound")

    try:
        async with pools.lease(OutboundPoolName.LLM):
            attempt = asyncio.create_task(
                post_json_with_retries(client, request, retry)
            )
            for _ in range(100):
                if pools.snapshot(OutboundPoolName.LLM).waiting == 1:
                    break
                await asyncio.sleep(0)
            assert pools.snapshot(OutboundPoolName.LLM).waiting == 1
            await asyncio.sleep(0.001)

        with pytest.raises(McpError) as exc_info:
            await attempt

        snapshot_text = repr(pools.snapshot(OutboundPoolName.LLM))
        pool_log_text = "\n".join(
            record.getMessage()
            for record in caplog.records
            if record.name.startswith("mcp_server_phytomni.runtime.outbound")
        )
        public_error_text = f"{exc_info.value!s}\n{exc_info.value!r}"
        for marker in _PRIVATE_MARKERS:
            assert marker not in snapshot_text
            assert marker not in pool_log_text
            assert marker not in public_error_text

        await pools.aclose()
        with pytest.raises(OutboundRuntimeClosedError) as closed_error:
            async with pools.lease(OutboundPoolName.LLM):
                pytest.fail("a closed pool must reject the marked request")
        for marker in _PRIVATE_MARKERS:
            assert marker not in str(closed_error.value)
    finally:
        await profile.aclose()
