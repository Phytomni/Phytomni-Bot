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

import asyncio
import importlib
from dataclasses import dataclass, field
from typing import Any

import pytest

from mcp_server_phytomni.agents.analyst import graph as analyst_graph
from mcp_server_phytomni.agents.analyst import task_ops
from mcp_server_phytomni.agents.brief_gene.pipeline import run_bi_api
from mcp_server_phytomni.agents.data.nl2sql import (
    Nl2SqlRequest,
    _execute_nl2sql_via_relay,
)
from mcp_server_phytomni.agents.deep_genome.dispatch import (
    DeepGenomeDispatchMixin,
)
from mcp_server_phytomni.agents.deep_genome.profile import _post_bi_sql
from mcp_server_phytomni.agents.evolution import agent as evolution_agent
from mcp_server_phytomni.agents.evolution.agent import find_spa_taxids
from mcp_server_phytomni.agents.knowledge import retrieval
from mcp_server_phytomni.agents.knowledge.retrieval import (
    _rerank_batch,
    _RerankBatchRequest,
    _retrieve_scope_docs,
    _RetrieveScopeKey,
    _RetrieveScopeRequest,
)
from mcp_server_phytomni.agents.shared import sql as shared_sql
from mcp_server_phytomni.agents.shared.sql import relay_bi_query
from mcp_server_phytomni.common.http import JsonPostRetry
from mcp_server_phytomni.common.relay_client import RelayRequestOptions
from mcp_server_phytomni.runtime.outbound import OutboundPoolName
from tests.support.outbound_fakes import (
    bounded_await,
    bounded_wait_for_event,
    managed_async_task,
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
        json_body: Any,
        *,
        pool: OutboundPoolName,
        options: RelayRequestOptions,
    ) -> Any:
        """Record a relay POST and return the canned response."""
        self.calls.append(
            {
                "method": "POST",
                "path": relay_path,
                "pool": pool,
                "body": json_body,
                "message": options.message,
                "extra_headers": options.extra_headers,
                "timeout": options.request_timeout,
            }
        )
        return self.response

    async def get_json(
        self,
        relay_path: str,
        *,
        pool: OutboundPoolName,
        options: RelayRequestOptions,
        query: dict[str, str] | None = None,
    ) -> Any:
        """Record a relay GET and return the canned response."""
        self.calls.append(
            {
                "method": "GET",
                "path": relay_path,
                "pool": pool,
                "message": options.message,
                "query": query,
                "timeout": options.request_timeout,
            }
        )
        return self.response

    async def post_data(
        self,
        relay_path: str,
        data: Any,
        *,
        pool: OutboundPoolName,
        options: RelayRequestOptions,
    ) -> Any:
        """Record a relay form-data POST and return the canned response."""
        self.calls.append(
            {
                "method": "POST_DATA",
                "path": relay_path,
                "pool": pool,
                "data": data,
                "message": options.message,
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
    relay = _patch_relay(
        monkeypatch,
        retrieval,
        {
            "doc_list": [
                {
                    "chunk_id": "d1",
                    "title": "Title",
                    "content": "Content",
                }
            ]
        },
    )

    docs = await _retrieve_scope_docs(
        _RetrieveScopeKey(
            contract_version=2,
            user_query="leaf growth",
            repo_id="repo-1",
            scope="document",
            page_num=1,
            page_size=3,
            filter_string=None,
            extra_repo_ids=(),
        ),
        _RetrieveScopeRequest(
            client=_UNUSED_CLIENT,
            retrieve_url="https://operator.invalid/search",
            timeout=1.0,
            max_retries=0,
            retriable_codes=(503,),
        ),
    )

    assert docs == [
        {
            "chunk_id": "d1",
            "title": "Title",
            "content": "Content",
        }
    ]
    assert relay.calls[0]["path"] == "retrieve/search"
    assert relay.calls[0]["pool"] is OutboundPoolName.RETRIEVAL
    assert relay.calls[0]["body"]["repo_id"] == "repo-1"
    assert relay.calls[0]["body"]["content"] == "leaf growth"
    assert relay.calls[0]["timeout"] == 1.0


async def test_rerank_batch_routes_through_relay(monkeypatch):
    """Relay mode posts the rerank body to /v1/relay/rerank/rank."""
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    relay = _patch_relay(
        monkeypatch, retrieval, {"rank_result": [{"id": "d1", "score": 0.9}]}
    )

    ranked = await _rerank_batch(
        _UNUSED_CLIENT,
        _RerankBatchRequest(
            user_query="leaf growth",
            docs_batch=[{"id": "d1", "title": "Title", "content": "Content"}],
            rerank_url="https://operator.invalid/rerank",
            top_n=3,
            timeout=1.0,
            max_retries=0,
            retriable_codes=(503,),
        ),
    )

    assert ranked == [{"id": "d1", "score": 0.9}]
    assert relay.calls[0]["path"] == "rerank/rank"
    assert relay.calls[0]["pool"] is OutboundPoolName.RERANK
    assert relay.calls[0]["body"]["query"] == "leaf growth"
    assert relay.calls[0]["body"]["docs"] == [
        {"id": "d1", "title": "Title", "content": "Content"}
    ]
    assert relay.calls[0]["timeout"] == 1.0


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
    assert relay.calls[0]["pool"] is OutboundPoolName.NL2SQL
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
    assert relay.calls[0]["pool"] is OutboundPoolName.ANALYSIS_STATUS


async def test_analyst_submit_routes_through_relay(monkeypatch):
    """Relay-mode analyst submit POSTs to /v1/relay/analysis/tasks.

    The submit headers carry no operator IAM token (the relay injects
    it), and the job is forwarded through the relay rather than posted to
    the operator ANALYSIS_URL.
    """
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    relay = _patch_relay(monkeypatch, analyst_graph, {"id": "task-abc"})

    async def _no_token(**_kwargs: Any) -> str:
        raise AssertionError("get_token must not run in relay mode")

    monkeypatch.setattr(analyst_graph, "get_token", _no_token)
    mixin = analyst_graph.AnalystGraphMixin
    submit_headers = getattr(mixin, "_submit_headers")
    post_submit_job = getattr(mixin, "_post_submit_job")

    headers = await submit_headers(object())
    assert "X-Auth-Token" not in headers
    assert headers["Content-Type"] == "application/json"

    result = await post_submit_job(
        object(),
        headers,
        {"job": 1},
        "job-1",
        "agent_data/user_data/cust42/runs/x/output/",
    )

    assert result["task_id"] == "task-abc"
    assert result["task_status"] == "PENDING"
    assert result["output_dir"] == "agent_data/user_data/cust42/runs/x/output/"
    assert relay.calls[0]["method"] == "POST"
    assert relay.calls[0]["path"] == "analysis/tasks"
    assert relay.calls[0]["pool"] is OutboundPoolName.ANALYSIS_CONTROL
    assert relay.calls[0]["body"] == {"job": 1}


async def test_task_log_routes_through_relay_with_task_name_query(monkeypatch):
    """Relay-mode task log GETs /v1/relay/analysis/{id}/logs with query."""
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    relay = _patch_relay(monkeypatch, task_ops, {"log": "ok"})

    result = await task_ops.task_log("task-9", compute_resource="small")

    assert result == {"log": "ok"}
    assert relay.calls[0]["path"] == "analysis/task-9/logs"
    assert relay.calls[0]["pool"] is OutboundPoolName.ANALYSIS_STATUS
    assert relay.calls[0]["query"] == {"task_name": "analyst-agents-small"}


async def test_task_delete_routes_through_relay(monkeypatch):
    """Relay-mode terminate POSTs /v1/relay/analysis/{id}/terminate."""
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    relay = _patch_relay(monkeypatch, task_ops, {"ok": True})

    result = await task_ops.task_delete("task-9")

    assert result == "Delete task task-9 success."
    assert relay.calls[0]["method"] == "POST"
    assert relay.calls[0]["path"] == "analysis/task-9/terminate"
    assert relay.calls[0]["pool"] is OutboundPoolName.ANALYSIS_CONTROL
    assert relay.calls[0]["body"] == {"force": True}


async def test_relay_bi_query_posts_to_bi_query_route(monkeypatch):
    """The shared BI relay seam posts the SQL body to /v1/relay/bi/query."""
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    relay = _patch_relay(monkeypatch, shared_sql, {"rows": [1]})

    result = await relay_bi_query("SELECT 1", message="BI query failed")

    assert result == {"rows": [1]}
    assert relay.calls[0]["path"] == "bi/query"
    assert relay.calls[0]["pool"] is OutboundPoolName.BI
    assert relay.calls[0]["body"] == {"sql": "SELECT 1", "returnType": "json"}


async def test_run_bi_api_routes_through_relay(monkeypatch):
    """brief_gene run_bi_api routes BI through the relay in relay mode."""
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    relay = _patch_relay(monkeypatch, shared_sql, {"data": "x"})

    result = await run_bi_api("SELECT 2")

    assert result == {"data": "x"}
    assert relay.calls[0]["path"] == "bi/query"
    assert relay.calls[0]["body"]["sql"] == "SELECT 2"


async def test_post_bi_sql_routes_through_relay(monkeypatch):
    """deep_genome _post_bi_sql routes BI through the relay in relay mode."""
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    relay = _patch_relay(monkeypatch, shared_sql, {"records": []})

    result = await _post_bi_sql("SELECT 3", request_timeout=1.0)

    assert result == {"records": []}
    assert relay.calls[0]["path"] == "bi/query"
    assert relay.calls[0]["body"]["sql"] == "SELECT 3"


async def test_bi_json_routes_through_relay(monkeypatch):
    """deep_genome dispatch _bi_json routes BI through the relay."""
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    relay = _patch_relay(monkeypatch, shared_sql, {"value": 1})

    class _BiProbe(DeepGenomeDispatchMixin):
        """Test subclass exposing the protected _bi_json publicly."""

        async def call_bi_json(self, sql: str) -> dict:
            """Invoke the protected BI JSON helper from inside the class."""
            return await self._bi_json(sql)

    result = await _BiProbe().call_bi_json("SELECT 4")

    assert result == {"value": 1}
    assert relay.calls[0]["path"] == "bi/query"
    assert relay.calls[0]["body"]["sql"] == "SELECT 4"


async def test_find_spa_taxids_routes_through_relay(monkeypatch):
    """Relay-mode SPA-FAQ GETs /v1/relay/spa-faq/{repo_id} with query."""
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    relay = _patch_relay(
        monkeypatch,
        evolution_agent,
        {"total": 1, "records": [{"answer": "9606.1"}]},
    )

    taxids = await find_spa_taxids("Arabidopsis", request_timeout=1.0)

    assert taxids == ["9606"]
    assert relay.calls[0]["method"] == "GET"
    assert relay.calls[0]["path"].startswith("spa-faq/")
    assert relay.calls[0]["pool"] is OutboundPoolName.SPA_FAQ
    assert relay.calls[0]["query"]["question"] == "Arabidopsis"
    assert relay.calls[0]["query"]["page_size"] == "10"
    assert relay.calls[0]["timeout"] == 1.0


async def test_spa_faq_waits_for_iam_before_target_pool(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
) -> None:
    """A direct SPA lookup does not occupy capacity while IAM is pending."""
    iam_entered = asyncio.Event()
    release_iam = asyncio.Event()

    async def blocked_token(**_kwargs: Any) -> str:
        iam_entered.set()
        await release_iam.wait()
        return "test-token"

    monkeypatch.setattr(evolution_agent, "get_token", blocked_token)
    monkeypatch.setattr(evolution_agent, "relay_mode_enabled", lambda: False)
    outbound_runtime.transport.enqueue(
        content=b'{"total":1,"records":[{"answer":"9606.1"}]}'
    )
    async with managed_async_task(
        find_spa_taxids("Arabidopsis", request_timeout=1.0),
        release_events=(release_iam,),
    ) as task:
        await bounded_wait_for_event(iam_entered, task=task)
        snapshot = outbound_runtime.runtime.pools.snapshot(
            OutboundPoolName.SPA_FAQ
        )
        assert snapshot.started == 0
        assert snapshot.in_use == 0
        assert snapshot.waiting == 0

        release_iam.set()
        assert await bounded_await(task) == ["9606"]


async def test_bi_query_operator_mode_runs_gauss_query(monkeypatch):
    """Outside relay mode bi_query runs the SQL directly via gauss_query."""
    monkeypatch.delenv("PHYTOMNI_RELAY_MODE", raising=False)
    monkeypatch.delenv("RELAY_MODE", raising=False)
    captured: dict[str, Any] = {}

    async def fake_gauss_query(sql: str, **kwargs: Any) -> Any:
        captured["sql"] = sql
        captured["request_timeout"] = kwargs["request_timeout"]
        return {"message": "ok", "data": [{"x": 9}]}

    monkeypatch.setattr(shared_sql, "gauss_query", fake_gauss_query)

    result = await shared_sql.bi_query(
        "SELECT 9",
        retry=JsonPostRetry(
            timeout=1.0,
            max_retries=0,
            retriable_codes=(503,),
            message="BI query failed",
        ),
    )

    assert result == {"message": "ok", "data": [{"x": 9}]}
    assert captured["sql"] == "SELECT 9"
    assert captured["request_timeout"] == 1.0
