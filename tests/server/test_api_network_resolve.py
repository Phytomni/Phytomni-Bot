# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Route tests for the resolve_to_id flag on network native runs.

GeneNetwork is not exposed under /v1/chat/completions; these tests
pin the /v1/agents/network/runs path. Uses the ``resolve_to_id``
flag and TO-id metadata key, not ``resolve_gene_id``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from tests.support.execution_contract_fixtures import (
    assert_failed_execution_projection,
)
from tests.support.handler_fakes import network_run_arguments
from tests.support.resolver_fakes import assert_invalid_argument_response

from mcp_server_phytomni import server
from mcp_server_phytomni.agents.network import resolve_query as nw_module
from mcp_server_phytomni.agents.network.resolve_query import (
    GeneNetworkResolveError,
    GeneNetworkResolveResult,
    GeneNetworkToIdCandidate,
)
from mcp_server_phytomni.api import app as api_app
from mcp_server_phytomni.runtime.run_registry import RunRecord, RunRegistry
from mcp_server_phytomni.runtime.submit_recorder import records_submission

pytestmark = pytest.mark.server


def _resolved(
    to_id: str, raw: str, species_code: str = "osa"
) -> GeneNetworkResolveResult:
    """Return a canned network resolver result.

    ``species_code`` defaults to ``"osa"`` (rice) for fixture brevity;
    tests that wire-shape-check the metadata block can override per
    case when the expected value differs from the default.
    """
    return GeneNetworkResolveResult(
        to_id=to_id,
        raw_query=raw,
        species_code=species_code,
        candidates=[
            GeneNetworkToIdCandidate(
                to_id=to_id, confidence=1.0, species_code=species_code
            )
        ],
    )


def _stub_network_handler(
    monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]
) -> None:
    """Replace GeneNetworkAgent handler with a minimal capturing stub."""

    async def fake(args: Any) -> dict[str, Any]:
        captured["species_code"] = args.species_code
        captured["to_id"] = args.to_id
        return {
            "network_task": {
                "task_id": "network-resolver-task",
                "output_dir": "tenant/network",
            }
        }

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.GENE_NETWORK_AGENT.value,
        records_submission("network")(fake),
    )


async def _post_run(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    agent_slug: str,
    arguments: dict[str, Any],
) -> httpx.Response:
    """POST one native /v1/agents/{slug}/runs with arguments."""
    auth_header = {"Authorization": f"Bearer {issued_api_key}"}
    payload = {"arguments": arguments}
    return await api_client.post(
        f"/v1/agents/{agent_slug}/runs", headers=auth_header, json=payload
    )


async def _wait_for_background_run(
    tasks_db_path: str,
    run_id: str,
    *,
    failed: bool = False,
) -> RunRecord:
    """Poll one accepted Network run until it attaches work or fails."""
    registry = RunRegistry(tasks_db_path)
    for _ in range(100):
        record = registry.get_run(run_id, owner="u1")
        if record is not None:
            if failed and record.status == "failed":
                return record
            if not failed and record.task_ids:
                return record
        await asyncio.sleep(0)
    pytest.fail("background Network run did not settle")


def _assert_v2_failed_projection(
    tasks_db_path: str, record: RunRecord
) -> None:
    """Assert failure is owned by the canonical V2 execution journal."""
    assert record.error is None
    assert_failed_execution_projection(tasks_db_path, record)


