# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Static close-owner inventory for outbound resources and streams."""

from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path
from typing import Literal

import pytest

pytestmark = pytest.mark.unit

_SRC_ROOT = Path(__file__).parents[2] / "src"

_EXPECTED_OUTBOUND_OWNER_FUNCTIONS = {
    "mcp_server_phytomni/runtime/outbound/http.py": frozenset(
        {
            "BoundAsyncRequestClient.request",
            "OutboundHttpRuntime._close_resources",
            "close_resources",
        }
    ),
    "mcp_server_phytomni/runtime/outbound/lifecycle.py": frozenset(
        {
            "OutboundRuntime._close_resources",
            "_build_openai_client",
            "_close_openai_resource",
            "init_outbound_runtime",
        }
    ),
    "mcp_server_phytomni/runtime/outbound/obs.py": frozenset(
        {"ObsClientRuntime._close_sync"}
    ),
    "mcp_server_phytomni/agents/shared/gauss.py": frozenset(
        {"aclose_gauss_pool"}
    ),
    "mcp_server_phytomni/interop/http_transport.py": frozenset(
        {
            "InteropHTTPTransport.aclose",
            "InteropHTTPTransport.handle_async_request",
            "_CappedResponseStream._close_safely",
        }
    ),
    "mcp_server_phytomni/interop/runtime.py": frozenset(
        {
            "InteropResourceRuntime._build_mcp_resource",
            "InteropResourceRuntime._evict_a2a",
            "InteropResourceRuntime._evict_http",
            "InteropResourceRuntime._evict_mcp",
            "InteropResourceRuntime._get_a2a_resource",
            "InteropResourceRuntime._get_mcp_resource",
            "InteropResourceRuntime.aclose",
        }
    ),
    "mcp_server_phytomni/interop/a2a_client.py": frozenset(
        {"_close_client", "_close_iterator"}
    ),
    "mcp_server_phytomni/api/relay/forward.py": frozenset(
        {
            "_RelayResponseBody.aclose",
            "_RelayResponseBody.close",
            "_RelayStreamingResponse.__call__",
            "_close_stack",
            "_open_relay_upstream",
            "forward_relay_request",
        }
    ),
    "mcp_server_phytomni/api/relay/obs.py": frozenset(
        {"_close_stream_iterator"}
    ),
    "mcp_server_phytomni/storage/obs_relay_ops.py": frozenset(
        {"close_stream"}
    ),
    "mcp_server_phytomni/storage/gene_example_reader.py": frozenset(
        {"CuratedReadControl.bind_source", "CuratedReadControl.finish_source"}
    ),
    "mcp_server_phytomni/agents/chat/service.py": frozenset(
        {"_close_async_stream"}
    ),
    "mcp_client_phytomni/client.py": frozenset(
        {"PhytomniMcpClient.__aexit__", "PhytomniMcpClient.close"}
    ),
    "mcp_client_phytomni/http_client.py": frozenset(
        {"PhytomniHttpClient.__aexit__", "PhytomniHttpClient.aclose"}
    ),
}

type _CloseDisposition = Literal[
    "downstream_iterator",
    "lifecycle_entrypoint",
    "local_not_outbound",
    "native_asyncpg_pool",
    "separate_client_process",
    "server_outbound_owner",
]

