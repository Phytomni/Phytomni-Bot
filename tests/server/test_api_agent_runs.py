# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
# pylint: disable=too-many-lines
"""Tests for the native ``/v1/agents`` and ``/v1/agents/{slug}/runs``.

Covers listing, auth enforcement, slug-not-found, sync-agent run with
``origin="local"`` written here, and remote-agent run with the
chokepoint-minted ``origin="remote"`` run_id read back via
``tasks.run_id``.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from tests.support.handler_fakes import review_success_result
from tests.support.http_fakes import (
    assert_duplicate_attachment_response,
    install_rejection_handler,
    install_tool_handler,
    minimal_tool_handler,
    open_asgi_client,
)
from tests.support.resolver_fakes import (
    post_duplicate_attachment_run,
    post_native_run,
    post_recorded_analyst_run,
)
from tests.support.resumable_asset_fakes import (
    AssetHttpTestContext,
    AssetRejectionCase,
    BackgroundAssetCase,
    ResumableAssetSpec,
    build_resumable_asset,
    execute_opaque_asset_run,
    execute_rejected_asset_run,
    install_attachment_capture,
    wait_for_attachment_submission,
)

from mcp_server_phytomni import server
from mcp_server_phytomni.agents.shared.dataset_description import (
    DatasetDescriptionResult,
)
from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.api.a2ui_runtime import ReviewExecution
from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.api.lifecycle_contract import empty_agent_result
from mcp_server_phytomni.api.routes import (
    attachment_inputs as attachment_inputs_module,
)
from mcp_server_phytomni.api.upload_runtime import UploadRuntime
from mcp_server_phytomni.mcp.result_formatting import FormattedToolResult
from mcp_server_phytomni.mcp.schemas import (
    AGENT_TOOL_DEFINITIONS,
    PhytomniAgents,
)
from mcp_server_phytomni.runtime import (
    submit_recorder as submit_recorder_module,
)
from mcp_server_phytomni.runtime.background_submission import (
    BackgroundSubmissionLaunchError,
)
from mcp_server_phytomni.runtime.run_registry import (
    RunRegistry,
)
from mcp_server_phytomni.runtime.submit_recorder import records_submission

pytestmark = pytest.mark.server


def test_native_run_projects_submission_warnings_into_execution() -> None:
    """Default HTTP results expose safe partial-submission warnings."""
    envelope = SimpleNamespace(
        formatted=FormattedToolResult(answer="accepted", metadata={}),
        raw={
            "submission_warnings": [
                {
                    "code": "partial_submission",
                    "retryable": False,
                    "rejected_count": 1,
                    "private_error": "drop me",
                }
            ]
        },
    )

    _result, response_result = getattr(
        api_app_module, "_format_agent_run_result"
    )(
        envelope,
        resolve_meta={},
        debug=False,
    )

    assert response_result["execution"] == {
        "warnings": [
            {
                "code": "partial_submission",
                "retryable": False,
                "rejected_count": 1,
            }
        ]
    }
    assert "raw" not in response_result


@dataclass(frozen=True)
class _RemoteCase(BackgroundAssetCase):
    """One parametrize row for the remote-agent chokepoint contract."""


@pytest.fixture(name="asset_http_context")
def _asset_http_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tasks_db_path: str,
    issued_api_key: str,
) -> AssetHttpTestContext:
    """Bundle opaque-asset HTTP fixtures without hiding their ownership."""
    return AssetHttpTestContext(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        db_path=tasks_db_path,
        api_key=issued_api_key,
    )


def _canonical_agent_slug(tool: PhytomniAgents) -> str:
    """Derive the native API slug from the canonical MCP enum member."""
    candidate = tool.name.removesuffix("_AGENT").lower()
    return {
        "in_silico_research": "research",
        "digital_design": "design",
        "gene_network": "network",
    }.get(candidate, candidate)


def _auth(key: str) -> dict[str, str]:
    """Build one Bearer auth header."""
    return {"Authorization": f"Bearer {key}"}


def _create_scoped_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Any, dict[str, str]]:
    """Create an isolated app plus the three delegated-owner key shapes."""
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "tasks.sqlite"))
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(tmp_path / "keys.sqlite"))
    store = ApiKeyStore(str(tmp_path / "keys.sqlite"))
    keys = {
        "scope_less": store.create(user_id="principal-owner").api_key,
        "agents": store.create(
            user_id="principal-owner", scopes=["agents"]
        ).api_key,
        "delegate": store.create(
            user_id="web-service", scopes=["agents", "files:delegate"]
        ).api_key,
    }
    return api_app_module.create_app(), keys


def _dataset_arguments(slug: str, query: str) -> dict[str, Any]:
    """Return schema-shaped Analyst or Research dataset arguments."""
    if slug == "analyst":
        return {
            "goal_description": query,
            "data_list": {},
            "obs_file_list": [],
        }
    return {"user_query": query, "data_list": {}, "obs_file_list": []}


def _install_dataset_assets(
    context: AssetHttpTestContext,
    *,
    owner: str = "u1",
    dataset_filename: str = "input.csv",
) -> tuple[Any, str, str]:
    """Create one completed dataset and document and return references."""
    dataset = build_resumable_asset(
        context.tmp_path,
        db_path=context.db_path,
        spec=ResumableAssetSpec(
            owner=owner,
            filename=dataset_filename,
            content=b"gene,value\nAT1G01010,7\n",
            purpose="dataset",
        ),
    )
    document = build_resumable_asset(
        context.tmp_path,
        db_path=context.db_path,
        spec=ResumableAssetSpec(
            owner=owner,
            filename="context.pdf",
            content=b"%PDF-1.4\ncontext\n",
            purpose="chat_attachment",
        ),
    )
    context.monkeypatch.setattr(
        UploadRuntime,
        "get_asset_resolver",
        lambda _runtime: dataset.resolver,
    )
    return dataset.resolver, dataset.asset_id, document.asset_id


async def _post_asset_run(
    context: AssetHttpTestContext,
    **options: Any,
) -> httpx.Response:
    """POST one native run with opaque attachments."""
    payload: dict[str, Any] = {
        "arguments": options["arguments"],
        "attachments": options["attachments"],
    }
    for key in ("owner_subject", "dataset_description", "debug"):
        if options.get(key) is not None:
            payload[key] = options[key]
    async with open_asgi_client(
        context.monkeypatch,
        api_app_module.create_app(),
        base_url="http://api.asset.test",
    ) as client:
        return await client.post(
            f"/v1/agents/{options['slug']}/runs",
            headers=_auth(options.get("api_key") or context.api_key),
            json=payload,
        )


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


async def test_list_agents_omits_disabled_context_protocol(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
) -> None:
    """The default-off context protocol is absent from the public catalog."""
    response = await api_client.get(
        "/v1/agents",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    assert "protocols" not in response.json()


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
    detail = response.json()["error"]
    assert detail["code"] == "not_found"
    assert isinstance(detail["code"], str)
    assert "type" not in detail
    assert detail["message"] == "resource not found"
    assert detail["request_id"]
    assert detail["retryable"] is False


async def test_agent_run_sync_writes_local_run(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A sync-agent invocation returns the agent.run envelope at 200."""

    fake = minimal_tool_handler("ok")

    install_tool_handler(
        monkeypatch, server.PhytomniAgents.CHAT_AGENT.value, fake
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


async def test_native_run_rejects_duplicate_attachments_before_handler(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """Duplicate attachment references fail before tool dispatch."""
    del tasks_db_path
    marker = install_rejection_handler(
        monkeypatch, server.PhytomniAgents.CHAT_AGENT.value
    )
    path = "/obs/phytomni/agent_data/uploads/u1/fixture/duplicate.pdf"
    response = await post_duplicate_attachment_run(
        api_client, issued_api_key, path
    )

    assert_duplicate_attachment_response(response)
    assert not marker["called"]


async def test_agent_run_sync_persistence_failure_returns_safe_500(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sync run cannot surface succeeded when persistence failed."""

    fake = minimal_tool_handler("ok")

    def fail_record_sync(**_kwargs: Any) -> str:
        """Raise the app-level persistence failure without private leakage."""
        raise api_app_module.run_lifecycle.RunPersistenceError(
            "private persistence detail"
        )

    install_tool_handler(
        monkeypatch, server.PhytomniAgents.CHAT_AGENT.value, fake
    )
    monkeypatch.setattr(
        api_app_module,
        "_record_sync_run",
        fail_record_sync,
    )

    response = await post_native_run(
        api_client,
        issued_api_key,
        "chat",
        {"user_query": "hi", "obs_file_list": []},
    )

    assert response.status_code == 500
    detail = response.json()["error"]
    assert detail["code"] == "run_persistence_failed"
    assert detail["message"] == "The completed run could not be persisted."
    assert detail["stage"] == "run_persist"
    assert detail["retryable"] is False
    assert isinstance(detail["request_id"], str)
    assert detail["request_id"]
    assert "private persistence detail" not in response.text


@pytest.mark.parametrize(
    "case",
    [
        (
            "chat",
            server.PhytomniAgents.CHAT_AGENT.value,
            {"user_query": "hi", "obs_file_list": []},
        ),
        (
            "knowledge",
            server.PhytomniAgents.KNOWLEDGE_AGENT.value,
            {"user_query": "hi", "obs_file_list": []},
        ),
        (
            "data",
            server.PhytomniAgents.DATA_AGENT.value,
            {"user_query": "count rice genes"},
        ),
        (
            "review",
            server.PhytomniAgents.REVIEW_AGENT.value,
            {"user_query": "review this", "obs_file_list": []},
        ),
        (
            "brief_gene",
            server.PhytomniAgents.BRIEF_GENE_AGENT.value,
            {"user_query": "AT1G01010"},
        ),
    ],
)
async def test_native_sync_agents_keep_succeeded_envelope(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str, dict[str, Any]],
) -> None:
    """Established synchronous native runs never launch a worker."""
    slug, tool_name, arguments = case

    background_launcher = Mock(name="background_launcher")
    monkeypatch.setattr(
        api_app_module, "launch_background_submission", background_launcher
    )
    if slug == "review":

        async def fake_review(**_kwargs: Any) -> Any:
            return ReviewExecution(
                run_id="native-review-sync",
                status="succeeded",
                result=review_success_result(),
            )

        monkeypatch.setattr(
            api_app_module, "_run_review_with_interrupt", fake_review
        )
    else:
        install_tool_handler(
            monkeypatch, tool_name, minimal_tool_handler("ok")
        )
    response = await post_native_run(
        api_client, issued_api_key, slug, arguments
    )
    body = response.json()
    assert response.status_code == 200
    assert body["object"] == "agent.run"
    assert body["agent"] == slug
    assert body["status"] == "succeeded"
    assert body["task_ids"] == []
    assert body["id"] == body["run_id"]
    assert background_launcher.call_count == 0


async def test_agent_run_sync_persists_request_info(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A sync agent run captures dialogue / query / tool_name on the row."""

    fake = minimal_tool_handler("ok")
    install_tool_handler(
        monkeypatch, server.PhytomniAgents.CHAT_AGENT.value, fake
    )
    response = await post_native_run(
        api_client,
        issued_api_key,
        "chat",
        {
            "user_query": "summarise C3 photosynthesis",
            "obs_file_list": [],
        },
        dialogue_id="dlg-agent-7",
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
            slug="deep_genome",
            tool_name=server.PhytomniAgents.DEEP_GENOME_AGENT.value,
            stub_return={"task_id": "T-D", "output_dir": "/obs/d"},
            arguments={"species_code": "ATH", "gene_id": "AT1G01010"},
            expected_task_ids={"T-D"},
        ),
        id="deep_genome-top-level-task_id",
    ),
]


_BACKGROUND_CASES = [
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
        id="analyst",
    ),
    pytest.param(
        _RemoteCase(
            slug="research",
            tool_name=server.PhytomniAgents.IN_SILICO_RESEARCH_AGENT.value,
            stub_return={
                "task_ids": ["T-R1", "T-R2"],
                "output_dir": "/obs/r",
            },
            arguments={
                "user_query": "test",
                "data_list": {},
                "obs_file_list": [],
            },
            expected_task_ids={"T-R1", "T-R2"},
        ),
        id="research",
    ),
    pytest.param(
        _RemoteCase(
            slug="network",
            tool_name=server.PhytomniAgents.GENE_NETWORK_AGENT.value,
            stub_return={
                "network_task": {
                    "task_id": "T-N",
                    "output_dir": "/obs/n",
                }
            },
            arguments={
                "species_code": "osa",
                "to_id": "TO:0000207",
                "obs_file_list": [],
                "resolve_to_id": False,
            },
            expected_task_ids={"T-N"},
        ),
        id="network",
    ),
    pytest.param(
        _RemoteCase(
            slug="design",
            tool_name=server.PhytomniAgents.DIGITAL_DESIGN_AGENT.value,
            stub_return={
                "design_task_result": [
                    {"task_id": "T-D1", "output_dir": "/obs/d1"},
                    {"task_id": "T-D2", "output_dir": "/obs/d2"},
                ]
            },
            arguments={
                "species_code": "ath",
                "gene_id": "AT1G01010",
                "obs_file_list": [],
                "resolve_gene_id": False,
            },
            expected_task_ids={"T-D1", "T-D2"},
        ),
        id="design",
    ),
]


