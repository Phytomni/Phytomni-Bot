# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Data-only HTTP fakes shared by native resolver route tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from mcp_server_phytomni import server
from mcp_server_phytomni.api import app as api_app


@dataclass(frozen=True)
class ResolverCaseSpec:
    """Static route and resolver wiring for one native agent."""

    slug: str
    tool_name: str
    answer: str
    resolver_attribute: str
    result_factory: Callable[[str, str], Any]
    error_factory: Callable[[str], Exception]


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


def register_gene_capture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tool_name: str,
    captured: dict[str, Any],
    answer: str,
) -> None:
    """Register a handler that captures the resolved gene arguments."""

    async def fake(args: Any) -> dict[str, Any]:
        """Capture the structured species and gene arguments."""
        captured["species_code"] = args.species_code
        captured["gene_id"] = args.gene_id
        return {"answer": answer, "doc_list": []}

    monkeypatch.setitem(server.TOOL_HANDLERS, tool_name, fake)


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

    if scenario == "resolved":
        assert response.status_code == 202
        body = response.json()
        assert captured["species_code"] == case.expected.species_code
        assert captured["gene_id"] == case.expected.resolved_gene_id
        assert resolver_calls == [case.expected.resolved_raw_query]
        metadata = body["result"]["formatted"].get("metadata") or {}
        assert (
            metadata.get("original_query") == case.expected.resolved_raw_query
        )
        assert (
            metadata.get("resolved_gene_id") == case.expected.resolved_gene_id
        )
        assert (
            metadata.get("resolved_species_code") == case.expected.species_code
        )
        assert metadata.get("resolve_gene_id") is True
        return

    if scenario == "passthrough":
        assert response.status_code == 202
        assert captured["gene_id"] == case.expected.resolved_gene_id
        assert not resolver_calls
        return

    assert response.status_code == 400
    body = response.json()
    if scenario == "missing":
        assert "user_query" in body["error"]["message"]
    if scenario == "failure":
        assert case.expected.failure_message in body["error"]["message"]
    if scenario == "blank":
        assert "species_code" in body["error"]["message"]
    if scenario == "missing":
        assert not resolver_calls
    else:
        assert resolver_calls == [arguments["user_query"]]
    assert "gene_id" not in captured


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
        del tasks_db_path
        context = NativeResolverContext(
            api_client=api_client,
            issued_api_key=issued_api_key,
            monkeypatch=monkeypatch,
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
            "test_native_runs_rejects_missing_user_query",
            "missing",
            "flag=true without user_query is 400 before the resolver runs.",
        ),
        (
            "test_native_runs_resolver_failure_returns_400",
            "failure",
            "ResolverError surfaces as HTTP 400 with the reason.",
        ),
        (
            "test_native_runs_blank_species_code_returns_400",
            "blank",
            "Blank species_code from the resolver maps to HTTP 400.",
        ),
    )
    for test_name, scenario, docstring in scenarios:
        namespace[test_name] = make_native_resolver_test(
            case,
            scenario=scenario,
            test_name=test_name,
            docstring=docstring,
        )