_EXPECTED_CLOSE_OWNERS: dict[str, Counter[str]] = {
    "mcp_client_phytomni/client.py": Counter(
        {
            "PhytomniMcpClient.__aexit__": 1,
            "PhytomniMcpClient.close": 1,
        }
    ),
    "mcp_client_phytomni/http_client.py": Counter(
        {
            "PhytomniHttpClient.__aexit__": 1,
            "PhytomniHttpClient.aclose": 1,
        }
    ),
    "mcp_server_phytomni/agents/chat/service.py": Counter(
        {
            "_close_async_stream": 1,
            "_stream_response_to_dict": 1,
            "stream_phyto_chat_chunks": 1,
        }
    ),
    "mcp_server_phytomni/agents/shared/citation_database.py": Counter(
        {
            "_connect_read_only": 1,
            "_lookup_citation_records": 1,
            "validate_citation_database": 1,
        }
    ),
    "mcp_server_phytomni/agents/shared/gauss.py": Counter(
        {"aclose_gauss_pool": 1}
    ),
    "mcp_server_phytomni/api/agent_run_support.py": Counter(
        {"stream_run_id": 1}
    ),
    "mcp_server_phytomni/api/app_support.py": Counter({"_http_lifespan": 2}),
    "mcp_server_phytomni/api/openai_mapping.py": Counter(
        {"to_chat_completion_chunks": 1}
    ),
    "mcp_server_phytomni/api/relay/forward.py": Counter(
        {
            "_RelayResponseBody.aclose": 1,
            "_RelayResponseBody.close": 2,
            "_RelayStreamingResponse.__call__": 1,
            "_close_stack": 2,
            "_close_unstarted": 1,
            "_open_relay_upstream": 2,
            "_stream": 2,
            "forward_relay_request": 1,
        }
    ),
    "mcp_server_phytomni/api/relay/obs.py": Counter(
        {
            "_close_stream_iterator": 1,
            "_produce_obs_chunks": 1,
            "_stream": 1,
        }
    ),
    "mcp_server_phytomni/api/relay/routes.py": Counter(
        {"_read_research_grant_rows": 1}
    ),
    "mcp_server_phytomni/api/research_capabilities.py": Counter(
        {"ResearchRelayCapabilityCache.schedule_refresh": 1}
    ),
    "mcp_server_phytomni/api/run_lifecycle.py": Counter(
        {
            "_close_checkpointer": 1,
            "_delete_review_candidate_checkpoints": 1,
        }
    ),
    "mcp_server_phytomni/api/streaming.py": Counter({"_wrapped": 2}),
    "mcp_server_phytomni/common/reasoning_content.py": Counter(
        {"normalize_message_fields": 1}
    ),
    "mcp_server_phytomni/func_cache/core.py": Counter(
        {
            "CacheRuntime.__init__": 1,
            "CacheRuntime._cache_value_accepted": 1,
        }
    ),
    "mcp_server_phytomni/func_cache/storage.py": Counter(
        {
            "Storage._get_conn": 1,
            "Storage.close": 1,
            "Storage.close_all": 2,
            "Storage.try_acquire_lock": 1,
        }
    ),
    "mcp_server_phytomni/interop/a2a_client.py": Counter(
        {
            "_close_client": 1,
            "_close_iterator": 1,
            "_open_execution": 1,
            "_stream_execution": 3,
            "stream_with_client": 1,
        }
    ),
    "mcp_server_phytomni/interop/http_transport.py": Counter(
        {
            "InteropHTTPTransport.aclose": 1,
            "InteropHTTPTransport.handle_async_request": 2,
            "_CappedResponseStream.__aiter__": 4,
            "_CappedResponseStream._close_safely": 1,
            "_CappedResponseStream.aclose": 1,
        }
    ),
    "mcp_server_phytomni/interop/runtime.py": Counter(
        {
            "InteropResourceRuntime._build_mcp_resource": 1,
            "InteropResourceRuntime._evict_a2a": 1,
            "InteropResourceRuntime._evict_http": 1,
            "InteropResourceRuntime._evict_mcp": 1,
            "InteropResourceRuntime._get_a2a_resource": 1,
            "InteropResourceRuntime._get_mcp_resource": 1,
            "InteropResourceRuntime.aclose": 3,
        }
    ),
    "mcp_server_phytomni/mcp/app.py": Counter(
        {
            "_stream_chat_agent": 1,
            "_stream_chat_events": 1,
            "_stream_graph_agent": 1,
            "serve": 2,
        }
    ),
    "mcp_server_phytomni/runtime/checkpoint_backend.py": Counter(
        {
            "_DirectSqliteConnection.close": 1,
            "_DirectSqliteCursor.__aexit__": 1,
        }
    ),
    "mcp_server_phytomni/runtime/conversation_context/review_lock.py": Counter(
        {
            "ReviewMutationLock.release": 1,
            "acquire_review_mutation_lock": 1,
        }
    ),
    "mcp_server_phytomni/runtime/deep_genome_admin.py": Counter(
        {"_create_backup": 2}
    ),
    "mcp_server_phytomni/runtime/deep_genome_store.py": Counter(
        {
            "DeepGenomeStore._init_schema": 1,
            "DeepGenomeStore.accept_remote_submission": 1,
            "DeepGenomeStore.compensate_launch_failure": 1,
            "DeepGenomeStore.get_snapshot": 1,
            "DeepGenomeStore.reserve_run": 1,
            "DeepGenomeStore.seed_plan": 1,
        }
    ),
    "mcp_server_phytomni/runtime/deep_genome_transitions.py": Counter(
        {
            "DeepGenomeTransitionMixin.apply_brief_gene_transition": 1,
            "DeepGenomeTransitionMixin.apply_work_item_transition": 1,
            "DeepGenomeTransitionMixin.fail_umbrella": 1,
            "DeepGenomeTransitionMixin.publish_final_report": 1,
        }
    ),
    "mcp_server_phytomni/runtime/async_iterator_v2.py": Counter(
        {"close_async_iterator": 1}
    ),
    "mcp_server_phytomni/runtime/execution_entrypoint_v2.py": Counter(
        {"_StreamLifecycle._wrapped": 1}
    ),
    "mcp_server_phytomni/runtime/execution_event_store.py": Counter(
        {
            "SQLiteExecutionEventStore._init_db": 1,
            "SQLiteExecutionEventStore.append": 1,
            "SQLiteExecutionEventStore.list_events": 1,
            "SQLiteExecutionEventStore.get_event": 1,
            "SQLiteExecutionEventStore.get_projection": 1,
        }
    ),
    "mcp_server_phytomni/runtime/langgraph_runner.py": Counter(
        {"stream_graph": 1}
    ),
    "mcp_server_phytomni/runtime/memory/sqlite.py": Counter(
        {
            "MemoryStore.__init__": 1,
            "MemoryStore._connect": 1,
            "MemoryStore.close": 1,
        }
    ),
    "mcp_server_phytomni/runtime/outbound/http.py": Counter(
        {
            "BoundAsyncRequestClient.request": 1,
            "OutboundHttpRuntime._close_resources": 2,
            "OutboundHttpRuntime.aclose": 1,
            "close_resources": 3,
        }
    ),
    "mcp_server_phytomni/runtime/outbound/lifecycle.py": Counter(
        {
            "OutboundRuntime._close_resources": 2,
            "OutboundRuntime.begin_close": 1,
            "_build_openai_client": 1,
            "_close_openai_resource": 2,
            "aclose_outbound_runtime": 2,
            "close_openai": 1,
            "init_outbound_runtime": 2,
        }
    ),
    "mcp_server_phytomni/runtime/outbound/obs.py": Counter(
        {
            "ObsClientRuntime._close_sync": 1,
            "ObsClientRuntime.aclose": 1,
            "ObsClientRuntime.close_in_thread": 2,
        }
    ),
    "mcp_server_phytomni/runtime/outbound/registry.py": Counter(
        {"OutboundPoolRegistry._acquire": 2}
    ),
    "mcp_server_phytomni/runtime/run_registry_views.py": Counter(
        {"RunRegistryViewsMixin.claim_a2ui_action": 1}
    ),
    "mcp_server_phytomni/runtime/sqlite.py": Counter(
        {"sqlite_connection": 1, "sqlite_transaction": 1}
    ),
    "mcp_server_phytomni/runtime/task_manager.py": Counter(
        {
            "TaskManager._init_db": 1,
            "TaskManager.create_task": 1,
            "TaskManager.get_task": 1,
            "TaskManager.get_task_agent": 1,
            "TaskManager.get_task_by_fingerprint": 1,
            "TaskManager.get_task_degraded": 1,
            "TaskManager.get_task_final_report": 1,
            "TaskManager.get_task_log": 1,
            "TaskManager.record": 1,
            "TaskManager.set_task_degraded": 1,
            "TaskManager.set_task_final_report": 1,
            "TaskManager.set_task_log": 1,
            "TaskManager.update_task": 1,
        }
    ),
    "mcp_server_phytomni/storage/downloads.py": Counter(
        {"download_list_convert": 1}
    ),
    "mcp_server_phytomni/storage/gene_example_reader.py": Counter(
        {
            "CuratedReadControl.bind_source": 1,
            "CuratedReadControl.finish_source": 1,
        }
    ),
    "mcp_server_phytomni/storage/gene_examples.py": Counter(
        {"_open_directory": 2, "_read_declared": 2, "build_gene_bundle": 1}
    ),
    "mcp_server_phytomni/storage/obs_relay_ops.py": Counter(
        {"_iter_sdk_chunks": 1, "close_stream": 1}
    ),
}