@pytest.mark.parametrize("case", _BACKGROUND_CASES)
async def test_native_background_run_preserves_empty_obs_file_list(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
    case: _RemoteCase,
) -> None:
    """An explicit empty attachment list reaches all four typed Agents."""
    captured: dict[str, Any] = {}
    install_attachment_capture(monkeypatch, case, captured)

    response = await api_client.post(
        f"/v1/agents/{case.slug}/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={"arguments": case.arguments, "attachments": []},
    )

    assert response.status_code == 202
    arguments = await wait_for_attachment_submission(
        captured=captured,
        db_path=tasks_db_path,
        run_id=response.json()["run_id"],
        case=case,
    )
    assert arguments.obs_file_list == []


@pytest.mark.parametrize("case", _BACKGROUND_CASES)
async def test_native_background_run_resolves_opaque_owner_asset(
    asset_http_context: AssetHttpTestContext,
    case: _RemoteCase,
) -> None:
    """An owner-completed opaque asset becomes one internal reference."""
    harness, response, arguments = await execute_opaque_asset_run(
        asset_http_context,
        case,
    )
    assert response.status_code == 202
    assert len(arguments.obs_file_list) == 1
    internal_reference = arguments.obs_file_list[0]
    assert not hasattr(arguments, "attachments")
    assert "attachments" not in arguments.model_dump()
    assert harness.asset_id not in response.text
    assert internal_reference not in response.text


