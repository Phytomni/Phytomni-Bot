# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Exhaustive static inventory for outbound resource ownership."""

from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path
from typing import Literal

import pytest
from tests.support.outbound_operation_inventory import (
    _EXPECTED_NETWORK_OPERATIONS,
    _EXPECTED_NON_NETWORK_OPERATIONS,
    _NETWORK_OPERATION_DISPOSITIONS,
)
from tests.support.outbound_pool_contracts import (
    OPERATOR_FORWARD_ROUTES,
    OPERATOR_SERVER_TERMINATED_ROUTES,
    RELAY_CLIENT_WRAPPERS,
    RELAY_GENERIC_CALLS,
)

from mcp_server_phytomni.config.required_env import REQUIRED_OUTBOUND_FIELDS
from mcp_server_phytomni.runtime.outbound import OutboundPoolName
from mcp_server_phytomni.runtime.outbound.lifecycle import _CAPACITY_FIELDS

pytestmark = pytest.mark.unit

_SRC_ROOT = Path(__file__).parents[2] / "src"
_SERVER_ROOT = _SRC_ROOT / "mcp_server_phytomni"
_CLIENT_ROOT = _SRC_ROOT / "mcp_client_phytomni"

type _CallSite = tuple[str, str, str]
type _ResourceDisposition = Literal[
    "server_outbound_runtime",
    "native_asyncpg_pool",
    "local_not_outbound",
    "separate_client_process",
]
_LOCAL_STORAGE_DISPOSITION: _ResourceDisposition = "local_not_outbound"

_EXPECTED_CAPACITY_FIELD_TEXT = """
OUTBOUND_LLM_CONCURRENCY OUTBOUND_RETRIEVAL_CONCURRENCY
OUTBOUND_RERANK_CONCURRENCY OUTBOUND_NL2SQL_CONCURRENCY
OUTBOUND_ANALYSIS_CONTROL_CONCURRENCY
OUTBOUND_ANALYSIS_STATUS_CONCURRENCY OUTBOUND_IAM_CONCURRENCY
OUTBOUND_SPA_FAQ_CONCURRENCY OUTBOUND_BI_CONCURRENCY
OUTBOUND_OBS_CONCURRENCY OUTBOUND_RELAY_CONTROL_CONCURRENCY
OUTBOUND_INTEROP_CONCURRENCY
"""
_EXPECTED_CAPACITY_FIELDS = dict(
    zip(
        OutboundPoolName,
        _EXPECTED_CAPACITY_FIELD_TEXT.split(),
        strict=True,
    )
)