_EXPECTED_CLOSE_PATH_DISPOSITIONS: dict[str, _CloseDisposition] = {
    "mcp_client_phytomni/client.py": "separate_client_process",
    "mcp_client_phytomni/http_client.py": "separate_client_process",
    "mcp_server_phytomni/agents/chat/service.py": "server_outbound_owner",
    "mcp_server_phytomni/agents/shared/citation_database.py": (
        "local_not_outbound"
    ),
    "mcp_server_phytomni/agents/shared/gauss.py": "native_asyncpg_pool",
    "mcp_server_phytomni/api/agent_run_support.py": "downstream_iterator",
    "mcp_server_phytomni/api/app_support.py": "lifecycle_entrypoint",
    "mcp_server_phytomni/api/openai_mapping.py": "downstream_iterator",
    "mcp_server_phytomni/api/relay/forward.py": "server_outbound_owner",
    "mcp_server_phytomni/api/relay/obs.py": "server_outbound_owner",
    "mcp_server_phytomni/api/relay/routes.py": "local_not_outbound",
    "mcp_server_phytomni/api/research_capabilities.py": "local_not_outbound",
    "mcp_server_phytomni/api/run_lifecycle.py": "local_not_outbound",
    "mcp_server_phytomni/api/streaming.py": "downstream_iterator",
    "mcp_server_phytomni/common/reasoning_content.py": "local_not_outbound",
    "mcp_server_phytomni/func_cache/core.py": "local_not_outbound",
    "mcp_server_phytomni/func_cache/storage.py": "local_not_outbound",
    "mcp_server_phytomni/interop/a2a_client.py": "server_outbound_owner",
    "mcp_server_phytomni/interop/http_transport.py": "server_outbound_owner",
    "mcp_server_phytomni/interop/runtime.py": "server_outbound_owner",
    "mcp_server_phytomni/mcp/app.py": "lifecycle_entrypoint",
    "mcp_server_phytomni/runtime/checkpoint_backend.py": "local_not_outbound",
    "mcp_server_phytomni/runtime/async_iterator_v2.py": (
        "downstream_iterator"
    ),
    "mcp_server_phytomni/runtime/conversation_context/review_lock.py": (
        "local_not_outbound"
    ),
    "mcp_server_phytomni/runtime/deep_genome_admin.py": "local_not_outbound",
    "mcp_server_phytomni/runtime/deep_genome_store.py": "local_not_outbound",
    "mcp_server_phytomni/runtime/deep_genome_transitions.py": (
        "local_not_outbound"
    ),
    "mcp_server_phytomni/runtime/execution_entrypoint_v2.py": (
        "downstream_iterator"
    ),
    "mcp_server_phytomni/runtime/execution_event_store.py": (
        "local_not_outbound"
    ),
    "mcp_server_phytomni/runtime/langgraph_runner.py": ("downstream_iterator"),
    "mcp_server_phytomni/runtime/memory/sqlite.py": "local_not_outbound",
    "mcp_server_phytomni/runtime/outbound/http.py": "server_outbound_owner",
    "mcp_server_phytomni/runtime/outbound/lifecycle.py": (
        "server_outbound_owner"
    ),
    "mcp_server_phytomni/runtime/outbound/obs.py": "server_outbound_owner",
    "mcp_server_phytomni/runtime/outbound/registry.py": (
        "server_outbound_owner"
    ),
    "mcp_server_phytomni/runtime/run_registry_views.py": "local_not_outbound",
    "mcp_server_phytomni/runtime/sqlite.py": "local_not_outbound",
    "mcp_server_phytomni/runtime/task_manager.py": "local_not_outbound",
    "mcp_server_phytomni/storage/downloads.py": "local_not_outbound",
    "mcp_server_phytomni/storage/gene_example_reader.py": (
        "server_outbound_owner"
    ),
    "mcp_server_phytomni/storage/gene_examples.py": "local_not_outbound",
    "mcp_server_phytomni/storage/obs_relay_ops.py": "server_outbound_owner",
}


