# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Data-only HTTP fakes shared by native resolver route tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from mcp_server_phytomni import server
from mcp_server_phytomni.api import app as api_app
from mcp_server_phytomni.runtime.run_registry import RunRecord, RunRegistry
from mcp_server_phytomni.runtime.submit_recorder import records_submission
from tests.support.http_fakes import install_tool_handler


def assert_invalid_argument_response(response: httpx.Response) -> None:
    """Assert the stable 400 envelope shared by resolver route tests."""
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "invalid_argument"
    assert body["error"]["message"] == "invalid request"


@dataclass(frozen=True)
class ResolverCaseSpec:
    """Static route and resolver wiring for one native agent."""

    slug: str
    tool_name: str
    answer: str
    resolver_attribute: str
    result_factory: Callable[[str, str], Any]
    error_factory: Callable[[str], Exception]
    background_submission: bool = False


@dataclass(frozen=True)
class ResolverCaseExpected:
    """Expected values and error messages for one native resolver agent."""

    species_code: str
    resolved_gene_id: str
    resolved_raw_query: str
    failure_message: str
    blank_message: str


@dataclass(frozen=True)
class ResolverCaseArguments:
    """Scenario-specific request arguments for one native resolver agent."""

    resolved_arguments: dict[str, Any]
    passthrough_arguments: dict[str, Any]
    missing_arguments: dict[str, Any]
    failure_arguments: dict[str, Any]
    blank_arguments: dict[str, Any]


@dataclass(frozen=True)
class NativeResolverCase:
    """Data-only contract inputs for one native resolver agent."""

    spec: ResolverCaseSpec
    expected: ResolverCaseExpected
    arguments: ResolverCaseArguments


@dataclass(frozen=True)
class NativeResolverContext:
    """Pytest resources required by one native resolver route test."""

    api_client: httpx.AsyncClient
    issued_api_key: str
    monkeypatch: pytest.MonkeyPatch
    tasks_db_path: str


@dataclass(frozen=True)
class NativeResolverObservation:
    """Observed response and resolver state for one scenario assertion."""

    response: httpx.Response
    case: NativeResolverCase
    scenario: str
    captured: dict[str, Any]
    resolver_calls: list[str]
    arguments: dict[str, Any]
    tasks_db_path: str


@dataclass(frozen=True)
class NativeRunHandlerSpec:
    """Handler and request data for one native route fixture."""

    tool_name: str
    handler: Any
    agent_slug: str
    arguments: dict[str, Any]
    dialogue_id: str | None = None


def register_gene_capture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    agent_slug: str,
    tool_name: str,
    captured: dict[str, Any],
    answer: str,
) -> None:
    """Register a handler that captures the resolved gene arguments."""

    async def fake(args: Any) -> dict[str, Any]:
        """Capture the structured species and gene arguments."""
        captured["species_code"] = args.species_code
        captured["gene_id"] = args.gene_id
        task = {
            "task_id": f"{agent_slug}-resolver-task",
            "output_dir": "tenant/resolver",
        }
        if agent_slug == "network":
            return {"network_task": task, "answer": answer}
        if agent_slug == "design":
            return {"design_task_result": [task], "answer": answer}
        return {**task, "answer": answer}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        tool_name,
        records_submission(agent_slug)(fake),
    )


async def post_native_run(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    agent_slug: str,
    arguments: dict[str, Any],
    *,
    dialogue_id: str | None = None,
) -> httpx.Response:
    """POST one native agent run with a typed argument mapping."""
    payload: dict[str, Any] = {"arguments": arguments}
    if dialogue_id is not None:
        payload["dialogue_id"] = dialogue_id
    return await api_client.post(
        f"/v1/agents/{agent_slug}/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json=payload,
    )


async def post_native_run_with_handler(
    monkeypatch: pytest.MonkeyPatch,
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    spec: NativeRunHandlerSpec,
) -> httpx.Response:
    """Install one tool fake and invoke its native route."""
    install_tool_handler(monkeypatch, spec.tool_name, spec.handler)
    return await post_native_run(
        api_client,
        issued_api_key,
        spec.agent_slug,
        spec.arguments,
        dialogue_id=spec.dialogue_id,
    )