async def test_owner_subject_is_authorized_as_attachment_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Explicit owner assertions require files:delegate even for self."""
    app, keys = _create_scoped_app(monkeypatch, tmp_path)

    async def fail_invoke(**_kwargs: Any) -> tuple[dict[str, Any], int]:
        raise AssertionError("validation should fail before invocation")

    monkeypatch.setattr(api_app_module, "_invoke_agent_run", fail_invoke)
    request = {
        "arguments": {
            "goal_description": "bounded analysis",
            "data_list": {},
            "obs_file_list": [],
        },
        "owner_subject": "principal-owner",
    }
    async with open_asgi_client(
        monkeypatch, app, base_url="http://api.owner.test"
    ) as client:
        no_scope = await client.post(
            "/v1/agents/analyst/runs",
            headers=_auth(keys["scope_less"]),
            json=request,
        )
        agents_only = await client.post(
            "/v1/agents/analyst/runs",
            headers=_auth(keys["agents"]),
            json=request,
        )
        blank = await client.post(
            "/v1/agents/analyst/runs",
            headers=_auth(keys["delegate"]),
            json={**request, "owner_subject": "   "},
        )

    for response in (no_scope, agents_only):
        assert response.status_code == 403
        assert (
            response.json()["error"]["message"] == "request is not permitted"
        )
        assert "principal-owner" not in response.text
        assert "files:delegate" not in response.text
    assert blank.status_code == 422
    assert "validation should fail" not in blank.text


async def test_omitted_owner_subject_preserves_scope_less_principal_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Absent owner_subject keeps the authenticated principal behavior."""
    app, keys = _create_scoped_app(monkeypatch, tmp_path)
    calls: list[dict[str, Any]] = []

    async def fake_invoke(**kwargs: Any) -> tuple[dict[str, Any], int]:
        calls.append(kwargs)
        return (
            {
                "id": "principal-run",
                "run_id": "principal-run",
                "object": "agent.run",
                "agent": "analyst",
                "status": "running",
                "task_ids": [],
                "result": empty_agent_result(),
            },
            202,
        )

    monkeypatch.setattr(api_app_module, "_invoke_agent_run", fake_invoke)
    async with open_asgi_client(
        monkeypatch, app, base_url="http://api.owner.test"
    ) as client:
        response = await client.post(
            "/v1/agents/analyst/runs",
            headers=_auth(keys["scope_less"]),
            json={
                "arguments": {
                    "goal_description": "bounded analysis",
                    "data_list": {},
                    "obs_file_list": [],
                }
            },
        )

    assert response.status_code == 202
    assert calls[0]["attachment_evidence"] is None
    assert not RunRegistry(str(tmp_path / "tasks.sqlite")).list_runs(
        owner="principal-owner"
    )


