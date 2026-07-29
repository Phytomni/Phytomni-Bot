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

import asyncio
import sqlite3
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from tests.support.http_fakes import (
    assert_duplicate_attachment_response,
    install_rejection_handler,
    install_tool_handler,
    minimal_tool_handler,
)
from tests.support.resolver_fakes import (
    post_duplicate_attachment_run,
    post_native_run,
    post_recorded_analyst_run,
)
from tests.support.sqlite import closed_sqlite_connection
from tests.support.terminal_results import (
    SensitiveTerminalResultSpec,
    public_partial_warning,
    public_report_projection,
    public_scientific_table_artifact,
    sensitive_terminal_result,
)

from mcp_server_phytomni import server
from mcp_server_phytomni.agents.shared.a2ui import validate_a2ui_surface
from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.api.lifecycle_contract import empty_agent_result
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
    RunOutcome,
    RunRegistry,
    RunSpec,
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
            return api_app_module._ReviewExecution(
                run_id="native-review-sync",
                status="succeeded",
                result={
                    "formatted": {"answer": "review ok", "metadata": {}},
                    "execution": {"warnings": []},
                    "raw": None,
                },
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
async def test_background_agent_returns_reserved_run_before_handler_finishes(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
    case: _RemoteCase,
) -> None:
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

    async def fake(args: Any) -> dict[str, Any]:
        """Return the parametrised stub wrapper payload."""
        _ = args
        return case.stub_return

    install_tool_handler(
        monkeypatch,
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
    assert record.task_ids == ()
    assert record.error == "background_submission_failed"


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


async def test_run_read_replaces_invalid_persisted_review_surface(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Read projection replaces malformed persisted Review A2UI safely."""
    run_id = "run-review-persisted-invalid"
    RunRegistry(tasks_db_path).create_run(
        RunSpec(run_id, "u1", "review", "local"),
        outcome=RunOutcome(
            status="input_required",
            result={
                "private_result": {"provider_payload": "private"},
                "interrupt": {
                    "thread_id": "thread-review-safe",
                    "provider_trace": "private",
                    "draft": {
                        "draft": "review this result",
                        "a2ui": {"widget": "invalid"},
                        "provider_payload": {"secret": "private"},
                        "private_path": "/srv/private",
                    },
                },
            },
        ),
    )

    response = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    listing = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    assert listing.status_code == 200
    for body in (response.json(), listing.json()["data"][0]):
        assert body["id"] == run_id
        assert body["run_id"] == run_id
        surface = body["result"]["interrupt"]["draft"]["a2ui"]
        validate_a2ui_surface(surface)
        assert surface["surface_id"] == f"{run_id}-review-confirm"
        assert body["result"]["interrupt"]["thread_id"] == "thread-review-safe"
        assert set(body["result"]) == {"interrupt", "status"}
        assert set(body["result"]["interrupt"]) == {"thread_id", "draft"}
        assert set(body["result"]["interrupt"]["draft"]) == {
            "summary",
            "a2ui",
        }


async def test_run_reads_publish_canonical_id_aliases(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Fetched and listed persisted rows expose byte-identical identities."""
    run_id = "run-canonical-read-id"
    result = empty_agent_result()
    result["formatted"]["answer"] = "complete"
    RunRegistry(tasks_db_path).create_run(
        RunSpec(run_id, "u1", "chat", "local"),
        outcome=RunOutcome(
            status="succeeded",
            result=result,
        ),
    )

    fetched = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    listed = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert fetched.status_code == 200
    assert fetched.json()["id"] == fetched.json()["run_id"] == run_id
    assert listed.status_code == 200
    row = listed.json()["data"][0]
    assert row["id"] == row["run_id"] == run_id


async def test_invalid_persisted_succeeded_state_maps_to_safe_error(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """A malformed persisted result cannot be returned as valid data."""
    run_id = "run-invalid-persisted-succeeded"
    RunRegistry(tasks_db_path).create_run(
        RunSpec(run_id, "u1", "chat", "local"),
        outcome=RunOutcome(
            status="succeeded",
            result={},
        ),
    )
    with closed_sqlite_connection(tasks_db_path) as connection:
        connection.execute(
            "UPDATE runs SET result_json = ? WHERE run_id = ?",
            ('"invalid"', run_id),
        )

    fetched = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    listed = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    for response in (fetched, listed):
        assert response.status_code == 500
        error = response.json()["error"]
        assert isinstance(error["code"], str)
        assert error["stage"] in {"lifecycle", "projection"}


@pytest.mark.parametrize("status", ("succeeded", "failed"))
async def test_terminal_run_reads_project_nested_sensitive_result(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    status: str,
) -> None:
    """Terminal reads keep only the nested result projection allowlist."""
    run_id = f"run-terminal-without-formatted-{status}"
    RunRegistry(tasks_db_path).create_run(
        RunSpec(run_id, "u1", "chat", "local"),
        outcome=RunOutcome(
            status=status,
            result=sensitive_terminal_result(
                SensitiveTerminalResultSpec(
                    answer="public terminal answer",
                    task_id="task-safe",
                    citation=("pm", "12345"),
                    table=(["gene"], [["AT1G01010"]]),
                    warning=("private_error", "provider exception"),
                )
            ),
            error="provider exception: private details",
        ),
    )

    fetched = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    listed = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert fetched.status_code == 200
    assert listed.status_code == 200
    listed_row = next(
        row for row in listed.json()["data"] if row["id"] == run_id
    )
    for body in (fetched.json(), listed_row):
        assert body["id"] == body["run_id"] == run_id
        assert body["status"] == status
        assert body["result"]["formatted"] == {
            "answer": "public terminal answer",
            "follow_up_questions": ["next?"],
            "references": [
                {
                    "file_id": "doc-1",
                    "title": "Public title",
                    "pm": "12345",
                }
            ],
            "tabular": {
                "headers": ["gene"],
                "rows": [["AT1G01010"]],
            },
            "metadata": {
                "original_query": "public query",
            },
        }
        assert body["result"]["execution"]["tracking"] == {"degraded": False}
        assert body["result"]["execution"]["warnings"] == [
            public_partial_warning()
        ]
        assert body["result"]["execution"]["tasks"] == [
            {
                "id": "task-safe",
                "accepted": True,
                "status": "succeeded",
            }
        ]
        assert body["result"]["execution"]["artifacts"] == [
            public_scientific_table_artifact()
        ]
        assert body["result"]["execution"]["output_dirs"] == [
            "/obs/public/result"
        ]
        assert body["result"]["execution"]["report"] == (
            public_report_projection()
        )
        assert body["result"]["execution"]["diagnostics"] == [
            {
                "code": "upstream_partial",
                "stage": "analysis",
                "retryable": False,
            }
        ]
        assert set(body["result"]) == {"formatted", "execution"}
        assert "provider_trace" not in body["result"]
        assert "raw" not in body["result"]
        assert "provider_payload" not in body["result"]["execution"]
        assert "provider_payload" not in str(body)
        assert "provider_trace" not in str(body)
        assert "private_error" not in str(body)
        if status == "failed":
            assert body["error"] == "run failed"
        else:
            assert "error" not in body


@pytest.mark.parametrize("status", ("succeeded", "failed"))
async def test_terminal_run_reads_default_missing_formatted_result(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    status: str,
) -> None:
    """Terminal reads default absent display data while retaining execution."""
    run_id = f"run-terminal-missing-formatted-{status}"
    RunRegistry(tasks_db_path).create_run(
        RunSpec(run_id, "u1", "chat", "local"),
        outcome=RunOutcome(
            status=status,
            result={
                "execution": {
                    "tracking": {"degraded": False},
                    "tasks": [
                        {
                            "id": "task-retained",
                            "accepted": True,
                            "status": status,
                        }
                    ],
                    "artifacts": [
                        {
                            "role": "scientific_table",
                            "name": "retained.tsv",
                            "mime_type": "text/tab-separated-values",
                            "size_bytes": 8,
                        }
                    ],
                }
            },
            error="provider exception: private details",
        ),
    )

    fetched = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    listed = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert fetched.status_code == 200
    assert listed.status_code == 200
    listed_row = next(
        row for row in listed.json()["data"] if row["id"] == run_id
    )
    for body in (fetched.json(), listed_row):
        assert body["result"]["formatted"] == empty_agent_result()["formatted"]
        assert body["result"]["execution"]["tasks"] == [
            {
                "id": "task-retained",
                "accepted": True,
                "status": status,
            }
        ]
        assert body["result"]["execution"]["artifacts"] == [
            {
                "role": "scientific_table",
                "name": "retained.tsv",
                "mime_type": "text/tab-separated-values",
                "size_bytes": 8,
            }
        ]
        if status == "failed":
            assert body["error"] == "run failed"
        else:
            assert "error" not in body


async def test_persisted_running_projects_empty_result(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """A persisted running row needs no terminal result to remain readable."""
    run_id = "run-persisted-running-no-result"
    RunRegistry(tasks_db_path).create_run(
        RunSpec(run_id, "u1", "chat", "local"),
        outcome=RunOutcome(status="running", result=None),
    )

    fetched = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    listed = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert fetched.status_code == 200
    assert listed.status_code == 200
    for body in (fetched.json(), listed.json()["data"][0]):
        assert body["id"] == body["run_id"] == run_id
        assert body["status"] == "running"
        assert body["result"] == empty_agent_result()
