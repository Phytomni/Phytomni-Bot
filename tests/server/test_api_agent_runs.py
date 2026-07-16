# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the native ``/v1/agents`` and ``/v1/agents/{slug}/runs``.

Covers listing, auth enforcement, slug-not-found, sync-agent run with
``origin="local"`` written here, and remote-agent run with the
chokepoint-minted ``origin="remote"`` run_id read back via
``tasks.run_id``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from mcp_server_phytomni import server
from mcp_server_phytomni.mcp.schemas import (
    AGENT_TOOL_DEFINITIONS,
    PhytomniAgents,
)
from mcp_server_phytomni.runtime import (
    submit_recorder as submit_recorder_module,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry
from mcp_server_phytomni.runtime.submit_recorder import records_submission

pytestmark = pytest.mark.server


@dataclass(frozen=True)
class _RemoteCase:
    """One parametrize row for the remote-agent chokepoint contract."""

    slug: str
    tool_name: str
    stub_return: dict[str, Any]
    arguments: dict[str, Any]
    expected_task_ids: set[str]


def _canonical_agent_slug(tool: PhytomniAgents) -> str:
    """Derive the native API slug from the canonical MCP enum member."""
    candidate = tool.name.removesuffix("_AGENT").lower()
    return {
        "in_silico_research": "research",
        "digital_design": "design",
        "gene_network": "network",
    }.get(candidate, candidate)


async def test_list_agents_returns_all_ten(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
) -> None:
    """List both sync and remote agents with their tool and origin."""
    response = await api_client.get(
        "/v1/agents",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    slugs = {row["slug"] for row in body["data"]}
    assert slugs == {
        "chat",
        "knowledge",
        "data",
        "review",
        "brief_gene",
        "analyst",
        "deep_genome",
        "research",
        "design",
        "network",
    }
    origins = {row["slug"]: row["origin"] for row in body["data"]}
    assert origins["chat"] == "local"
    assert origins["analyst"] == "remote"


async def test_list_agents_requires_auth(
    api_client: httpx.AsyncClient,
) -> None:
    """Unauthorized callers see the unified 401 envelope."""
    response = await api_client.get("/v1/agents")
    assert response.status_code == 401


async def test_list_agents_includes_legacy_aliases_for_web_tools(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
) -> None:
    """Every /v1/agents row exposes a ``legacy_aliases`` list.

    The route itself never accepts the listed aliases as routing
    slugs; chat-ai and Phytomni-Web Go consume the metadata to build
    their own alias→slug translation table. Bot-added agents that
    have no Web counterpart return an empty list so the response
    shape stays uniform across every row.
    """
    response = await api_client.get(
        "/v1/agents",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 200
    rows_by_slug = {row["slug"]: row for row in response.json()["data"]}

    for slug, row in rows_by_slug.items():
        assert "legacy_aliases" in row, f"{slug!r} missing legacy_aliases"
        assert isinstance(row["legacy_aliases"], list)

    # Bot-added agents have no historical Web tool name.
    for slug in ("brief_gene", "design", "network"):
        assert rows_by_slug[slug]["legacy_aliases"] == []

    # Spot-check the two pluralised-typo aliases Web sometimes emits.
    assert rows_by_slug["knowledge"]["legacy_aliases"] == [
        "KnowledgeAgent",
        "KnowledgeAgents",
    ]
    assert rows_by_slug["data"]["legacy_aliases"] == [
        "DataAgent",
        "DatabaseAgents",
    ]

    # Every historical Web tool name appears in exactly one row.
    flat_aliases: set[str] = set()
    for row in rows_by_slug.values():
        flat_aliases.update(row["legacy_aliases"])
    expected_web_aliases = {
        "ChatAgent",
        "KnowledgeAgent",
        "KnowledgeAgents",
        "DataAgent",
        "DatabaseAgents",
        "ReviewAgent",
        "ReviewAgents",
        "AnalystAgent",
        "AnalysisAgents",
        "DeepGenomeAgent",
        "InSilicoResearchAgent",
    }
    missing = expected_web_aliases - flat_aliases
    assert not missing, f"missing legacy aliases: {sorted(missing)}"


async def test_list_agents_adds_capabilities_without_changing_legacy_fields(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
) -> None:
    """The route publishes additive facts in the stable ten-row order."""
    response = await api_client.get(
        "/v1/agents",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    rows = response.json()["data"]
    expected_slugs = (
        "chat",
        "knowledge",
        "data",
        "review",
        "brief_gene",
        "analyst",
        "deep_genome",
        "research",
        "design",
        "network",
    )
    assert tuple(row["slug"] for row in rows) == expected_slugs
    expected_tools = dict(
        zip(
            (
                _canonical_agent_slug(name)
                for name, _description, _model in AGENT_TOOL_DEFINITIONS
            ),
            (
                name.value
                for name, _description, _model in AGENT_TOOL_DEFINITIONS
            ),
            strict=True,
        )
    )
    for row in rows:
        slug = row["slug"]
        assert row["tool"] == expected_tools[slug]
        assert set(row) == {
            "slug",
            "tool",
            "origin",
            "legacy_aliases",
            "capabilities",
        }
        assert isinstance(row["capabilities"], dict)
        assert isinstance(row["capabilities"]["report_states"], list)


async def test_agent_run_unknown_slug_returns_404(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
) -> None:
    """An unknown slug is rejected with a 404 envelope."""
    response = await api_client.post(
        "/v1/agents/mystery/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={"arguments": {}},
    )
    assert response.status_code == 404


async def test_agent_run_sync_writes_local_run(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A sync-agent invocation returns the agent.run envelope at 200."""

    async def fake(args: Any) -> dict[str, Any]:
        """Return a stub chat completion-shaped result."""
        _ = args
        return {"answer": "ok", "doc_list": []}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake,
    )

    response = await api_client.post(
        "/v1/agents/chat/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={"arguments": {"user_query": "hi", "obs_file_list": []}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "agent.run"
    assert body["agent"] == "chat"
    assert body["status"] == "succeeded"
    assert body["task_ids"] == []
    assert body["id"]
    assert body["result"] is not None

    listing = RunRegistry(tasks_db_path).list_runs(owner="u1")
    assert len(listing) == 1
    record = listing[0]
    assert record.spec.run_id == body["id"]
    assert record.spec.agent == "chat"
    assert record.spec.origin == "local"
    assert record.status == "succeeded"


async def test_agent_run_sync_persists_request_info(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A sync agent run captures dialogue / query / tool_name on the row."""

    async def fake(args: Any) -> dict[str, Any]:
        """Return a stub chat completion-shaped result."""
        _ = args
        return {"answer": "ok", "doc_list": []}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake,
    )

    response = await api_client.post(
        "/v1/agents/chat/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "arguments": {
                "user_query": "summarise C3 photosynthesis",
                "obs_file_list": [],
            },
            "dialogue_id": "dlg-agent-7",
        },
    )
    assert response.status_code == 200

    listing = RunRegistry(tasks_db_path).list_runs(owner="u1")
    assert len(listing) == 1
    info = listing[0].request_info
    assert info.dialogue_id == "dlg-agent-7"
    assert info.query == "summarise C3 photosynthesis"
    assert info.tool_name == "ChatAgent"
    # Native agent runs never carry an OpenAI-compat model id.
    assert info.model is None
    assert info.request_json is not None


_REMOTE_CASES = [
    pytest.param(
        _RemoteCase(
            slug="analyst",
            tool_name=server.PhytomniAgents.ANALYST_AGENT.value,
            stub_return={"task_id": "T-A", "output_dir": "/obs/a"},
            arguments={
                "goal_description": "test",
                "data_list": {},
                "obs_file_list": [],
            },
            expected_task_ids={"T-A"},
        ),
        id="analyst-top-level-task_id",
    ),
    pytest.param(
        _RemoteCase(
            slug="deep_genome",
            tool_name=server.PhytomniAgents.DEEP_GENOME_AGENT.value,
            stub_return={"task_id": "T-D", "output_dir": "/obs/d"},
            arguments={"species_code": "ATH", "gene_id": "AT1G01010"},
            expected_task_ids={"T-D"},
        ),
        id="deep_genome-top-level-task_id",
    ),
    pytest.param(
        _RemoteCase(
            slug="research",
            tool_name=server.PhytomniAgents.IN_SILICO_RESEARCH_AGENT.value,
            stub_return={
                "task_ids": {"g1": "T-R1", "g2": "T-R2"},
                "output_dir": "/obs/r",
            },
            arguments={
                "user_query": "test",
                "data_list": {},
                "obs_file_list": [],
            },
            expected_task_ids={"T-R1", "T-R2"},
        ),
        id="research-task_ids-map",
    ),
]


@pytest.mark.parametrize("case", _REMOTE_CASES)
async def test_agent_run_remote_returns_chokepoint_run_id(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
    case: _RemoteCase,
) -> None:
    """Remote agents return 202 + run_id + task_ids regardless of shape.

    Covers all three wrapper return shapes the chokepoint handles:
    analyst / deep_genome (top-level ``task_id``) and research
    (``task_ids`` dict map). The HTTP layer reads ``current_run_id``
    via contextvar, so the response no longer depends on whether the
    formatter exposes ``metadata.task_id`` (analyst) vs
    ``metadata.server_id`` (deep_genome) vs nothing (research).
    """

    async def fake(args: Any) -> dict[str, Any]:
        """Return the parametrised stub wrapper payload."""
        _ = args
        return case.stub_return

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        case.tool_name,
        records_submission(case.slug)(fake),
    )

    response = await api_client.post(
        f"/v1/agents/{case.slug}/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={"arguments": case.arguments},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["object"] == "agent.run"
    assert body["agent"] == case.slug
    assert body["status"] == "running"
    assert body["id"]
    assert set(body["task_ids"]) == case.expected_task_ids

    listing = RunRegistry(tasks_db_path).list_runs(owner="u1")
    assert len(listing) == 1
    record = listing[0]
    assert record.spec.run_id == body["id"]
    assert record.spec.agent == case.slug
    assert record.spec.origin == "remote"
    assert set(record.task_ids) == case.expected_task_ids
    # No degraded_tracking flag on a healthy submission — pin the
    # absence so a future regression that always-sets the flag does
    # not silently degrade every 202 response.
    assert "degraded_tracking" not in body


async def test_agent_run_remote_surfaces_degraded_tracking_when_recorder_fails(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A recorder persistence failure surfaces ``degraded_tracking`` on 202.

    Pins the silent-failure mitigation: when ``RunRegistry.create_run``
    raises during the submit chokepoint, the remote tasks have already
    been accepted by the upstream platform (the wrapper return is the
    proof) but the local ``runs`` / ``tasks`` rows were not written.
    Without the flag a client cannot distinguish this case from a
    legitimate analyst dedup-hit (both produce ``id=None`` /
    ``task_ids=[]``). The body must carry ``degraded_tracking: True``
    alongside the empty identity fields so operators see a routable
    signal and ``GET /v1/runs/{id}`` 404s are explained.
    """

    def _raising_create_run(*_args: Any, **_kwargs: Any) -> None:
        """Simulate the persistence failure the contract handles."""
        raise sqlite3.OperationalError("disk I/O error")

    def _exploding_registry_factory(_db_path: str) -> SimpleNamespace:
        """Stand in for ``RunRegistry(db_path)`` so create_run raises."""
        return SimpleNamespace(create_run=_raising_create_run)

    monkeypatch.setattr(
        submit_recorder_module, "RunRegistry", _exploding_registry_factory
    )

    async def fake(args: Any) -> dict[str, Any]:
        """Return a canonical analyst submission payload."""
        _ = args
        return {"task_id": "T-degraded", "output_dir": "/obs/run"}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.ANALYST_AGENT.value,
        records_submission("analyst")(fake),
    )

    response = await api_client.post(
        "/v1/agents/analyst/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "arguments": {
                "goal_description": "test",
                "data_list": {},
                "obs_file_list": [],
            }
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["id"] is None
    assert body["task_ids"] == []
    assert body["degraded_tracking"] is True
    # And the registry stayed empty since create_run was the failure
    # point — proves the flag was driven by the live failure, not by
    # stale state left over from a previous test.
    assert not RunRegistry(tasks_db_path).list_runs(owner="u1")