@pytest.mark.parametrize(
    ("slug", "query_key"),
    [("analyst", "goal_description"), ("research", "user_query")],
)
async def test_direct_dataset_assets_project_to_data_list_before_202(
    asset_http_context: AssetHttpTestContext,
    slug: str,
    query_key: str,
) -> None:
    """Managed datasets are described and submitted before 202 acceptance."""
    _resolver, dataset_id, document_id = _install_dataset_assets(
        asset_http_context
    )
    captured: dict[str, Any] = {}
    case = _RemoteCase(
        slug=slug,
        tool_name=(
            server.PhytomniAgents.ANALYST_AGENT.value
            if slug == "analyst"
            else server.PhytomniAgents.IN_SILICO_RESEARCH_AGENT.value
        ),
        stub_return=(
            {"task_id": f"T-{slug}", "output_dir": "/obs/out"}
            if slug == "analyst"
            else {"task_ids": [f"T-{slug}"], "output_dir": "/obs/out"}
        ),
        arguments=_dataset_arguments(slug, f"{slug} query"),
        expected_task_ids={f"T-{slug}"},
    )
    install_attachment_capture(asset_http_context.monkeypatch, case, captured)

    async def fail_completion(**_kwargs: Any) -> DatasetDescriptionResult:
        raise AssertionError("supplied description should skip provider")

    asset_http_context.monkeypatch.setattr(
        attachment_inputs_module,
        "complete_dataset_descriptions",
        fail_completion,
        raising=False,
    )
    response = await _post_asset_run(
        asset_http_context,
        slug=slug,
        arguments=case.arguments,
        attachments=[{"asset_id": dataset_id}, {"asset_id": document_id}],
        dataset_description="supplied batch description",
    )

    assert response.status_code == 202, response.text
    arguments = await wait_for_attachment_submission(
        captured=captured,
        db_path=asset_http_context.db_path,
        run_id=response.json()["run_id"],
        case=case,
    )
    dumped = arguments.model_dump()
    dataset_reference = next(iter(dumped["data_list"]))
    assert dumped[query_key] == f"{slug} query"
    assert dumped["data_list"] == {
        dataset_reference: "supplied batch description"
    }
    assert len(dumped["obs_file_list"]) == 1
    assert "attachments" not in dumped
    assert "owner_subject" not in dumped
    assert "dataset_description" not in dumped