async def post_duplicate_attachment_run(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    path: str,
) -> httpx.Response:
    """Invoke the Chat route with the same path twice for validation tests."""
    return await post_native_run(
        api_client,
        issued_api_key,
        "chat",
        {"user_query": "hi", "obs_file_list": [path, path]},
    )


async def post_recorded_analyst_run(
    monkeypatch: pytest.MonkeyPatch,
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    fake: Callable[[Any], Any],
    arguments: dict[str, Any],
) -> httpx.Response:
    """Invoke the analyst route through the submission recorder seam."""
    return await post_native_run_with_handler(
        monkeypatch,
        api_client,
        issued_api_key,
        NativeRunHandlerSpec(
            tool_name=server.PhytomniAgents.ANALYST_AGENT.value,
            handler=records_submission("analyst")(fake),
            agent_slug="analyst",
            arguments=arguments,
        ),
    )


def _assert_resolved_metadata(
    metadata: dict[str, Any],
    observation: NativeResolverObservation,
) -> None:
    """Assert the metadata and resolver calls for a resolved scenario."""
    expected = observation.case.expected
    assert observation.captured["species_code"] == expected.species_code
    assert observation.captured["gene_id"] == expected.resolved_gene_id
    assert observation.resolver_calls == [expected.resolved_raw_query]
    assert metadata.get("original_query") == expected.resolved_raw_query
    assert metadata.get("resolved_gene_id") == expected.resolved_gene_id
    assert metadata.get("resolved_species_code") == expected.species_code
    assert metadata.get("resolve_gene_id") is True


async def _wait_for_background_record(
    observation: NativeResolverObservation,
) -> RunRecord:
    """Wait until a background resolver run reaches its expected state."""
    body = observation.response.json()
    assert body["task_ids"] == []
    assert body["run_id"]
    registry = RunRegistry(observation.tasks_db_path)
    record: RunRecord | None = None
    for _ in range(100):
        record = registry.get_run(body["run_id"], owner="u1")
        if record is not None:
            if observation.scenario in {"resolved", "passthrough"} and (
                record.task_ids
            ):
                break
            if observation.scenario in {"missing", "failure", "blank"} and (
                record.status == "failed"
            ):
                break
        await asyncio.sleep(0)
    else:
        pytest.fail("background resolver run did not settle")
    assert record is not None
    return record


def _assert_background_resolver_success(
    observation: NativeResolverObservation,
    record: RunRecord,
) -> None:
    """Assert a successful background resolver projection."""
    if observation.scenario == "resolved":
        assert record.result is not None
        metadata = record.result["formatted"].get("metadata") or {}
        _assert_resolved_metadata(metadata, observation)
        return
    assert observation.captured["gene_id"] == (
        observation.case.expected.resolved_gene_id
    )
    assert not observation.resolver_calls


def _assert_background_resolver_failure(
    observation: NativeResolverObservation,
    record: RunRecord,
) -> None:
    """Assert a safely sanitized background resolver failure."""
    assert record.status == "failed"
    assert not record.task_ids
    assert record.error == "background_submission_failed"
    assert observation.case.expected.failure_message not in record.error
    assert observation.case.expected.blank_message not in record.error
    if observation.scenario == "missing":
        assert not observation.resolver_calls
    else:
        assert observation.resolver_calls == [
            observation.arguments["user_query"]
        ]
    assert "gene_id" not in observation.captured


async def _assert_background_resolver_observation(
    observation: NativeResolverObservation,
) -> None:
    """Assert the accepted-run contract for a background resolver call."""
    assert observation.response.status_code == 202
    record = await _wait_for_background_record(observation)
    if observation.scenario in {"resolved", "passthrough"}:
        _assert_background_resolver_success(observation, record)
    else:
        _assert_background_resolver_failure(observation, record)


def _assert_sync_resolver_observation(
    observation: NativeResolverObservation,
) -> None:
    """Assert the direct-response contract for a synchronous resolver call."""
    expected = observation.case.expected
    if observation.scenario == "resolved":
        assert observation.response.status_code == 202
        body = observation.response.json()
        metadata = body["result"]["formatted"].get("metadata") or {}
        _assert_resolved_metadata(metadata, observation)
        return
    if observation.scenario == "passthrough":
        assert observation.response.status_code == 202
        assert observation.captured["gene_id"] == expected.resolved_gene_id
        assert not observation.resolver_calls
        return
    assert_invalid_argument_response(observation.response)
    if observation.scenario == "missing":
        assert not observation.resolver_calls
    else:
        assert observation.resolver_calls == [
            observation.arguments["user_query"]
        ]
    assert "gene_id" not in observation.captured


