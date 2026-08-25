# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Expert parity tests for the shared native HTTP run contract."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from mcp_server_phytomni import server
from mcp_server_phytomni.agents.expert import (
    ExpertProviderError,
    ExpertProviderTimeoutError,
    ExpertRoutingContractError,
    ToolSelection,
)
from mcp_server_phytomni.api import app as api_app
from mcp_server_phytomni.api.lifecycle_contract import empty_agent_result
from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.research_input_store import ResearchInputStore
from mcp_server_phytomni.runtime.run_registry import RunRecord, RunRegistry
from mcp_server_phytomni.runtime.submit_recorder import records_submission

pytestmark = pytest.mark.server


@pytest.fixture(autouse=True)
async def _publish_outbound_runtime(outbound_runtime: Any) -> None:
    """Keep direct ASGITransport requests inside runtime ownership."""
    del outbound_runtime


@pytest.fixture(autouse=True)
def _expert_tasks_db(tasks_db_path: str) -> None:
    """Give every Expert contract test an isolated run registry."""
    _ = tasks_db_path


def _auth(api_key: str) -> dict[str, str]:
    """Return the bearer header used by the API fixtures."""
    return {"Authorization": f"Bearer {api_key}"}


def _patch_selection(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    """Make the Expert selector return one deterministic selection."""

    async def fake_select(*_args: Any, **_kwargs: Any) -> ToolSelection:
        return ToolSelection(tool_name, arguments)

    monkeypatch.setattr(api_app, "select_agent_tool", fake_select)


def _patch_analyst_selection(
    monkeypatch: pytest.MonkeyPatch,
    *,
    goal: str,
) -> None:
    """Install the common valid Analyst selection shape."""
    _patch_selection(
        monkeypatch,
        "AnalystAgent",
        {"goal_description": goal, "data_list": {}, "obs_file_list": []},
    )


async def _post_forced_expert(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tool_name: str,
    *,
    body: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Post one forced Expert request with optional contract fields."""
    payload: dict[str, Any] = {
        "user_query": "q",
        "allowed_tools": [tool_name],
        "forced_tool": tool_name,
    }
    if body:
        payload.update(body)
    headers = _auth(issued_api_key)
    if tool_name == "InSilicoResearchAgent":
        headers["Idempotency-Key"] = "test-forced-research"
    return await api_client.post(
        "/v1/query/route",
        headers={**headers, **(extra_headers or {})},
        json=payload,
    )


def _contract_shape(body: dict[str, Any]) -> dict[str, Any]:
    """Keep stable envelope structure while ignoring generated identities."""
    result = body["result"]
    formatted = result["formatted"]
    execution = result["execution"]
    return {
        "object": body["object"],
        "agent": body["agent"],
        "status": body["status"],
        "task_count": len(body["task_ids"]),
        "has_run_id": "run_id" in body,
        "result_keys": tuple(sorted(result)),
        "formatted_keys": tuple(sorted(formatted)),
        "execution_keys": tuple(sorted(execution)),
    }


def _assert_pending_research(
    db_path: str, run_id: str, *, query: str = "q"
) -> None:
    """Assert the HTTP seam persisted admission without an installed root."""
    resolution = ResearchInputStore(db_path).load_resolution(run_id)
    assert resolution is not None
    assert resolution["status"] == "pending"
    assert resolution["effective_query"] == query
    assert resolution["managed_snapshot_json"] == []


async def _wait_for_run(
    db_path: str,
    run_id: str,
    **options: Any,
) -> RunRecord:
    """Poll one owned run without sleeping the event loop thread."""
    owner = options.get("owner", "u1")
    task_ids = options.get("task_ids")
    status = options.get("status")
    attempts = options.get("attempts", 100)
    registry = RunRegistry(db_path)
    for _ in range(attempts):
        record = registry.get_run(run_id, owner=owner)
        if record is not None:
            tasks_match = task_ids is None or set(record.task_ids) == task_ids
            status_match = status is None or record.status == status
            if tasks_match and status_match:
                await asyncio.sleep(0)
                return record
        await asyncio.sleep(0)
    expected = f"tasks={sorted(task_ids) if task_ids is not None else '*'}"
    if status is not None:
        expected += f", status={status}"
    pytest.fail(f"run {run_id} did not reach {expected}")


@dataclass(frozen=True, slots=True)
class _ParityCase:
    """One direct/native versus Expert request pair."""

    tool_name: str
    slug: str
    expected_status: int
    native_args: dict[str, Any]
    selected_args: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _FailureCase:
    """One typed Expert failure and its public mapping."""

    failure: type[Exception]
    expected_status: int
    expected_code: str
    retryable: bool


def _parity_handler(case: _ParityCase) -> Any:
    """Build the direct/Expert fake handler for one parity case."""
    calls = 0

    def submission_payload(call_number: int) -> dict[str, Any]:
        """Return the canonical remote payload for one invocation."""
        task_id = f"expert-parity-{case.slug}-{call_number}"
        if case.slug == "analyst":
            return {"task_id": task_id, "output_dir": "tenant/expert-parity"}
        if case.slug == "research":
            return {
                "task_ids": [task_id],
                "output_dir": "tenant/expert-parity",
            }
        if case.slug == "network":
            return {
                "network_task": {
                    "task_id": task_id,
                    "output_dir": "tenant/expert-parity",
                }
            }
        if case.slug == "design":
            return {
                "design_task_result": [
                    {
                        "task_id": task_id,
                        "output_dir": "tenant/expert-parity",
                    }
                ]
            }
        raise AssertionError(f"unexpected background slug: {case.slug}")

    async def fake(_args: Any) -> dict[str, Any]:
        """Return the selected case's sync or remote response."""
        nonlocal calls
        calls += 1
        if case.expected_status == 202:
            return submission_payload(calls)
        return {"answer": f"answer-{calls}", "doc_list": []}

    if case.expected_status == 202:
        return records_submission(case.slug)(fake)
    return fake


_PARITY_CASES = (
    pytest.param(
        _ParityCase(
            "ChatAgent",
            "chat",
            200,
            {"user_query": "q", "obs_file_list": []},
            {"user_query": "q"},
        ),
        id="chat-sync",
    ),
    pytest.param(
        _ParityCase(
            "DataAgent",
            "data",
            200,
            {"user_query": "q"},
            {"user_query": "q"},
        ),
        id="data-sync",
    ),
    pytest.param(
        _ParityCase(
            "AnalystAgent",
            "analyst",
            202,
            {
                "goal_description": "q",
                "data_list": {},
                "obs_file_list": [],
            },
            {
                "goal_description": "q",
                "data_list": {},
                "obs_file_list": [],
            },
        ),
        id="analyst-remote",
    ),
    pytest.param(
        _ParityCase(
            "InSilicoResearchAgent",
            "research",
            202,
            {"user_query": "q", "data_list": {}, "obs_file_list": []},
            {"user_query": "q", "data_list": {}, "obs_file_list": []},
        ),
        id="research-remote",
    ),
    pytest.param(
        _ParityCase(
            "DigitalDesignAgent",
            "design",
            202,
            {
                "species_code": "ath",
                "gene_id": "AT1G01010",
                "obs_file_list": [],
                "resolve_gene_id": False,
            },
            {
                "species_code": "ath",
                "gene_id": "AT1G01010",
                "obs_file_list": [],
                "resolve_gene_id": False,
            },
        ),
        id="design-remote",
    ),
    pytest.param(
        _ParityCase(
            "GeneNetworkAgent",
            "network",
            202,
            {
                "species_code": "osa",
                "to_id": "TO:0000207",
                "obs_file_list": [],
                "resolve_to_id": False,
            },
            {
                "species_code": "osa",
                "to_id": "TO:0000207",
                "obs_file_list": [],
                "resolve_to_id": False,
            },
        ),
        id="network-remote",
    ),
)


_FAILURE_CASES = (
    pytest.param(
        _FailureCase(
            ExpertRoutingContractError,
            502,
            "routing_contract_violation",
            False,
        ),
        id="contract",
    ),
    pytest.param(
        _FailureCase(
            ExpertProviderTimeoutError,
            504,
            "upstream_timeout",
            True,
        ),
        id="timeout",
    ),
    pytest.param(
        _FailureCase(
            ExpertProviderError,
            502,
            "routing_upstream_failed",
            True,
        ),
        id="provider",
    ),
)


@pytest.mark.parametrize(
    "case",
    _PARITY_CASES,
)
async def test_expert_uses_native_run_contract(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    case: _ParityCase,
) -> None:
    """Expert and direct native runs expose the same envelope structure."""
    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        case.tool_name,
        _parity_handler(case),
    )

    direct_headers = _auth(issued_api_key)
    if case.slug == "research":
        direct_headers["Idempotency-Key"] = "test-direct-research"
    direct = await api_client.post(
        f"/v1/agents/{case.slug}/runs",
        headers=direct_headers,
        json={"arguments": case.native_args},
    )
    assert direct.status_code == case.expected_status
    direct_body = direct.json()
    direct_record: RunRecord | None = None
    if case.expected_status == 202:
        if case.slug == "research":
            direct_record = await _wait_for_run(
                api_app.resolve_tasks_db_path(),
                direct_body["run_id"],
                task_ids=set(),
                status="running",
            )
            _assert_pending_research(
                api_app.resolve_tasks_db_path(), direct_body["run_id"]
            )
        else:
            direct_task_ids = {f"expert-parity-{case.slug}-1"}
            assert direct_body["id"] == direct_body["run_id"]
            assert set(direct_body["task_ids"]) == direct_task_ids
            assert {
                task["id"]
                for task in direct_body["result"]["execution"]["tasks"]
            } == direct_task_ids
            direct_record = await _wait_for_run(
                api_app.resolve_tasks_db_path(),
                direct_body["run_id"],
                task_ids=direct_task_ids,
                status="running",
            )
            assert direct_record.spec.agent == case.slug

    _patch_selection(monkeypatch, case.tool_name, case.selected_args)
    routed = await _post_forced_expert(
        api_client,
        issued_api_key,
        case.tool_name,
    )

    assert routed.status_code == case.expected_status
    routed_body = routed.json()
    assert routed_body["agent"] == case.slug
    assert routed_body["agent"] != "expert"
    if case.expected_status == 202:
        if case.slug == "research":
            routed_record = await _wait_for_run(
                api_app.resolve_tasks_db_path(),
                routed_body["run_id"],
                task_ids=set(),
                status="running",
            )
            _assert_pending_research(
                api_app.resolve_tasks_db_path(), routed_body["run_id"]
            )
            assert direct_record is not None
            assert routed_record.spec.agent == "research"
            assert routed_record.task_ids == direct_record.task_ids == ()
            return
        routed_task_ids = {f"expert-parity-{case.slug}-2"}
        assert routed_body["id"] == routed_body["run_id"]
        assert set(routed_body["task_ids"]) == routed_task_ids
        assert {
            task["id"] for task in routed_body["result"]["execution"]["tasks"]
        } == routed_task_ids
        routed_record = await _wait_for_run(
            api_app.resolve_tasks_db_path(),
            routed_body["run_id"],
            task_ids=routed_task_ids,
            status="running",
        )
        assert routed_record.spec.run_id == routed_body["run_id"]
        assert routed_record.spec.agent == case.slug
        assert (
            routed_record.request_info.request_id
            == routed.headers["X-Request-Id"]
        )
        assert routed_record.request_info.tool_name == case.tool_name
        assert routed_record.request_info.query == (
            "q" if case.slug == "analyst" else None
        )
        assert json.loads(routed_record.request_info.request_json or "{}") == {
            "agent": case.slug,
            "tool_name": case.tool_name,
            "dialogue_id": None,
            "locale": "en-US",
        }
        assert direct_record is not None
        assert routed_record.task_ids != direct_record.task_ids
    else:
        assert _contract_shape(routed_body) == _contract_shape(direct_body)


async def test_expert_partial_remote_preserves_execution_warnings(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial remote acceptance keeps warnings in the shared result."""

    async def fake(_args: Any) -> dict[str, Any]:
        return {
            "task_ids": ["expert-partial-1"],
            "output_dir": "tenant/expert-partial",
            "submission_warnings": [
                {
                    "code": "partial_submission",
                    "retryable": False,
                    "rejected_count": 1,
                    "private_error": "must not leak",
                }
            ],
        }

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.IN_SILICO_RESEARCH_AGENT.value,
        records_submission("research")(fake),
    )
    _patch_selection(
        monkeypatch,
        "InSilicoResearchAgent",
        {"user_query": "q", "data_list": {}, "obs_file_list": []},
    )

    response = await _post_forced_expert(
        api_client,
        issued_api_key,
        "InSilicoResearchAgent",
    )

    assert response.status_code == 202
    body = response.json()
    assert body["agent"] == "research"
    assert body["id"] == body["run_id"]
    assert body["task_ids"] == []
    assert body["result"] == empty_agent_result()

    record = await _wait_for_run(
        api_app.resolve_tasks_db_path(),
        body["run_id"],
        task_ids=set(),
        status="running",
    )
    _assert_pending_research(api_app.resolve_tasks_db_path(), body["run_id"])
    assert record.result is None
    assert "must not leak" not in response.text


@pytest.mark.parametrize(
    "case",
    _FAILURE_CASES,
)
async def test_expert_routing_failures_are_safe_and_side_effect_free(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    case: _FailureCase,
) -> None:
    """Typed selector/provider failures never dispatch or persist a run."""
    sentinel = (
        "ROUTER-PROMPT-SENTINEL RAW-MODEL-SENTINEL "
        "ALLOWLIST-SENTINEL PROVIDER-PAYLOAD-SENTINEL "
        "credential-like-sentinel"
    )

    async def fail_select(*_args: Any, **_kwargs: Any) -> ToolSelection:
        raise case.failure(sentinel)

    monkeypatch.setattr(api_app, "select_agent_tool", fail_select)
    caplog.set_level(logging.WARNING, logger="mcp_server_phytomni.api.app")

    response = await _post_forced_expert(
        api_client,
        issued_api_key,
        "ChatAgent",
    )

    assert response.status_code == case.expected_status
    error = response.json()["error"]
    assert error["code"] == case.expected_code
    assert error["stage"] == "routing"
    assert error["retryable"] is case.retryable
    assert sentinel not in response.text
    assert sentinel not in caplog.text
    assert not RunRegistry(api_app.resolve_tasks_db_path()).list_runs(
        owner="u1"
    )


async def test_expert_degraded_remote_preserves_accepted_task_ids(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A child persistence failure settles the reserved run safely."""

    def _raising_reserved_submissions(*_args: Any, **_kwargs: Any) -> bool:
        raise sqlite3.OperationalError("private registry failure")

    monkeypatch.setattr(
        RunRegistry,
        "record_reserved_submissions",
        _raising_reserved_submissions,
    )

    async def fake(_args: Any) -> dict[str, Any]:
        return {
            "task_id": "expert-degraded-1",
            "output_dir": "tenant/expert-degraded",
        }

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.ANALYST_AGENT.value,
        records_submission("analyst")(fake),
    )
    _patch_analyst_selection(monkeypatch, goal="q")

    response = await _post_forced_expert(
        api_client,
        issued_api_key,
        "AnalystAgent",
    )

    assert response.status_code == 202
    body = response.json()
    assert body["id"] == body["run_id"]
    assert body["task_ids"] == ["expert-degraded-1"]
    assert body["result"]["execution"]["tracking"] == {"degraded": True}
    assert [task["id"] for task in body["result"]["execution"]["tasks"]] == [
        "expert-degraded-1"
    ]
    assert body["agent"] == "analyst"
    record = await _wait_for_run(
        tasks_db_path,
        body["run_id"],
        status="running",
    )
    assert record.error is None
    assert record.task_ids == ()
    assert record.request_info.execution_id
    projection = SQLiteExecutionJournal(tasks_db_path).get_projection(
        record.request_info.execution_id, owner="u1"
    )
    assert projection.status.value == "running"
    assert projection.tracking_health.value == "degraded"
    assert "private registry failure" not in response.text


async def test_expert_run_info_keeps_native_identity_without_router_payload(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """Expert rows persist the original query and only safe route metadata."""

    async def fake(_args: Any) -> dict[str, Any]:
        return {"task_id": "expert-identity-1", "output_dir": "tenant/out"}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.ANALYST_AGENT.value,
        records_submission("analyst")(fake),
    )
    _patch_analyst_selection(monkeypatch, goal="selected goal")

    response = await _post_forced_expert(
        api_client,
        issued_api_key,
        "AnalystAgent",
        body={
            "user_query": "original user question",
            "history": [{"role": "user", "content": "private history"}],
            "dialogue_id": "dialogue-expert-1",
            "locale": "zh-CN",
        },
        extra_headers={"Accept-Language": "en-US"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["id"] == body["run_id"]
    assert body["task_ids"] == ["expert-identity-1"]
    assert [task["id"] for task in body["result"]["execution"]["tasks"]] == [
        "expert-identity-1"
    ]
    record = RunRegistry(tasks_db_path).get_run(body["run_id"], owner="u1")
    assert record is not None
    info = record.request_info
    assert info.dialogue_id == "dialogue-expert-1"
    assert info.tool_name == "AnalystAgent"
    assert info.locale == "zh-CN"
    assert info.request_id == response.headers["X-Request-Id"]
    assert info.query == "original user question"
    assert json.loads(info.request_json or "{}") == {
        "agent": "analyst",
        "tool_name": "AnalystAgent",
        "dialogue_id": "dialogue-expert-1",
        "locale": "zh-CN",
    }
    record = await _wait_for_run(
        tasks_db_path,
        body["run_id"],
        task_ids={"expert-identity-1"},
        status="running",
    )
    assert record.task_ids == ("expert-identity-1",)