async def test_direct_dataset_blank_description_uses_one_completion_result(
    asset_http_context: AssetHttpTestContext,
) -> None:
    """A blank batch description is completed once for managed datasets."""
    _resolver, dataset_id, _document_id = _install_dataset_assets(
        asset_http_context
    )
    captured: dict[str, Any] = {}
    case = _RemoteCase(
        slug="analyst",
        tool_name=server.PhytomniAgents.ANALYST_AGENT.value,
        stub_return={"task_id": "T-generated", "output_dir": "/obs/out"},
        arguments=_dataset_arguments("analyst", "complete this dataset"),
        expected_task_ids={"T-generated"},
    )
    install_attachment_capture(asset_http_context.monkeypatch, case, captured)
    calls: list[dict[str, Any]] = []

    async def fake_completion(**kwargs: Any) -> DatasetDescriptionResult:
        calls.append(kwargs)
        return DatasetDescriptionResult(("generated role",), "generated")

    asset_http_context.monkeypatch.setattr(
        attachment_inputs_module,
        "complete_dataset_descriptions",
        fake_completion,
        raising=False,
    )
    response = await _post_asset_run(
        asset_http_context,
        slug="analyst",
        arguments=case.arguments,
        attachments=[{"asset_id": dataset_id}],
        dataset_description=" ",
    )

    assert response.status_code == 202
    arguments = await wait_for_attachment_submission(
        captured=captured,
        db_path=asset_http_context.db_path,
        run_id=response.json()["run_id"],
        case=case,
    )
    assert calls[0]["query"] == "complete this dataset"
    assert list(arguments.data_list.values()) == ["generated role"]


async def test_dataset_assets_fail_before_completion_and_reservation(
    asset_http_context: AssetHttpTestContext,
) -> None:
    """Unsupported dataset assets do not complete, persist, or launch."""
    _resolver, dataset_id, _document_id = _install_dataset_assets(
        asset_http_context
    )
    marker = install_rejection_handler(
        asset_http_context.monkeypatch,
        server.PhytomniAgents.CHAT_AGENT.value,
    )
    background_launcher = Mock(name="background_launcher")
    asset_http_context.monkeypatch.setattr(
        api_app_module, "launch_background_submission", background_launcher
    )

    async def fail_completion(**_kwargs: Any) -> DatasetDescriptionResult:
        raise AssertionError("unsupported agent should not complete")

    asset_http_context.monkeypatch.setattr(
        attachment_inputs_module,
        "complete_dataset_descriptions",
        fail_completion,
        raising=False,
    )
    response = await _post_asset_run(
        asset_http_context,
        slug="chat",
        arguments={"user_query": "hi", "obs_file_list": []},
        attachments=[{"asset_id": dataset_id}],
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "attachment_not_supported"
    assert marker["called"] is False
    assert background_launcher.call_count == 0
    assert not RunRegistry(asset_http_context.db_path).list_runs(owner="u1")


async def test_dataset_completion_blocks_before_umbrella_reservation(
    asset_http_context: AssetHttpTestContext,
) -> None:
    """The POST cannot return 202 while dataset completion is pending."""
    _resolver, dataset_id, _document_id = _install_dataset_assets(
        asset_http_context
    )
    release = asyncio.Event()
    started = asyncio.Event()

    async def slow_completion(**_kwargs: Any) -> DatasetDescriptionResult:
        started.set()
        await release.wait()
        return DatasetDescriptionResult(("ready",), "generated")

    asset_http_context.monkeypatch.setattr(
        attachment_inputs_module,
        "complete_dataset_descriptions",
        slow_completion,
        raising=False,
    )
    captured: dict[str, Any] = {}
    case = _RemoteCase(
        slug="analyst",
        tool_name=server.PhytomniAgents.ANALYST_AGENT.value,
        stub_return={"task_id": "T-blocked", "output_dir": "/obs/out"},
        arguments=_dataset_arguments("analyst", "wait for data"),
        expected_task_ids={"T-blocked"},
    )
    install_attachment_capture(asset_http_context.monkeypatch, case, captured)
    request_task = asyncio.create_task(
        _post_asset_run(
            asset_http_context,
            slug="analyst",
            arguments=case.arguments,
            attachments=[{"asset_id": dataset_id}],
        )
    )

    await asyncio.wait_for(started.wait(), timeout=1)
    assert not RunRegistry(asset_http_context.db_path).list_runs(owner="u1")
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(request_task), timeout=0.01)
    release.set()
    response = await request_task

    assert response.status_code == 202