async def test_native_runs_resolves_when_flag_true(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """flag=true rewrites user_query into to_id and stamps metadata."""
    captured: dict[str, Any] = {}
    resolver_calls: list[str] = []
    _stub_network_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, network_config: Any, sensitive_config: Any
    ) -> GeneNetworkResolveResult:
        del network_config, sensitive_config
        resolver_calls.append(raw_query)
        return _resolved("TO:0000207", raw_query)

    monkeypatch.setattr(api_app, "resolve_network_user_query", fake_resolve)

    response = await _post_run(
        api_client,
        issued_api_key,
        "network",
        {
            "user_query": "rice plant height trait",
            "resolve_to_id": True,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["task_ids"] == ["network-resolver-task"]
    await _wait_for_background_run(tasks_db_path, body["run_id"])
    assert captured["species_code"] == "osa"
    assert captured["to_id"] == "TO:0000207"
    assert resolver_calls == ["rice plant height trait"]
    metadata = body["result"]["formatted"].get("metadata") or {}
    assert metadata.get("original_query") == "rice plant height trait"
    assert metadata.get("resolved_to_id") == "TO:0000207"
    assert metadata.get("resolved_species_code") == "osa"
    assert metadata.get("resolve_to_id") is True


async def test_native_runs_defaults_bare_to_id_to_rice_without_llm(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A bare TO id resolves to rice before the Network handler runs."""
    captured: dict[str, Any] = {}
    _stub_network_handler(monkeypatch, captured)

    async def fail_phyto_chat(**_kwargs: Any) -> dict[str, Any]:
        pytest.fail("bare TO ids must not invoke the LLM resolver")

    monkeypatch.setattr(nw_module, "phyto_chat", fail_phyto_chat)

    response = await _post_run(
        api_client,
        issued_api_key,
        "network",
        {
            "user_query": "TO:0000227",
            "resolve_to_id": True,
        },
    )

    assert response.status_code == 202
    body = response.json()
    await _wait_for_background_run(tasks_db_path, body["run_id"])
    assert captured == {
        "species_code": "osa",
        "to_id": "TO:0000227",
    }
    metadata = body["result"]["formatted"].get("metadata") or {}
    assert metadata.get("original_query") == "TO:0000227"
    assert metadata.get("resolved_to_id") == "TO:0000227"
    assert metadata.get("resolved_species_code") == "osa"


async def test_native_runs_skips_resolver_when_flag_false(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """flag=false leaves to_id as-is from the structured request."""
    captured: dict[str, Any] = {}
    resolver_calls: list[str] = []
    _stub_network_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, network_config: Any, sensitive_config: Any
    ) -> GeneNetworkResolveResult:
        del network_config, sensitive_config
        resolver_calls.append(raw_query)
        return _resolved("UNUSED", raw_query)

    monkeypatch.setattr(api_app, "resolve_network_user_query", fake_resolve)

    response = await _post_run(
        api_client,
        issued_api_key,
        "network",
        network_run_arguments(),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["task_ids"] == ["network-resolver-task"]
    record = await _wait_for_background_run(tasks_db_path, body["run_id"])
    assert set(record.task_ids) == {"network-resolver-task"}
    assert captured["to_id"] == "TO:0000207"
    assert not resolver_calls


async def test_native_runs_rejects_resolve_to_id_on_non_network_agent(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """resolve_to_id on /v1/agents/chat/runs returns 400."""
    del tasks_db_path
    captured: dict[str, Any] = {}
    resolver_calls: list[str] = []

    async def fake_chat(args: Any) -> dict[str, Any]:
        captured["user_query"] = args.user_query
        return {"answer": "chat answer", "doc_list": []}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake_chat,
    )

    async def fake_resolve(
        raw_query: str, *, network_config: Any, sensitive_config: Any
    ) -> GeneNetworkResolveResult:
        del network_config, sensitive_config
        resolver_calls.append(raw_query)
        return _resolved("UNUSED", raw_query)

    monkeypatch.setattr(api_app, "resolve_network_user_query", fake_resolve)

    response = await _post_run(
        api_client,
        issued_api_key,
        "chat",
        {
            "user_query": "rice height",
            "resolve_to_id": True,
        },
    )

    assert_invalid_argument_response(response)
    assert not resolver_calls
    assert "user_query" not in captured


async def test_native_runs_missing_user_query_settles_failed(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """flag=true without user_query settles the accepted run failed."""
    captured: dict[str, Any] = {}
    resolver_calls: list[str] = []
    _stub_network_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, network_config: Any, sensitive_config: Any
    ) -> GeneNetworkResolveResult:
        del network_config, sensitive_config
        resolver_calls.append(raw_query)
        return _resolved("UNUSED", raw_query)

    monkeypatch.setattr(api_app, "resolve_network_user_query", fake_resolve)

    response = await _post_run(
        api_client,
        issued_api_key,
        "network",
        {
            "species_code": "osa",
            "resolve_to_id": True,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["task_ids"] == []
    record = await _wait_for_background_run(
        tasks_db_path, body["run_id"], failed=True
    )
    assert record.task_ids == ()
    _assert_v2_failed_projection(tasks_db_path, record)
    assert not resolver_calls
    assert "to_id" not in captured


async def test_native_runs_resolver_failure_settles_failed(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """GeneNetworkResolveError settles the accepted run failed safely."""
    captured: dict[str, Any] = {}
    _stub_network_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, network_config: Any, sensitive_config: Any
    ) -> GeneNetworkResolveResult:
        del raw_query, network_config, sensitive_config
        raise GeneNetworkResolveError(
            "no valid candidate (LLM proposed ids not in the catalog)"
        )

    monkeypatch.setattr(api_app, "resolve_network_user_query", fake_resolve)

    response = await _post_run(
        api_client,
        issued_api_key,
        "network",
        {
            "user_query": "ambiguous trait",
            "resolve_to_id": True,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["task_ids"] == []
    record = await _wait_for_background_run(
        tasks_db_path, body["run_id"], failed=True
    )
    assert record.task_ids == ()
    _assert_v2_failed_projection(tasks_db_path, record)
    assert "to_id" not in captured


async def test_native_runs_blank_species_code_settles_failed(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """LLM blank species_code settles the accepted run failed safely.

    The network resolver owns its own empty-string guard (independent
    of the BGA path): when the LLM omits ``species_code`` the
    resolver raises ``GeneNetworkResolveError`` and the API layer
    settles the reserved run failed. Pinning this case here keeps the
    contract regression-proofed at the HTTP boundary.
    """
    captured: dict[str, Any] = {}
    _stub_network_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, network_config: Any, sensitive_config: Any
    ) -> GeneNetworkResolveResult:
        del raw_query, network_config, sensitive_config
        raise GeneNetworkResolveError(
            "species_code could not be determined from query: baz"
        )

    monkeypatch.setattr(api_app, "resolve_network_user_query", fake_resolve)

    response = await _post_run(
        api_client,
        issued_api_key,
        "network",
        {
            "user_query": "baz",
            "resolve_to_id": True,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["task_ids"] == []
    record = await _wait_for_background_run(
        tasks_db_path, body["run_id"], failed=True
    )
    assert record.task_ids == ()
    _assert_v2_failed_projection(tasks_db_path, record)
    assert "to_id" not in captured
    assert "species_code" not in captured
