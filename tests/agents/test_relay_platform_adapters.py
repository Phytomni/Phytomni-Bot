# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Relay-mode platform adapter tests.

Pins that in customer relay mode each platform HTTP boundary routes
through the relay routes (via ``current_relay_client``) instead of the
operator endpoint, preserving the request body shape and response
parsing. Private boundary helpers are imported by name so the tests
exercise them without a protected-access access expression.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Optional

import pytest

from mcp_server_phytomni.agents.analyst import task_ops
from mcp_server_phytomni.agents.data.nl2sql import (
    Nl2SqlRequest,
    _execute_nl2sql_via_relay,
)
from mcp_server_phytomni.agents.knowledge import retrieval
from mcp_server_phytomni.agents.knowledge.retrieval import (
    _rerank_batch,
    _retrieve_scope_docs,
)

pytestmark = pytest.mark.agent

# ``data/__init__`` re-exports the ``nl2sql`` function, which shadows the
# submodule under a plain ``import ... as`` alias; import_module returns
# the real module object so monkeypatching its globals reaches the
# relay helper.
nl2sql_mod = importlib.import_module("mcp_server_phytomni.agents.data.nl2sql")

# The boundary helpers take an ``AsyncClient`` they ignore in relay mode
# (the relay path uses ``current_relay_client``); ``Any`` lets the tests
# pass a placeholder without tripping the ``AsyncClient`` parameter type.
_UNUSED_CLIENT: Any = None


@dataclass(frozen=True)
class _FakeRelay:
    """Records relay calls and replays a canned JSON response.

    A frozen dataclass with the ``RelayClient`` policy fields so the
    NL2SQL path's ``dataclasses.replace(current_relay_client(), ...)``
    yields an instance sharing this one's ``calls`` list, keeping the
    recorded calls visible to the test.
    """

    response: Any
    calls: list[dict[str, Any]] = field(default_factory=list)
    base_url: str = "https://relay.test"
    api_key: Any = None
    timeout: float = 1.0
    max_retries: int = 0
    retriable_codes: tuple[int, ...] = ()

    async def post_json(
        self,
        relay_path: str,
        *,
        json_body: Any,
        message: str,
        extra_headers: Optional[dict[str, str]] = None,
    ) -> Any:
        """Record a relay POST and return the canned response."""
        self.calls.append(
            {
                "method": "POST",
                "path": relay_path,
                "body": json_body,
                "message": message,
                "extra_headers": extra_headers,
            }
        )
        return self.response

    async def get_json(
        self,
        relay_path: str,
        *,
        message: str,
        query: Optional[dict[str, str]] = None,
    ) -> Any:
        """Record a relay GET and return the canned response."""
        self.calls.append(
            {
                "method": "GET",
                "path": relay_path,
                "message": message,
                "query": query,
            }
        )
        return self.response


def _patch_relay(monkeypatch, module: Any, response: Any) -> _FakeRelay:
    """Patch ``current_relay_client`` in ``module`` to a recording fake."""
    relay = _FakeRelay(response)
    monkeypatch.setattr(module, "current_relay_client", lambda: relay)
    return relay


async def test_retrieve_scope_docs_routes_through_relay(monkeypatch):
    """Relay mode posts the retrieve body to /v1/relay/retrieve/search."""
    _retrieve_scope_docs.cache_clear()
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    relay = _patch_relay(monkeypatch, retrieval, {"doc_list": [{"id": "d1"}]})

    docs = await _retrieve_scope_docs(
        _UNUSED_CLIENT,
        user_query="leaf growth",
        retrieve_url="https://operator.invalid/search",
        repo_id="repo-1",
        scope="document",
        page_num=1,
        page_size=3,
        filter_string=None,
        extra_repo_ids=(),
        timeout=1.0,
        max_retries=0,
        retriable_codes=(503,),
    )

    assert docs == [{"id": "d1"}]
    assert relay.calls[0]["path"] == "retrieve/search"
    assert relay.calls[0]["body"]["repo_id"] == "repo-1"
    assert relay.calls[0]["body"]["content"] == "leaf growth"


async def test_rerank_batch_routes_through_relay(monkeypatch):
    """Relay mode posts the rerank body to /v1/relay/rerank/rank."""
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    relay = _patch_relay(
        monkeypatch, retrieval, {"rank_result": [{"id": "r1"}]}
    )

    ranked = await _rerank_batch(
        _UNUSED_CLIENT,
        user_query="leaf growth",
        docs_batch=[{"id": "d1"}],
        rerank_url="https://operator.invalid/rerank",
        top_n=3,
        timeout=1.0,
        max_retries=0,
        retriable_codes=(503,),
    )

    assert ranked == [{"id": "r1"}]
    assert relay.calls[0]["path"] == "rerank/rank"
    assert relay.calls[0]["body"]["query"] == "leaf growth"
    assert relay.calls[0]["body"]["docs"] == [{"id": "d1"}]


async def test_nl2sql_routes_through_relay_with_workspace_header(monkeypatch):
    """Relay-mode NL2SQL posts to /v1/relay/database/nl2sql.

    It forwards X-Workspace-Id and does not mint an operator IAM token
    (the relay injects it); the body carries the resolved payload.
    """
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    relay = _patch_relay(
        monkeypatch, nl2sql_mod, {"data": [{"sql": "SELECT 1"}]}
    )

    request = Nl2SqlRequest(
        database_url="https://operator.invalid/nl2sql",
        workspace_id="ws-1",
        payload_data={"message_content": "list genes", "dialog_id": "d0"},
        timeout=1.0,
        retriable_codes=(503,),
        max_retries=0,
    )
    result = await _execute_nl2sql_via_relay(request)

    assert result == {"data": [{"sql": "SELECT 1"}]}
    assert relay.calls[0]["path"] == "database/nl2sql"
    assert relay.calls[0]["extra_headers"] == {"X-Workspace-Id": "ws-1"}
    assert relay.calls[0]["body"]["message_content"] == "list genes"


async def test_task_status_routes_through_relay(monkeypatch):
    """Relay-mode task status GETs /v1/relay/analysis/{task_id}."""
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    relay = _patch_relay(monkeypatch, task_ops, {"status": "running"})

    result = await task_ops.task_status("task-9")

    assert result == {"status": "running"}
    assert relay.calls[0]["method"] == "GET"
    assert relay.calls[0]["path"] == "analysis/task-9"


async def test_task_log_routes_through_relay_with_task_name_query(monkeypatch):
    """Relay-mode task log GETs /v1/relay/analysis/{id}/logs with query."""
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    relay = _patch_relay(monkeypatch, task_ops, {"log": "ok"})

    result = await task_ops.task_log("task-9", compute_resource="small")

    assert result == {"log": "ok"}
    assert relay.calls[0]["path"] == "analysis/task-9/logs"
    assert relay.calls[0]["query"] == {"task_name": "analyst-agents-small"}


async def test_task_delete_routes_through_relay(monkeypatch):
    """Relay-mode terminate POSTs /v1/relay/analysis/{id}/terminate."""
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    relay = _patch_relay(monkeypatch, task_ops, {"ok": True})

    result = await task_ops.task_delete("task-9")

    assert result == "Delete task task-9 success."
    assert relay.calls[0]["method"] == "POST"
    assert relay.calls[0]["path"] == "analysis/task-9/terminate"
    assert relay.calls[0]["body"] == {"force": True}
