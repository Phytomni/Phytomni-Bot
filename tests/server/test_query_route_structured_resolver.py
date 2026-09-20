# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Expert route tests for post-selection structured-id resolvers."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from tests.server.test_query_route import (
    SimpleNamespace,
    ToolSelection,
    _conversation_envelope,
    _patch_select,
    _post_context_route,
    _post_query_route,
)
from tests.support.execution_contract_fixtures import (
    assert_failed_execution_projection,
)
from tests.support.resolver_fakes import register_gene_capture

import mcp_server_phytomni.api.app as api_app
from mcp_server_phytomni import server
from mcp_server_phytomni.agents.deep_genome.resolve_query import (
    DeepGenomeResolveError,
)
from mcp_server_phytomni.agents.expert import router as expert_router
from mcp_server_phytomni.api.lifecycle_contract import empty_agent_result
from mcp_server_phytomni.runtime.run_registry import RunRegistry

pytestmark = pytest.mark.server


def _capture_invoke(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status_code: int = 200,
    status: str = "succeeded",
) -> dict[str, Any]:
    """Capture native Expert dispatch arguments without running an agent."""
    captured: dict[str, Any] = {}

    async def fake_invoke(**kwargs: Any) -> tuple[dict[str, Any], int]:
        captured.update(kwargs)
        return (
            {
                "id": "run-1",
                "run_id": "run-1",
                "object": "agent.run",
                "agent": kwargs["agent"],
                "status": status,
                "task_ids": [],
                "result": empty_agent_result(),
            },
            status_code,
        )

    monkeypatch.setattr(api_app, "_invoke_agent_run", fake_invoke)
    return captured


async def _explode_routing(**_kwargs: Any) -> object:
    """Fail if Expert still calls the routing model."""
    raise AssertionError("structured Expert path must not call the model")


@pytest.mark.parametrize(
    "case",
    [
        ("DeepGenomeAgent", "deep_genome", "resolve_gene_id"),
        ("DigitalDesignAgent", "design", "resolve_gene_id"),
        ("GeneNetworkAgent", "network", "resolve_to_id"),
    ],
)
async def test_route_missing_structured_ids_opts_into_resolver(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str, str],
) -> None:
    """A skipped structured pin opens the native resolver flag."""
    tool_name, slug, flag = case
    captured = _capture_invoke(monkeypatch)
    monkeypatch.setattr(
        expert_router, "complete_expert_routing", _explode_routing
    )

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "rice CAB1 function",
            "allowed_tools": [tool_name],
            "forced_tool": tool_name,
        },
    )

    assert response.status_code == 200
    assert captured["agent"] == slug
    assert captured["arguments"][flag] is True
    assert captured["arguments"]["user_query"] == "rice CAB1 function"


async def test_route_complete_structured_ids_skip_resolver(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A complete routing extraction is dispatched without resolver flags."""
    captured = _capture_invoke(monkeypatch)
    _patch_select(
        monkeypatch,
        ToolSelection(
            "DeepGenomeAgent",
            {"gene_id": "Os01g0177400", "species_code": "osa"},
        ),
    )

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "rice CAB1 function",
            "allowed_tools": ["DeepGenomeAgent", "ChatAgent"],
        },
    )

    assert response.status_code == 200
    assert captured["agent"] == "deep_genome"
    assert captured["arguments"]["gene_id"] == "Os01g0177400"
    assert captured["arguments"]["species_code"] == "osa"
    assert "resolve_gene_id" not in captured["arguments"]


async def test_route_brief_gene_does_not_open_resolver(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expert BriefGene still forwards user_query without resolve_gene_id."""
    captured = _capture_invoke(monkeypatch)
    monkeypatch.setattr(
        expert_router, "complete_expert_routing", _explode_routing
    )

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "rice CAB1 function",
            "allowed_tools": ["BriefGeneAgent"],
            "forced_tool": "BriefGeneAgent",
        },
    )

    assert response.status_code == 200
    assert captured["agent"] == "brief_gene"
    assert captured["arguments"]["user_query"] == "rice CAB1 function"
    assert "resolve_gene_id" not in captured["arguments"]


async def test_context_explicit_deep_genome_opts_into_resolver(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A V1 @DeepGenome pin fills missing ids through the native resolver."""
    captured = _capture_invoke(monkeypatch, status_code=202, status="running")
    monkeypatch.setattr(api_app, "select_agent_tool", _explode_routing)
    envelope = _conversation_envelope(
        requested_agent_id="DeepGenomeAgent",
        allowed_agent_ids=["DeepGenomeAgent"],
    )
    envelope["current_message"]["content"] = "rice CAB1 function"
    envelope["history_delta"][0]["content"] = "rice CAB1 function"

    response = await _post_context_route(
        api_client,
        issued_api_key,
        "DeepGenomeAgent",
        envelope,
    )

    assert response.status_code == 202
    assert captured["agent"] == "deep_genome"
    assert captured["arguments"]["resolve_gene_id"] is True
    assert captured["arguments"]["user_query"] == "rice CAB1 function"


async def test_route_missing_deep_genome_ids_resolve_then_dispatch(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """The native DeepGenome resolver runs after an Expert skip."""
    captured: dict[str, Any] = {}
    queries: list[str] = []
    register_gene_capture(
        monkeypatch,
        agent_slug="deep_genome",
        tool_name=server.PhytomniAgents.DEEP_GENOME_AGENT.value,
        captured=captured,
        answer="deep genome submission",
    )

    async def fake_resolve(raw_query: str, **_kwargs: Any) -> Any:
        queries.append(raw_query)
        return SimpleNamespace(gene_id="Os01g0177400", species_code="osa")

    monkeypatch.setattr(
        api_app, "resolve_deep_genome_user_query", fake_resolve
    )
    monkeypatch.setattr(
        expert_router, "complete_expert_routing", _explode_routing
    )

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "rice CAB1 function",
            "allowed_tools": ["DeepGenomeAgent"],
            "forced_tool": "DeepGenomeAgent",
        },
    )

    assert response.status_code == 202
    assert response.json()["agent"] == "deep_genome"
    assert queries == ["rice CAB1 function"]
    assert captured == {
        "species_code": "osa",
        "gene_id": "Os01g0177400",
    }
    assert RunRegistry(tasks_db_path).list_runs(owner="u1")


async def test_route_deep_genome_resolver_failure_settles_failed_run(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A selected async Agent keeps one sanitized failed execution."""

    async def fail_resolve(raw_query: str, **_kwargs: Any) -> Any:
        del raw_query
        raise DeepGenomeResolveError("no valid candidate")

    monkeypatch.setattr(
        api_app, "resolve_deep_genome_user_query", fail_resolve
    )
    monkeypatch.setattr(
        expert_router, "complete_expert_routing", _explode_routing
    )

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "ambiguous gene",
            "allowed_tools": ["DeepGenomeAgent"],
            "forced_tool": "DeepGenomeAgent",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["agent"] == "deep_genome"
    assert body["status"] == "failed"
    assert body["task_ids"] == []
    assert "no valid candidate" not in response.text

    records = RunRegistry(tasks_db_path).list_runs(owner="u1")
    assert len(records) == 1
    record = records[0]
    assert record.spec.run_id == body["run_id"]
    assert record.status == "failed"
    assert record.task_ids == ()
    assert_failed_execution_projection(tasks_db_path, record)