def _qualified_owner(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> str:
    """Return the enclosing class/function for one close call."""
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualified = current.name
            container = parents.get(current)
            while container is not None:
                if isinstance(container, ast.ClassDef):
                    qualified = f"{container.name}.{qualified}"
                container = parents.get(container)
            return qualified
        current = parents.get(current)
    raise AssertionError("close call has no enclosing function owner")


def _outbound_close_owners() -> dict[str, Counter[str]]:
    """Return every close-like call owner from the complete source tree."""
    found: dict[str, Counter[str]] = {}
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        owners: Counter[str] = Counter()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            tail = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id if isinstance(node.func, ast.Name) else ""
            )
            if "close" not in tail.lower() and tail not in {
                "closing",
                "aclosing",
                "__aexit__",
                "disconnect",
                "shutdown",
                "terminate",
            }:
                continue
            owners[_qualified_owner(node, parents)] += 1
        if owners:
            found[path.relative_to(_SRC_ROOT).as_posix()] = owners
    return found


def test_every_outbound_close_owner_is_in_the_finite_inventory() -> None:
    """No process resource or stream gains an unowned close path."""
    assert _outbound_close_owners() == _EXPECTED_CLOSE_OWNERS
    assert set(_EXPECTED_CLOSE_PATH_DISPOSITIONS) == set(
        _EXPECTED_CLOSE_OWNERS
    )
    for path, owners in _EXPECTED_OUTBOUND_OWNER_FUNCTIONS.items():
        assert owners <= set(_EXPECTED_CLOSE_OWNERS[path])


def test_close_scan_scope_is_independent_of_the_expected_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing expectations cannot make real close candidates disappear."""
    monkeypatch.setattr(
        sys.modules[__name__],
        "_EXPECTED_CLOSE_OWNERS",
        {},
    )

    assert "mcp_server_phytomni/runtime/outbound/http.py" in (
        _outbound_close_owners()
    )


def test_close_scan_includes_context_manager_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Standard-library closing helpers cannot hide a close owner."""
    source = tmp_path / "owned.py"
    source.write_text(
        "from contextlib import aclosing, closing\n\n"
        "async def own(resource):\n"
        "    with closing(resource):\n"
        "        await resource.ready()\n"
        "    async with aclosing(resource):\n"
        "        await resource.ready()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "_SRC_ROOT", tmp_path)

    assert _outbound_close_owners() == {"owned.py": Counter({"own": 2})}