_EXPECTED_OUTBOUND_CONSTRUCTORS: dict[_CallSite, _ResourceDisposition] = {
    (
        "mcp_server_phytomni/runtime/outbound/http.py",
        "build_outbound_http_runtime",
        "resolved_factories.trusted",
    ): "server_outbound_runtime",
    (
        "mcp_server_phytomni/runtime/outbound/http.py",
        "build_outbound_http_runtime",
        "resolved_factories.direct_upstream",
    ): "server_outbound_runtime",
    (
        "mcp_server_phytomni/runtime/outbound/lifecycle.py",
        "_build_openai_client",
        "httpx.AsyncClient",
    ): "server_outbound_runtime",
    (
        "mcp_server_phytomni/runtime/outbound/lifecycle.py",
        "_build_openai_client",
        "factories.openai",
    ): "server_outbound_runtime",
    (
        "mcp_server_phytomni/runtime/outbound/lifecycle.py",
        "init_outbound_runtime",
        "resolved_factories.interop",
    ): "server_outbound_runtime",
    (
        "mcp_server_phytomni/runtime/outbound/obs.py",
        "build_obs_client_runtime",
        "factory",
    ): "server_outbound_runtime",
    (
        "mcp_server_phytomni/interop/http_transport.py",
        "InteropHTTPTransport.__init__",
        "httpx.AsyncHTTPTransport",
    ): "server_outbound_runtime",
    (
        "mcp_server_phytomni/interop/http_transport.py",
        "httpx_client_factory",
        "_InteropAsyncClient",
    ): "server_outbound_runtime",
    (
        "mcp_server_phytomni/interop/runtime.py",
        "InteropResourceRuntime._get_http_client",
        "self._factories.http",
    ): "server_outbound_runtime",
    (
        "mcp_server_phytomni/interop/runtime.py",
        "InteropResourceRuntime._get_a2a_resource",
        "factory",
    ): "server_outbound_runtime",
    (
        "mcp_server_phytomni/interop/runtime.py",
        "InteropResourceRuntime._build_mcp_resource",
        "self._factories.streamable",
    ): "server_outbound_runtime",
    (
        "mcp_server_phytomni/interop/runtime.py",
        "InteropResourceRuntime._build_mcp_resource",
        "self._factories.stdio",
    ): "server_outbound_runtime",
    (
        "mcp_server_phytomni/interop/runtime.py",
        "InteropResourceRuntime._build_mcp_resource",
        "self._factories.session",
    ): "server_outbound_runtime",
    (
        "mcp_server_phytomni/agents/shared/gauss.py",
        "_gauss_pool",
        "asyncpg.create_pool",
    ): "native_asyncpg_pool",
    (
        "mcp_client_phytomni/http_client.py",
        "PhytomniHttpClient._get_client",
        "httpx.AsyncClient",
    ): "separate_client_process",
    (
        "mcp_client_phytomni/client.py",
        "PhytomniMcpClient.connect",
        "stdio_client",
    ): "separate_client_process",
    (
        "mcp_client_phytomni/client.py",
        "PhytomniMcpClient.connect",
        "ClientSession",
    ): "separate_client_process",
    (
        "mcp_client_phytomni/client.py",
        "PhytomniToolRouter.from_env",
        "AsyncOpenAI",
    ): "separate_client_process",
}

_DIRECT_FACTORY_CALLS = frozenset(
    {
        "factories.openai",
        "resolved_factories.direct_upstream",
        "resolved_factories.interop",
        "resolved_factories.trusted",
        "self._factories.http",
        "self._factories.session",
        "self._factories.stdio",
        "self._factories.streamable",
    }
)
_RESOURCE_CONSTRUCTOR_NAMES = frozenset(
    {
        "AsyncClient",
        "AsyncHTTPTransport",
        "AsyncOpenAI",
        "ClientSession",
        "ObsClient",
        "_InteropAsyncClient",
        "create_pool",
        "stdio_client",
        "streamable_http_client",
    }
)

_EXPECTED_LOCAL_SQLITE_CALLS: Counter[_CallSite] = Counter(
    {
        (
            "mcp_server_phytomni/func_cache/storage.py",
            "Storage._get_conn",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/runtime/run_registry_views.py",
            "RunRegistryViewsMixin.claim_a2ui_action",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/runtime/checkpoint_backend.py",
            "_DirectSqliteConnection.__init__",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/runtime/deep_genome_admin.py",
            "_create_backup",
            "sqlite3.connect",
        ): 2,
        (
            "mcp_server_phytomni/runtime/task_manager.py",
            "TaskManager._init_db",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/runtime/task_manager.py",
            "TaskManager._get_connection",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/runtime/sqlite.py",
            "sqlite_connection",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/runtime/sqlite.py",
            "sqlite_transaction",
            "sqlite3.connect",
        ): 2,
        (
            "mcp_server_phytomni/runtime/deep_genome_store.py",
            "DeepGenomeStore._init_schema",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/runtime/deep_genome_store.py",
            "DeepGenomeStore.reserve_run",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/runtime/deep_genome_store.py",
            "DeepGenomeStore.compensate_launch_failure",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/runtime/deep_genome_store.py",
            "DeepGenomeStore.seed_plan",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/runtime/deep_genome_store.py",
            "DeepGenomeStore.accept_remote_submission",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/runtime/deep_genome_store.py",
            "DeepGenomeStore.get_snapshot",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/runtime/deep_genome_transitions.py",
            "DeepGenomeTransitionMixin.apply_brief_gene_transition",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/runtime/deep_genome_transitions.py",
            "DeepGenomeTransitionMixin.apply_work_item_transition",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/runtime/deep_genome_transitions.py",
            "DeepGenomeTransitionMixin.publish_final_report",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/runtime/deep_genome_transitions.py",
            "DeepGenomeTransitionMixin.fail_umbrella",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/api/relay/routes.py",
            "_read_research_grant_rows",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/agents/shared/citation_database.py",
            "_connect_read_only",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/runtime/memory/sqlite.py",
            "MemoryStore._connect",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/runtime/memory/sqlite.py",
            "MemoryStore.__init__",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/runtime/execution_event_store.py",
            "SQLiteExecutionEventStore._init_db",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/runtime/execution_event_store.py",
            "SQLiteExecutionEventStore.append",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/runtime/execution_event_store.py",
            "SQLiteExecutionEventStore.list_events",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/runtime/execution_event_store.py",
            "SQLiteExecutionEventStore.get_event",
            "sqlite3.connect",
        ): 1,
        (
            "mcp_server_phytomni/runtime/execution_event_store.py",
            "SQLiteExecutionEventStore.get_projection",
            "sqlite3.connect",
        ): 1,
    }
)