async def test_managed_tsv_dataset_asset_fails_before_completion(
    asset_http_context: AssetHttpTestContext,
) -> None:
    """Managed dataset format validation runs before description completion."""
    _resolver, dataset_id, _document_id = _install_dataset_assets(
        asset_http_context, dataset_filename="input.tsv"
    )

    async def fail_completion(**_kwargs: Any) -> DatasetDescriptionResult:
        raise AssertionError("unsupported format should not complete")

    asset_http_context.monkeypatch.setattr(
        attachment_inputs_module,
        "complete_dataset_descriptions",
        fail_completion,
        raising=False,
    )
    response = await _post_asset_run(
        asset_http_context,
        slug="analyst",
        arguments=_dataset_arguments("analyst", "tsv dataset"),
        attachments=[{"asset_id": dataset_id}],
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_asset_format"
    assert not RunRegistry(asset_http_context.db_path).list_runs(owner="u1")


async def test_empty_dataset_completion_still_submits_managed_blank(
    asset_http_context: AssetHttpTestContext,
) -> None:
    """Managed evidence permits an empty generated description to submit."""
    _resolver, dataset_id, _document_id = _install_dataset_assets(
        asset_http_context
    )
    captured: dict[str, Any] = {}
    case = _RemoteCase(
        slug="research",
        tool_name=server.PhytomniAgents.IN_SILICO_RESEARCH_AGENT.value,
        stub_return={"task_ids": ["T-empty"], "output_dir": "/obs/out"},
        arguments=_dataset_arguments("research", "empty completion"),
        expected_task_ids={"T-empty"},
    )
    install_attachment_capture(asset_http_context.monkeypatch, case, captured)

    async def empty_completion(**_kwargs: Any) -> DatasetDescriptionResult:
        return DatasetDescriptionResult(("",), "empty")

    asset_http_context.monkeypatch.setattr(
        attachment_inputs_module,
        "complete_dataset_descriptions",
        empty_completion,
        raising=False,
    )
    response = await _post_asset_run(
        asset_http_context,
        slug="research",
        arguments=case.arguments,
        attachments=[{"asset_id": dataset_id}],
    )

    assert response.status_code == 202
    arguments = await wait_for_attachment_submission(
        captured=captured,
        db_path=asset_http_context.db_path,
        run_id=response.json()["run_id"],
        case=case,
    )
    assert list(arguments.data_list.values()) == [""]


async def test_delegated_asset_lookup_keeps_run_owner_as_principal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Delegation changes lookup owner but not accepted run ownership."""
    app, keys = _create_scoped_app(monkeypatch, tmp_path)
    context = AssetHttpTestContext(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        db_path=str(tmp_path / "tasks.sqlite"),
        api_key=keys["delegate"],
    )
    _resolver, dataset_id, _document_id = _install_dataset_assets(
        context, owner="principal-owner"
    )
    captured: dict[str, Any] = {}
    case = _RemoteCase(
        slug="analyst",
        tool_name=server.PhytomniAgents.ANALYST_AGENT.value,
        stub_return={"task_id": "T-delegated", "output_dir": "/obs/out"},
        arguments=_dataset_arguments("analyst", "delegated analysis"),
        expected_task_ids={"T-delegated"},
    )
    install_attachment_capture(monkeypatch, case, captured)
    async with open_asgi_client(
        monkeypatch, app, base_url="http://api.delegate.test"
    ) as client:
        response = await client.post(
            "/v1/agents/analyst/runs",
            headers=_auth(keys["delegate"]),
            json={
                "arguments": case.arguments,
                "attachments": [{"asset_id": dataset_id}],
                "owner_subject": "principal-owner",
                "dataset_description": "delegated dataset",
            },
        )

    assert response.status_code == 202, response.text
    registry = RunRegistry(context.db_path)
    arguments = None
    for _ in range(100):
        arguments = captured.get("arguments")
        record = registry.get_run(
            response.json()["run_id"], owner="web-service"
        )
        if (
            arguments is not None
            and record is not None
            and set(record.task_ids) == case.expected_task_ids
        ):
            break
        await asyncio.sleep(0)
    else:
        pytest.fail("delegated background submission did not settle")
    assert list(arguments.data_list.values()) == ["delegated dataset"]
    assert registry.get_run(response.json()["run_id"], owner="web-service")
    assert (
        registry.get_run(response.json()["run_id"], owner="principal-owner")
        is None
    )


async def test_sync_document_asset_request_and_result_are_redacted(
    asset_http_context: AssetHttpTestContext,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Managed references stay out of request JSON, responses, and logs."""
    prompt = "canonical redaction prompt"
    provider_output = "provider-private-output"
    attachment_marker = "document-private-reference"
    harness = build_resumable_asset(
        asset_http_context.tmp_path,
        db_path=asset_http_context.db_path,
        spec=ResumableAssetSpec(
            owner="u1",
            filename="redaction.pdf",
            content=b"%PDF-1.4 redaction",
            purpose="chat_attachment",
        ),
    )
    asset_http_context.monkeypatch.setattr(
        UploadRuntime,
        "get_asset_resolver",
        lambda _runtime: harness.resolver,
    )

    async def fake(args: Any) -> dict[str, Any]:
        reference = args.obs_file_list[0]
        return {
            "choices": [{"message": {"content": f"safe answer {reference}"}}],
            "provider_output": provider_output,
            "attachment_marker": attachment_marker,
            "private_reference": reference,
        }

    install_tool_handler(
        asset_http_context.monkeypatch,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake,
    )
    response = await _post_asset_run(
        asset_http_context,
        slug="chat",
        arguments={"user_query": prompt, "obs_file_list": []},
        attachments=[{"asset_id": harness.asset_id}],
    )
    debug_response = await _post_asset_run(
        asset_http_context,
        slug="chat",
        arguments={"user_query": prompt, "obs_file_list": []},
        attachments=[{"asset_id": harness.asset_id}],
        debug=True,
    )

    assert response.status_code == 200
    assert debug_response.status_code == 200
    listing = RunRegistry(asset_http_context.db_path).list_runs(owner="u1")
    assert len(listing) == 2
    info = listing[0].request_info
    assert info.query == prompt
    assert json.loads(info.request_json or "{}") == {
        "dialogue_id": None,
        "locale": "en-US",
        "route": "chat",
    }
    rendered = (
        response.text
        + debug_response.text
        + json.dumps(listing[0].result or {})
        + caplog.text
    )
    for sentinel in (
        harness.asset_id,
        provider_output,
        attachment_marker,
        "redaction.pdf",
    ):
        assert sentinel not in rendered
        assert sentinel not in (info.request_json or "")
    assert prompt not in (info.request_json or "")


_ATTACHMENT_REJECTION_CASES = (
    pytest.param(
        AssetRejectionCase("foreign", 404, "upload_asset_not_found"),
        id="foreign",
    ),
    pytest.param(
        AssetRejectionCase("incomplete", 409, "upload_state_conflict"),
        id="incomplete",
    ),
    pytest.param(
        AssetRejectionCase("duplicate", 409, "upload_state_conflict"),
        id="duplicate",
    ),
    pytest.param(
        AssetRejectionCase("malformed", 422, "invalid_upload_metadata"),
        id="malformed",
    ),
)


@pytest.mark.parametrize("case", _BACKGROUND_CASES)
@pytest.mark.parametrize("rejection", _ATTACHMENT_REJECTION_CASES)
async def test_native_background_run_rejects_unsafe_opaque_asset(
    asset_http_context: AssetHttpTestContext,
    caplog: pytest.LogCaptureFixture,
    case: _RemoteCase,
    rejection: AssetRejectionCase,
) -> None:
    """Owner, completion, uniqueness, and format failures stay private."""
    response, private_values, handler_called = (
        await execute_rejected_asset_run(
            asset_http_context,
            case,
            rejection.scenario,
        )
    )
    assert response.status_code == rejection.status_code
    assert response.json()["error"]["code"] == rejection.safe_code
    assert handler_called is False
    rendered = f"{response.text}\n{caplog.text}"
    for private_value in private_values:
        assert private_value not in rendered


@pytest.mark.parametrize("case", _BACKGROUND_CASES)
async def test_background_agent_returns_reserved_run_before_handler_finishes(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
    case: _RemoteCase,
) -> None:
    """Return the reserved run while the handler remains in flight."""
    release = asyncio.Event()
    started = asyncio.Event()

    async def slow_handler(_args: Any) -> dict[str, Any]:
        started.set()
        await release.wait()
        return case.stub_return

    install_tool_handler(
        monkeypatch,
        case.tool_name,
        records_submission(case.slug)(slow_handler),
    )
    request_task = asyncio.create_task(
        api_client.post(
            f"/v1/agents/{case.slug}/runs",
            headers={"Authorization": f"Bearer {issued_api_key}"},
            json={"arguments": case.arguments},
        )
    )

    await asyncio.wait_for(started.wait(), timeout=1)
    response: httpx.Response | None = None
    try:
        response = await asyncio.wait_for(
            asyncio.shield(request_task), timeout=1
        )
        returned_before_release = True
    except TimeoutError:
        returned_before_release = False
    finally:
        release.set()
    if not returned_before_release:
        response = await request_task

    assert returned_before_release
    assert response is not None
    assert response.status_code == 202
    body = response.json()
    assert body["agent"] == case.slug
    assert body["status"] == "running"
    assert body["id"] == body["run_id"]
    assert body["run_id"]
    assert body["task_ids"] == []
    assert body["result"] == empty_agent_result()
    assert "degraded_tracking" not in body

    registry = RunRegistry(tasks_db_path)
    for _ in range(100):
        record = registry.get_run(body["run_id"], owner="u1")
        if (
            record is not None
            and set(record.task_ids) == case.expected_task_ids
        ):
            break
        await asyncio.sleep(0)
    else:
        pytest.fail("background child tasks were not attached")

    assert record is not None
    assert record.spec.run_id == body["run_id"]
    assert record.spec.agent == case.slug
    assert record.status == "running"


@pytest.mark.parametrize("case", _REMOTE_CASES)
async def test_agent_run_remote_returns_chokepoint_run_id(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
    case: _RemoteCase,
) -> None:
    """Deep Genome keeps its immediate upstream child identity."""
    captured: dict[str, Any] = {}
    install_attachment_capture(monkeypatch, case, captured)

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
    assert body["id"] == body["run_id"]
    assert set(body["task_ids"]) == case.expected_task_ids

    listing = RunRegistry(tasks_db_path).list_runs(owner="u1")
    assert len(listing) == 1
    record = listing[0]
    assert record.spec.run_id == body["id"]
    assert record.spec.agent == case.slug
    assert record.spec.origin == "remote"
    assert set(record.task_ids) == case.expected_task_ids
    if case.slug == "analyst":
        assert (
            record.request_info.request_id == response.headers["x-request-id"]
        )
    # No degraded_tracking flag on a healthy submission — pin the
    # absence so a future regression that always-sets the flag does
    # not silently degrade every 202 response.
    assert "degraded_tracking" not in body


async def test_background_run_settles_failed_when_recorder_fails(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A child persistence failure settles the reserved run failed."""

    def _raising_persistence(*_args: Any, **_kwargs: Any) -> None:
        """Simulate the persistence failure the contract handles."""
        raise sqlite3.OperationalError("disk I/O error")

    def _exploding_registry_factory(_db_path: str) -> SimpleNamespace:
        """Stand in for ``RunRegistry(db_path)`` at either recorder seam."""
        return SimpleNamespace(
            create_run=_raising_persistence,
            record_reserved_submissions=_raising_persistence,
        )

    monkeypatch.setattr(
        submit_recorder_module, "RunRegistry", _exploding_registry_factory
    )

    async def fake(args: Any) -> dict[str, Any]:
        """Return a canonical analyst submission payload."""
        _ = args
        return {"task_id": "T-degraded", "output_dir": "/obs/run"}

    arguments = {
        "goal_description": "test",
        "data_list": {},
        "obs_file_list": [],
    }
    response = await post_recorded_analyst_run(
        monkeypatch=monkeypatch,
        api_client=api_client,
        issued_api_key=issued_api_key,
        fake=fake,
        arguments=arguments,
    )

    assert response.status_code == 202
    body = response.json()
    assert body["run_id"]
    assert body["task_ids"] == []
    assert "degraded_tracking" not in body
    registry = RunRegistry(tasks_db_path)
    for _ in range(100):
        record = registry.get_run(body["run_id"], owner="u1")
        if record is not None and record.status == "failed":
            break
        await asyncio.sleep(0)
    else:
        pytest.fail("background recorder failure did not settle")
    assert record is not None
    assert not record.task_ids
    assert record.error == "background_submission_tracking_failed"


async def test_background_debug_raw_never_persists_or_logs(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A debug request cannot retain raw worker material in the run store."""
    sentinels = (
        "prompt-sentinel-background",
        "model-sentinel-background",
        "attachment-sentinel-background",
    )

    async def fake(_args: Any) -> dict[str, Any]:
        return {
            "task_id": "task-private-raw",
            "output_dir": "/obs/run",
            "prompt": sentinels[0],
            "model_output": sentinels[1],
            "attachment_contents": sentinels[2],
        }

    install_tool_handler(
        monkeypatch,
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
            },
            "debug": True,
        },
    )

    assert response.status_code == 202
    run_id = response.json()["run_id"]
    registry = RunRegistry(tasks_db_path)
    for _ in range(100):
        record = registry.get_run(run_id, owner="u1")
        if record is not None and record.task_ids == ("task-private-raw",):
            break
        await asyncio.sleep(0)
    else:
        pytest.fail("background debug run did not attach its task")

    assert record is not None
    assert record.result is not None
    persisted = json.dumps(record.result)
    assert "raw" not in record.result
    for sentinel in sentinels:
        assert sentinel not in persisted
        assert sentinel not in caplog.text


async def test_background_submission_launch_failure_is_safe(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reservation failures return a stable error without private detail."""

    def fail_reservation(**_kwargs: Any) -> None:
        raise BackgroundSubmissionLaunchError("private path")

    monkeypatch.setattr(
        api_app_module,
        "reserve_background_submission",
        fail_reservation,
        raising=False,
    )

    async def fake(_args: Any) -> dict[str, Any]:
        return {"task_id": "T-never", "output_dir": "/obs/run"}

    response = await post_recorded_analyst_run(
        monkeypatch=monkeypatch,
        api_client=api_client,
        issued_api_key=issued_api_key,
        fake=fake,
        arguments={
            "goal_description": "test",
            "data_list": {},
            "obs_file_list": [],
        },
    )

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "run_persistence_failed",
        "message": "The background run could not be started.",
        "stage": "submission_start",
        "retryable": False,
        "request_id": response.headers["x-request-id"],
    }
    assert "private path" not in response.text