async def assert_native_resolver_case(
    context: NativeResolverContext,
    case: NativeResolverCase,
    *,
    scenario: str,
) -> None:
    """Exercise one named resolver scenario while keeping route assertions."""
    captured: dict[str, Any] = {}
    resolver_calls: list[str] = []
    register_gene_capture(
        context.monkeypatch,
        agent_slug=case.spec.slug,
        tool_name=case.spec.tool_name,
        captured=captured,
        answer=case.spec.answer,
    )

    async def fake_resolve(raw_query: str, **_kwargs: Any) -> Any:
        """Return the case result or raise its typed error."""
        resolver_calls.append(raw_query)
        if scenario == "failure":
            raise case.spec.error_factory(case.expected.failure_message)
        if scenario == "blank":
            raise case.spec.error_factory(case.expected.blank_message)
        return case.spec.result_factory(
            case.expected.resolved_gene_id, raw_query
        )

    context.monkeypatch.setattr(
        api_app, case.spec.resolver_attribute, fake_resolve
    )

    arguments = {
        "resolved": case.arguments.resolved_arguments,
        "passthrough": case.arguments.passthrough_arguments,
        "missing": case.arguments.missing_arguments,
        "failure": case.arguments.failure_arguments,
        "blank": case.arguments.blank_arguments,
    }[scenario]
    response = await post_native_run(
        context.api_client, context.issued_api_key, case.spec.slug, arguments
    )
    observation = NativeResolverObservation(
        response=response,
        case=case,
        scenario=scenario,
        captured=captured,
        resolver_calls=resolver_calls,
        arguments=arguments,
        tasks_db_path=context.tasks_db_path,
    )
    if case.spec.background_submission:
        await _assert_background_resolver_observation(observation)
    else:
        _assert_sync_resolver_observation(observation)


def make_native_resolver_test(
    case: NativeResolverCase,
    *,
    scenario: str,
    test_name: str,
    docstring: str,
) -> Callable[..., Any]:
    """Build one fixture-aware test function for a named case."""

    async def test(
        api_client: httpx.AsyncClient,
        issued_api_key: str,
        monkeypatch: pytest.MonkeyPatch,
        tasks_db_path: str,
    ) -> None:
        context = NativeResolverContext(
            api_client=api_client,
            issued_api_key=issued_api_key,
            monkeypatch=monkeypatch,
            tasks_db_path=tasks_db_path,
        )
        await assert_native_resolver_case(
            context,
            case,
            scenario=scenario,
        )

    test.__name__ = test_name
    test.__doc__ = docstring
    return test


def install_native_resolver_tests(
    namespace: dict[str, Any], case: NativeResolverCase
) -> None:
    """Install the stable five-scenario test names for one native agent."""
    failure_names = (
        (
            "test_native_runs_missing_user_query_settles_failed",
            "test_native_runs_resolver_failure_settles_failed",
            "test_native_runs_blank_species_code_settles_failed",
        )
        if case.spec.background_submission
        else (
            "test_native_runs_rejects_missing_user_query",
            "test_native_runs_resolver_failure_returns_400",
            "test_native_runs_blank_species_code_returns_400",
        )
    )
    scenarios = (
        (
            "test_native_runs_resolves_when_flag_true",
            "resolved",
            "flag=true rewrites user_query into gene_id and stamps metadata.",
        ),
        (
            "test_native_runs_skips_resolver_when_flag_false",
            "passthrough",
            "flag=false leaves gene_id as-is from the structured request.",
        ),
        (
            failure_names[0],
            "missing",
            "flag=true without user_query settles the accepted run failed.",
        ),
        (
            failure_names[1],
            "failure",
            "ResolverError settles the accepted run failed safely.",
        ),
        (
            failure_names[2],
            "blank",
            "Blank species_code settles the accepted run failed safely.",
        ),
    )
    for test_name, scenario, docstring in scenarios:
        namespace[test_name] = make_native_resolver_test(
            case,
            scenario=scenario,
            test_name=test_name,
            docstring=docstring,
        )