_OBS_OPERATION_NAMES = frozenset(
    {
        "abortMultipartUpload",
        "completeMultipartUpload",
        "deleteObject",
        "getObject",
        "getObjectMetadata",
        "initiateMultipartUpload",
        "listObjects",
        "putContent",
        "uploadPart",
    }
)
_HTTP_OPERATION_NAMES = frozenset(
    {
        "delete",
        "get",
        "head",
        "options",
        "patch",
        "post",
        "put",
        "request",
        "send",
        "stream",
    }
)
_HTTP_RECEIVER_MARKERS = frozenset(
    {
        "client",
        "http",
        "request",
        "response",
        "runtime",
        "transport",
        "upstream",
    }
)
_PROTOCOL_OPERATION_NAMES = frozenset(
    {
        "call_tool",
        "initialize",
        "list_tools",
        "send_message",
        "send_message_streaming",
    }
)
_PROTOCOL_RECEIVER_MARKERS = frozenset({"client", "server", "session"})
_BARE_PROTOCOL_OPERATIONS = frozenset(
    {"send_message", "send_message_streaming"}
)
_RESPONSE_BODY_OPERATION_NAMES = frozenset(
    {
        "aread",
        "aiter_bytes",
        "aiter_raw",
    }
)

type _PoolBindingSite = tuple[str, str, str, OutboundPoolName]

_EXPECTED_DIRECT_POOL_BINDINGS: Counter[_PoolBindingSite] = Counter(
    {
        (
            "mcp_server_phytomni/auth/iam.py",
            "get_token",
            "current_outbound_runtime().http.for_pool",
            OutboundPoolName.IAM,
        ),
        (
            "mcp_server_phytomni/common/relay_client.py",
            "RelayClient._request_bytes",
            "current_outbound_runtime().http.for_pool",
            OutboundPoolName.OBS,
        ),
        (
            "mcp_server_phytomni/common/relay_client.py",
            "RelayClient.get_obs_object_to_path",
            "http_runtime.stream",
            OutboundPoolName.OBS,
        ),
        (
            "mcp_server_phytomni/interop/runtime.py",
            "InteropResourceRuntime.run_http",
            "self._pools.lease",
            OutboundPoolName.INTEROP,
        ),
        (
            "mcp_server_phytomni/interop/runtime.py",
            "InteropResourceRuntime.stream_http",
            "self._pools.lease",
            OutboundPoolName.INTEROP,
        ),
        (
            "mcp_server_phytomni/interop/runtime.py",
            "InteropResourceRuntime.stream_a2a",
            "self._pools.lease",
            OutboundPoolName.INTEROP,
        ),
        (
            "mcp_server_phytomni/interop/runtime.py",
            "InteropResourceRuntime._get_mcp_resource",
            "self._pools.lease",
            OutboundPoolName.INTEROP,
        ),
        (
            "mcp_server_phytomni/interop/runtime.py",
            "InteropResourceRuntime.run_mcp",
            "self._pools.lease",
            OutboundPoolName.INTEROP,
        ),
        (
            "mcp_server_phytomni/agents/analyst/graph.py",
            "AnalystGraphMixin._post_submit_job",
            "current_outbound_http_client",
            OutboundPoolName.ANALYSIS_CONTROL,
        ),
        (
            "mcp_server_phytomni/agents/analyst/task_ops.py",
            "task_status",
            "current_outbound_http_client",
            OutboundPoolName.ANALYSIS_STATUS,
        ),
        (
            "mcp_server_phytomni/agents/analyst/task_ops.py",
            "task_log",
            "current_outbound_http_client",
            OutboundPoolName.ANALYSIS_STATUS,
        ),
        (
            "mcp_server_phytomni/agents/analyst/task_ops.py",
            "task_delete",
            "current_outbound_http_client",
            OutboundPoolName.ANALYSIS_CONTROL,
        ),
        (
            "mcp_server_phytomni/agents/knowledge/retrieval.py",
            "_retrieve_raw_docs",
            "current_outbound_runtime().http.for_pool",
            OutboundPoolName.RETRIEVAL,
        ),
        (
            "mcp_server_phytomni/agents/knowledge/retrieval.py",
            "rerank",
            "current_outbound_runtime().http.for_pool",
            OutboundPoolName.RERANK,
        ),
        (
            "mcp_server_phytomni/agents/data/nl2sql.py",
            "_execute_nl2sql_uncached",
            "runtime.http.for_pool",
            OutboundPoolName.NL2SQL,
        ),
        (
            "mcp_server_phytomni/agents/evolution/agent.py",
            "find_spa_taxids",
            "current_outbound_runtime().http.for_pool",
            OutboundPoolName.SPA_FAQ,
        ),
        (
            "mcp_server_phytomni/agents/chat/service.py",
            "_run_chat_completion_cached",
            "runtime.pools.lease",
            OutboundPoolName.LLM,
        ),
        (
            "mcp_server_phytomni/agents/chat/service.py",
            "stream_phyto_chat_chunks",
            "runtime.pools.lease",
            OutboundPoolName.LLM,
        ),
        (
            "mcp_server_phytomni/agents/expert/router.py",
            "_create",
            "runtime.pools.lease",
            OutboundPoolName.LLM,
        ),
        (
            "mcp_server_phytomni/runtime/outbound/obs.py",
            "ObsClientRuntime.run",
            "self._pools.lease",
            OutboundPoolName.OBS,
        ),
    }
)

_EXPECTED_DELEGATED_POOL_BINDINGS: Counter[_CallSite] = Counter(
    {
        (
            "mcp_server_phytomni/api/relay/forward.py",
            "_open_relay_upstream",
            "http_runtime.stream",
        ): 1,
        (
            "mcp_server_phytomni/common/relay_client.py",
            "RelayClient._request_json",
            "current_outbound_runtime().http.for_pool",
        ): 1,
        (
            "mcp_server_phytomni/runtime/outbound/http.py",
            "BoundAsyncRequestClient.request",
            "self._pools.lease",
        ): 1,
        (
            "mcp_server_phytomni/runtime/outbound/http.py",
            "OutboundHttpRuntime.stream",
            "self._pools.lease",
        ): 1,
        (
            "mcp_server_phytomni/runtime/outbound/lifecycle.py",
            "current_outbound_http_client",
            "current_outbound_runtime().http.for_pool",
        ): 1,
    }
)

_POOL_BINDING_CALLEES = frozenset(
    {
        "current_outbound_http_client",
        "current_outbound_runtime().http.for_pool",
        "http_runtime.stream",
        "runtime.http.for_pool",
        "runtime.pools.lease",
        "self._pools.lease",
    }
)


def _source_trees() -> tuple[tuple[Path, ast.Module], ...]:
    """Parse current server and sibling-client sources exactly once."""
    return tuple(
        (path, ast.parse(path.read_text(encoding="utf-8"), str(path)))
        for root in (_SERVER_ROOT, _CLIENT_ROOT)
        for path in root.rglob("*.py")
    )


def _parents(tree: ast.Module) -> dict[ast.AST, ast.AST]:
    """Return the immediate-parent lookup for one syntax tree."""
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _qualified_owner(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> str:
    """Return the enclosing class/function name for one syntax node."""
    owner = parents.get(node)
    while owner is not None and not isinstance(
        owner, (ast.FunctionDef, ast.AsyncFunctionDef)
    ):
        owner = parents.get(owner)
    if owner is None:
        return "<module>"
    classes: list[str] = []
    parent = parents.get(owner)
    while parent is not None:
        if isinstance(parent, ast.ClassDef):
            classes.append(parent.name)
        parent = parents.get(parent)
    return ".".join((*reversed(classes), owner.name))


def _call_site(
    path: Path,
    node: ast.Call,
    parents: dict[ast.AST, ast.AST],
) -> _CallSite:
    """Project one call to a stable source/owner/callee identity."""
    return (
        path.relative_to(_SRC_ROOT).as_posix(),
        _qualified_owner(node, parents),
        ast.unparse(node.func),
    )


def _outbound_constructor_calls() -> set[_CallSite]:
    """Return every recognized server or sibling-client constructor call."""
    calls: set[_CallSite] = set()
    bounded_factories = {
        (
            "mcp_server_phytomni/runtime/outbound/obs.py",
            "build_obs_client_runtime",
        ),
        (
            "mcp_server_phytomni/interop/runtime.py",
            "InteropResourceRuntime._get_a2a_resource",
        ),
    }
    for path, tree in _source_trees():
        parent_map = _parents(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            site = _call_site(path, node, parent_map)
            callee = site[2]
            tail = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id if isinstance(node.func, ast.Name) else ""
            )
            bounded_factory = callee == "factory" and site[:2] in (
                bounded_factories
            )
            if (
                tail in _RESOURCE_CONSTRUCTOR_NAMES
                or callee in _DIRECT_FACTORY_CALLS
                or bounded_factory
            ):
                calls.add(site)
    return calls


def _local_sqlite_calls() -> Counter[_CallSite]:
    """Return every local SQLite constructor as a finite multiset."""
    calls: Counter[_CallSite] = Counter()
    for path, tree in _source_trees():
        parent_map = _parents(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if ast.unparse(node.func) == "sqlite3.connect":
                calls[_call_site(path, node, parent_map)] += 1
    return calls


def _network_operation_calls() -> Counter[_CallSite]:
    """Return every semantic HTTP/OpenAI/OBS/Interop I/O candidate."""
    calls: Counter[_CallSite] = Counter()
    for path, tree in _source_trees():
        parent_map = _parents(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = ast.unparse(node.func)
            tail = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id if isinstance(node.func, ast.Name) else ""
            )
            receiver = (
                ast.unparse(node.func.value).lower()
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            receiver_words = {
                word
                for word in receiver.replace("(", ".")
                .replace(")", ".")
                .replace("_", ".")
                .split(".")
                if word
            }
            http_receivers = _HTTP_RECEIVER_MARKERS
            if tail == "get":
                receiver_tail = (
                    node.func.value.attr
                    if isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Attribute)
                    else (
                        node.func.value.id
                        if isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        else ""
                    )
                )
                is_http = receiver_tail == "client" or receiver_tail.endswith(
                    "_client"
                )
            else:
                is_http = tail in _HTTP_OPERATION_NAMES and bool(
                    receiver_words & http_receivers
                )
            is_openai = tail == "create" and any(
                namespace in callee
                for namespace in (".chat.completions.", ".embeddings.")
            )
            is_protocol = tail in _PROTOCOL_OPERATION_NAMES and (
                bool(receiver_words & _PROTOCOL_RECEIVER_MARKERS)
                or callee in _BARE_PROTOCOL_OPERATIONS
            )
            if not (
                is_http
                or is_openai
                or tail in _OBS_OPERATION_NAMES
                or is_protocol
                or tail in _RESPONSE_BODY_OPERATION_NAMES
                or callee == "self._delegate.handle_async_request"
            ):
                continue
            calls[_call_site(path, node, parent_map)] += 1
    return calls


def _direct_pool_binding_inventory() -> tuple[
    Counter[_PoolBindingSite],
    Counter[_CallSite],
]:
    """Return literal bindings and explicitly delegated dynamic bindings."""
    calls: Counter[_PoolBindingSite] = Counter()
    delegated: Counter[_CallSite] = Counter()
    for path, tree in _source_trees():
        parent_map = _parents(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = ast.unparse(node.func)
            if callee not in _POOL_BINDING_CALLEES:
                continue
            pool_nodes = {
                child.attr
                for child in ast.walk(node)
                if isinstance(child, ast.Attribute)
                and isinstance(child.value, ast.Name)
                and child.value.id == "OutboundPoolName"
            }
            if not pool_nodes:
                delegated[_call_site(path, node, parent_map)] += 1
                continue
            assert len(pool_nodes) == 1, _call_site(path, node, parent_map)
            site = _call_site(path, node, parent_map)
            (pool_name,) = pool_nodes
            calls[(*site, OutboundPoolName[pool_name])] += 1
    return calls, delegated


def _direct_pool_binding_calls() -> Counter[_PoolBindingSite]:
    """Return direct bindings, rejecting every unlisted dynamic binding."""
    calls, delegated = _direct_pool_binding_inventory()
    unexpected = delegated - _EXPECTED_DELEGATED_POOL_BINDINGS
    assert not unexpected, (
        "recognized pool binding requires literal OutboundPoolName: "
        f"{unexpected}"
    )
    return calls


def test_pool_and_capacity_inventory_is_exact() -> None:
    """Every named pool has one literal deployment capacity field."""
    assert _CAPACITY_FIELDS == _EXPECTED_CAPACITY_FIELDS
    assert set(OutboundPoolName) == set(_EXPECTED_CAPACITY_FIELDS)
    assert set(REQUIRED_OUTBOUND_FIELDS) == {
        *_EXPECTED_CAPACITY_FIELDS.values(),
        "OUTBOUND_POOL_WAIT_WARN_SECONDS",
    }


def test_outbound_constructor_inventory_is_exhaustive() -> None:
    """Every outbound resource constructor has one explicit disposition."""
    assert _outbound_constructor_calls() == set(
        _EXPECTED_OUTBOUND_CONSTRUCTORS
    )
    assert set(_EXPECTED_OUTBOUND_CONSTRUCTORS.values()) == {
        "server_outbound_runtime",
        "native_asyncpg_pool",
        "separate_client_process",
    }


def test_local_sqlite_inventory_is_explicitly_not_outbound() -> None:
    """Every SQLite connection is finite local storage, never a pool bypass."""
    assert _local_sqlite_calls() == _EXPECTED_LOCAL_SQLITE_CALLS
    assert _LOCAL_STORAGE_DISPOSITION == "local_not_outbound"


def test_sibling_client_process_cannot_import_server_runtime() -> None:
    """The shipped client owns its transports outside the server process."""
    for path in _CLIENT_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "mcp_server_phytomni.runtime.outbound" not in source


def test_network_operation_inventory_is_exhaustive() -> None:
    """Every send/open/stream operation has one finite owned disposition."""
    assert not (
        _EXPECTED_NETWORK_OPERATIONS & _EXPECTED_NON_NETWORK_OPERATIONS
    )
    assert _network_operation_calls() == (
        _EXPECTED_NETWORK_OPERATIONS + _EXPECTED_NON_NETWORK_OPERATIONS
    )
    assert set(_NETWORK_OPERATION_DISPOSITIONS) == set(
        _EXPECTED_NETWORK_OPERATIONS
    )
    expected_dispositions_text = (
        "pooled_http_adapter pooled_http_transport pooled_interop pooled_obs "
        "pooled_openai pooled_relay_stream separate_client_process"
    )
    assert set(_NETWORK_OPERATION_DISPOSITIONS.values()) == set(
        expected_dispositions_text.split()
    )


def test_network_inventory_recognizes_each_semantic_io_call_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scanner cannot omit I/O merely because its callee is new."""
    path = _SRC_ROOT / "semantic_network_probe.py"
    tree = ast.parse("""
async def probe(client, response, session):
    await client.get("https://example.test")
    await client.request("GET", "https://example.test")
    await client.send(request)
    async with client.stream("GET", "https://example.test"):
        pass
    await response.aread()
    await session.initialize()
    await session.list_tools()
    await session.call_tool("tool", {})
    await self.initialize()
    await self.list_tools()
""")
    monkeypatch.setattr(
        sys.modules[__name__],
        "_source_trees",
        lambda: ((path, tree),),
    )

    assert _network_operation_calls() == Counter(
        {
            ("semantic_network_probe.py", "probe", "client.get"): 1,
            ("semantic_network_probe.py", "probe", "client.request"): 1,
            ("semantic_network_probe.py", "probe", "client.send"): 1,
            ("semantic_network_probe.py", "probe", "client.stream"): 1,
            ("semantic_network_probe.py", "probe", "response.aread"): 1,
            ("semantic_network_probe.py", "probe", "session.initialize"): 1,
            ("semantic_network_probe.py", "probe", "session.list_tools"): 1,
            ("semantic_network_probe.py", "probe", "session.call_tool"): 1,
        }
    )


def test_relay_operation_tables_have_one_nonduplicated_role() -> None:
    """Relay-child and operator rows remain finite single-source contracts."""
    assert len(RELAY_GENERIC_CALLS) == len(set(RELAY_GENERIC_CALLS))
    assert len(RELAY_CLIENT_WRAPPERS) == len(set(RELAY_CLIENT_WRAPPERS))
    assert len(OPERATOR_FORWARD_ROUTES) == len(set(OPERATOR_FORWARD_ROUTES))
    assert len(OPERATOR_SERVER_TERMINATED_ROUTES) == len(
        set(OPERATOR_SERVER_TERMINATED_ROUTES)
    )
    assert {row.owner for row in OPERATOR_SERVER_TERMINATED_ROUTES} == {
        "bi",
        "control",
        "local",
        "obs",
    }


def test_direct_operation_table_covers_every_runtime_binding() -> None:
    """Every direct adapter and runtime action binds one literal pool."""
    _calls, delegated = _direct_pool_binding_inventory()
    assert delegated == _EXPECTED_DELEGATED_POOL_BINDINGS
    assert _direct_pool_binding_calls() == _EXPECTED_DIRECT_POOL_BINDINGS


def test_direct_binding_rejects_a_recognized_call_without_literal_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recognized direct binding cannot silently disappear from inventory."""
    path = _SRC_ROOT / "missing_literal_pool_probe.py"
    tree = ast.parse("""
def probe(pool):
    return current_outbound_runtime().http.for_pool(pool)
""")
    monkeypatch.setattr(
        sys.modules[__name__],
        "_source_trees",
        lambda: ((path, tree),),
    )

    with pytest.raises(AssertionError, match="literal OutboundPoolName"):
        _direct_pool_binding_calls()


def test_direct_relay_and_operator_rows_cover_every_pool_family() -> None:
    """All deployment roles collectively account for every service family."""
    covered = {
        *(site[3] for site in _EXPECTED_DIRECT_POOL_BINDINGS),
        *(row.pool for row in RELAY_GENERIC_CALLS),
        *(row.pool for row in RELAY_CLIENT_WRAPPERS),
        *(row.pool for row in OPERATOR_FORWARD_ROUTES),
    }
    assert covered == set(OutboundPoolName)
