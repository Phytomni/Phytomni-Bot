# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Expert attachment preselection, authorization, and context parity tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from tests.server.test_api_agent_context_runs import (
    _assert_selection_mismatch,
    _context_row_counts,
    _count_context_mutations,
)
from tests.server.test_query_route import (
    RunRegistry,
    ToolSelection,
    _auth,
    _patch_select,
    _router_completion,
    _stub_tool_handler,
    api_app,
    httpx,
)
from tests.support.chat_fakes import install_chat_handler
from tests.support.expert_router_fakes import patch_expert_router
from tests.support.http_fakes import (
    build_instant_chat_context_envelope,
    open_asgi_client,
    running_agent_run_body,
)
from tests.support.resumable_asset_fakes import (
    AssetHttpTestContext,
    enable_conversation_context_v1,
    install_dataset_and_document_assets,
)

import mcp_server_phytomni.api.agent_runs as agent_runs_module
from mcp_server_phytomni.agents.expert import router as expert_router
from mcp_server_phytomni.api.research_input import ResearchHttpAdmissionInput
from mcp_server_phytomni.api.routes import (
    expert_context as expert_context_routes,
)
from mcp_server_phytomni.api.schemas import ExpertQueryRequest

pytestmark = pytest.mark.server


@dataclass(frozen=True, slots=True)
class _PreselectorCase:
    """One Expert preselector channel-filter expectation."""

    attachments_kind: str
    allowed: tuple[str, ...]
    expected_tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _AuthzCase:
    """One Expert attachment authorization failure expectation."""

    allowed: tuple[str, ...]
    forced: str | None
    attachments: str


@dataclass(frozen=True, slots=True)
class _ExpertAssets:
    """Installed dataset/document assets for one Expert HTTP app."""

    app: Any
    dataset_id: str
    document_id: str
    dataset_ref: str
    document_ref: str


def _install_expert_purpose_assets(
    context: AssetHttpTestContext,
    *,
    owner: str = "u1",
) -> _ExpertAssets:
    """Install completed dataset/document assets into the app upload DB."""
    resolver, dataset_id, document_id = install_dataset_and_document_assets(
        context,
        owner=owner,
        dataset_filename="expert.csv",
        document_filename="expert.pdf",
    )
    dataset_ref = (
        resolver.resolve_bundle([{"asset_id": dataset_id}], owner)
        .datasets[0]
        .reference
    )
    document_ref = (
        resolver.resolve_bundle([{"asset_id": document_id}], owner)
        .documents[0]
        .reference
    )
    return _ExpertAssets(
        app=api_app.create_app(),
        dataset_id=dataset_id,
        document_id=document_id,
        dataset_ref=dataset_ref,
        document_ref=document_ref,
    )


def _attachments_for(
    assets: _ExpertAssets,
    kind: str,
) -> list[dict[str, str]]:
    """Build the opaque attachment list for one preselector kind."""
    if kind == "dataset":
        return [{"asset_id": assets.dataset_id}]
    if kind == "mixed":
        return [
            {"asset_id": assets.dataset_id},
            {"asset_id": assets.document_id},
        ]
    if kind == "document":
        return [{"asset_id": assets.document_id}]
    return []


def _selection_args(tool_name: str) -> dict[str, Any]:
    """Return schema-shaped selector arguments for one tool."""
    if tool_name == "AnalystAgent":
        return {
            "goal_description": "q",
            "data_list": {},
            "obs_file_list": [],
        }
    if tool_name == "InSilicoResearchAgent":
        return {"user_query": "q", "data_list": {}, "obs_file_list": []}
    return {"user_query": "canonical expert query"}


def _install_selected_handler(
    monkeypatch: pytest.MonkeyPatch,
    selected: str,
) -> None:
    """Install a sync or remote handler for the selected Expert tool."""
    if selected in {"AnalystAgent", "InSilicoResearchAgent"}:

        async def fake_invoke(**kwargs: Any) -> tuple[dict[str, Any], int]:
            return (
                running_agent_run_body("filtered-run", kwargs["agent"]),
                202,
            )

        if selected == "InSilicoResearchAgent":

            async def fake_research(
                _admission: ResearchHttpAdmissionInput,
                _bundle: Any,
                **_kwargs: Any,
            ) -> tuple[dict[str, Any], int]:
                return running_agent_run_body("filtered-run", "research"), 202

            monkeypatch.setattr(
                agent_runs_module, "invoke_research_http_run", fake_research
            )
        else:
            monkeypatch.setattr(api_app, "_invoke_agent_run", fake_invoke)
        return
    install_chat_handler(monkeypatch, {}, content="ok")
    if selected != "ChatAgent":
        _stub_tool_handler(
            monkeypatch,
            selected,
            {"answer": "ok", "doc_list": []},
        )


async def _post_expert_route(
    context: AssetHttpTestContext,
    app: Any,
    *,
    payload: dict[str, Any],
    base_url: str,
    api_key: str | None = None,
) -> httpx.Response:
    """POST one Expert route against a freshly created ASGI app."""
    async with open_asgi_client(
        context.monkeypatch, app, base_url=base_url
    ) as client:
        return await client.post(
            "/v1/query/route",
            headers={
                **_auth(api_key or context.api_key),
                "Idempotency-Key": "expert-route-test-key",
            },
            json=payload,
        )


def _forbid_router_and_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[Any], list[Any]]:
    """Install assertions that Expert routing never reaches selection."""
    router_calls: list[Any] = []
    agent_calls: list[Any] = []

    async def forbid_select(*_args: Any, **_kwargs: Any) -> ToolSelection:
        router_calls.append(1)
        raise AssertionError("router must not run")

    async def forbid_invoke(**_kwargs: Any) -> tuple[dict[str, Any], int]:
        agent_calls.append(1)
        raise AssertionError("agent must not run")

    monkeypatch.setattr(api_app, "select_agent_tool", forbid_select)
    monkeypatch.setattr(api_app, "_invoke_agent_run", forbid_invoke)
    return router_calls, agent_calls


def _expert_context_envelope(
    *,
    allowed: list[str],
    requested: str | None,
    request_id: str,
    content: str,
) -> dict[str, Any]:
    """Build one Expert-mode Instant envelope for context attachment tests."""
    envelope = build_instant_chat_context_envelope("9")
    envelope.update(
        {
            "mode": "expert",
            "request_id": request_id,
            "requested_agent_id": requested,
            "allowed_agent_ids": allowed,
            "current_message": {"content": content, "locale": "en-US"},
        }
    )
    return envelope


_PRESELECTOR_CASES = (
    _PreselectorCase(
        "dataset",
        (
            "ChatAgent",
            "AnalystAgent",
            "InSilicoResearchAgent",
            "KnowledgeAgent",
        ),
        (
            "ChatAgent",
            "AnalystAgent",
            "InSilicoResearchAgent",
            "KnowledgeAgent",
        ),
    ),
    _PreselectorCase(
        "dataset",
        ("InSilicoResearchAgent", "AnalystAgent"),
        ("InSilicoResearchAgent", "AnalystAgent"),
    ),
    _PreselectorCase(
        "mixed",
        ("AnalystAgent", "InSilicoResearchAgent"),
        (
            "AnalystAgent",
            "InSilicoResearchAgent",
        ),
    ),
    _PreselectorCase(
        "document",
        ("DataAgent", "ChatAgent", "AnalystAgent", "KnowledgeAgent"),
        ("ChatAgent", "AnalystAgent", "KnowledgeAgent"),
    ),
    _PreselectorCase(
        "none",
        ("ChatAgent", "DataAgent", "KnowledgeAgent"),
        ("ChatAgent", "DataAgent", "KnowledgeAgent"),
    ),
)


def _assert_research_attachment_call(
    call: dict[str, Any],
    *,
    document_ref: str,
    dataset_ref: str,
) -> None:
    """Assert one Research admission owns canonical query and assets."""
    admission = call["admission"]
    bundle = call["bundle"]
    assert admission.original_query == "original expert query"
    assert bundle.documents[0].reference == document_ref
    assert bundle.datasets[0].reference == dataset_ref


_AUTHZ_CASES = (
    _AuthzCase(("DataAgent", "BriefGeneAgent"), None, "dataset"),
    _AuthzCase(("DataAgent", "AnalystAgent"), "DataAgent", "dataset"),
    _AuthzCase(
        ("AnalystAgent", "BriefGeneAgent"),
        "BriefGeneAgent",
        "mixed",
    ),
)


@pytest.mark.parametrize("case", _PRESELECTOR_CASES)
async def test_expert_preselector_filters_tools_by_attachment_channels(
    asset_http_context: AssetHttpTestContext,
    case: _PreselectorCase,
) -> None:
    """Opaque attachment channels filter ordered Expert authorization."""
    assets = _install_expert_purpose_assets(asset_http_context)
    selected = case.expected_tools[0]
    captured: dict[str, Any] = {}
    patch_expert_router(
        asset_http_context.monkeypatch,
        expert_router,
        _router_completion((selected, json.dumps(_selection_args(selected)))),
        captured=captured,
    )
    _install_selected_handler(asset_http_context.monkeypatch, selected)
    response = await _post_expert_route(
        asset_http_context,
        assets.app,
        payload={
            "user_query": "canonical expert query",
            "allowed_tools": list(case.allowed),
            "attachments": _attachments_for(assets, case.attachments_kind),
        },
        base_url="http://api.expert-filter.test",
    )
    assert response.status_code in {200, 202}, response.text
    tool_names = [
        tool["function"]["name"] for tool in captured.get("tools", [])
    ]
    assert tool_names == list(case.expected_tools)
    messages = json.dumps(captured.get("messages", []), default=str)
    assert "canonical expert query" in messages
    assert assets.dataset_id not in messages
    assert assets.document_id not in messages
    assert "/obs/" not in messages
    assert "u1" not in messages


@pytest.mark.parametrize("case", _AUTHZ_CASES)
async def test_expert_empty_or_forced_attachment_authorization_fails(
    asset_http_context: AssetHttpTestContext,
    case: _AuthzCase,
) -> None:
    """Unsupported attachment authorization fails before routing."""
    assets = _install_expert_purpose_assets(asset_http_context)
    router_calls, agent_calls = _forbid_router_and_agent(
        asset_http_context.monkeypatch
    )
    payload: dict[str, Any] = {
        "user_query": "blocked query",
        "allowed_tools": list(case.allowed),
        "attachments": _attachments_for(assets, case.attachments),
    }
    if case.forced is not None:
        payload["forced_tool"] = case.forced
    response = await _post_expert_route(
        asset_http_context,
        assets.app,
        payload=payload,
        base_url="http://api.expert-authz.test",
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "attachment_not_supported"
    assert response.json()["error"]["stage"] == "attachment_validation"
    assert not router_calls
    assert not agent_calls
    assert not RunRegistry(asset_http_context.db_path).list_runs(owner="u1")


@pytest.mark.parametrize(
    "slug_tool",
    (("analyst", "AnalystAgent"), ("research", "InSilicoResearchAgent")),
)
async def test_expert_selected_arguments_discard_selector_paths(
    asset_http_context: AssetHttpTestContext,
    slug_tool: tuple[str, str],
) -> None:
    """Selector path maps never reach Analyst/Research after selection."""
    slug, tool_name = slug_tool
    assets = _install_expert_purpose_assets(asset_http_context)
    captured: dict[str, Any] = {}

    async def fake_invoke(**kwargs: Any) -> tuple[dict[str, Any], int]:
        captured.update(kwargs)
        return running_agent_run_body(f"expert-{slug}", slug), 202

    if slug == "research":

        async def fake_research(
            admission: ResearchHttpAdmissionInput,
            bundle: Any,
            **_kwargs: Any,
        ) -> tuple[dict[str, Any], int]:
            captured["admission"] = admission
            captured["bundle"] = bundle
            return running_agent_run_body("expert-research", "research"), 202

        asset_http_context.monkeypatch.setattr(
            agent_runs_module, "invoke_research_http_run", fake_research
        )
    else:
        asset_http_context.monkeypatch.setattr(
            api_app, "_invoke_agent_run", fake_invoke
        )
    _patch_select(
        asset_http_context.monkeypatch,
        ToolSelection(
            tool_name,
            {
                "goal_description": "selector rewrite",
                "user_query": "selector rewrite",
                "obs_file_list": ["/obs/selector-private"],
                "data_list": {"/obs/selector-private.csv": "selector value"},
            },
        ),
    )
    response = await _post_expert_route(
        asset_http_context,
        assets.app,
        payload={
            "user_query": "original expert query",
            "allowed_tools": [tool_name],
            "attachments": _attachments_for(assets, "mixed"),
        },
        base_url="http://api.expert-args.test",
    )
    assert response.status_code == 202, response.text
    if slug == "analyst":
        arguments = captured["arguments"]
        assert arguments["goal_description"] == "original expert query"
    else:
        admission = captured["admission"]
        bundle = captured["bundle"]
        assert admission.original_query == "original expert query"
        assert admission.managed_asset_ids == (
            assets.dataset_id,
            assets.document_id,
        )
        assert bundle.datasets[0].reference == assets.dataset_ref
        assert bundle.documents[0].reference == assets.document_ref
        assert "/obs/selector-private" not in str(admission)
        assert "selector rewrite" not in str(admission)
    assert "selector value" not in response.text


async def test_expert_rejects_unfiltered_router_selection(
    asset_http_context: AssetHttpTestContext,
) -> None:
    """A router cannot dispatch a zero-channel tool outside its allowlist."""
    assets = _install_expert_purpose_assets(asset_http_context)
    _patch_select(
        asset_http_context.monkeypatch,
        ToolSelection("DataAgent", {"user_query": "selector rewrite"}),
    )
    agent_calls: list[Any] = []

    async def forbid_invoke(**_kwargs: Any) -> tuple[dict[str, Any], int]:
        agent_calls.append(1)
        raise AssertionError("agent must not run")

    asset_http_context.monkeypatch.setattr(
        api_app, "_invoke_agent_run", forbid_invoke
    )
    response = await _post_expert_route(
        asset_http_context,
        assets.app,
        payload={
            "user_query": "original expert query",
            "allowed_tools": ["AnalystAgent"],
            "attachments": _attachments_for(assets, "dataset"),
        },
        base_url="http://api.expert-final-check.test",
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "attachment_not_supported"
    assert not agent_calls


async def test_expert_rejects_authorized_zero_channel_selection(
    asset_http_context: AssetHttpTestContext,
) -> None:
    """An originally allowed zero-channel tool cannot bypass prefiltering."""
    assets = _install_expert_purpose_assets(asset_http_context)
    _patch_select(
        asset_http_context.monkeypatch,
        ToolSelection("DataAgent", {"user_query": "selector rewrite"}),
    )
    agent_calls: list[Any] = []

    async def forbid_invoke(**_kwargs: Any) -> tuple[dict[str, Any], int]:
        agent_calls.append(1)
        raise AssertionError("agent must not run")

    asset_http_context.monkeypatch.setattr(
        api_app, "_invoke_agent_run", forbid_invoke
    )
    response = await _post_expert_route(
        asset_http_context,
        assets.app,
        payload={
            "user_query": "original expert query",
            "allowed_tools": ["DataAgent", "AnalystAgent"],
            "attachments": _attachments_for(assets, "dataset"),
        },
        base_url="http://api.expert-authorized-zero-channel.test",
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "attachment_not_supported"
    assert not agent_calls


async def test_expert_supported_forced_tool_stays_forced(
    asset_http_context: AssetHttpTestContext,
) -> None:
    """A supported forced tool dispatches without calling the routing model."""
    assets = _install_expert_purpose_assets(asset_http_context)
    captured: dict[str, Any] = {}
    patch_expert_router(
        asset_http_context.monkeypatch,
        expert_router,
        _router_completion(
            ("AnalystAgent", json.dumps(_selection_args("AnalystAgent")))
        ),
        captured=captured,
    )
    _install_selected_handler(asset_http_context.monkeypatch, "AnalystAgent")
    response = await _post_expert_route(
        asset_http_context,
        assets.app,
        payload={
            "user_query": "forced expert query",
            "allowed_tools": ["AnalystAgent"],
            "attachments": _attachments_for(assets, "dataset"),
            "forced_tool": "AnalystAgent",
        },
        base_url="http://api.expert-forced.test",
    )
    assert response.status_code == 202, response.text
    assert not captured


async def test_expert_rejects_stale_dataset_description_field(
    asset_http_context: AssetHttpTestContext,
) -> None:
    """Expert keeps its strict extra-field rejection policy."""
    assert ExpertQueryRequest.model_fields.get("dataset_description") is None
    response = await _post_expert_route(
        asset_http_context,
        api_app.create_app(),
        payload={
            "user_query": "original expert query",
            "allowed_tools": ["AnalystAgent"],
            "dataset_description": "supplied batch description",
        },
        base_url="http://api.expert-desc.test",
    )
    assert response.status_code == 422, response.text


def _enable_expert_context_assets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[AssetHttpTestContext, _ExpertAssets, str]:
    """Enable context V1 and install Expert assets under one owner key."""
    context, key = enable_conversation_context_v1(monkeypatch, tmp_path)
    return context, _install_expert_purpose_assets(context), key


def _patch_expert_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> list[Any]:
    """Count Expert attachment resolver calls for replay assertions."""
    calls: list[Any] = []
    original = expert_context_routes.resolve_attachment_input

    def counted(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(
        expert_context_routes, "resolve_attachment_input", counted
    )
    return calls


def _patch_expert_preparation(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    """Count Expert attachment preparation calls for replay assertions."""
    calls: list[dict[str, Any]] = []
    original = expert_context_routes.prepare_selected_expert_arguments

    def counted(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(
        expert_context_routes,
        "prepare_selected_expert_arguments",
        counted,
    )
    return calls


def _patch_expert_invocation(
    monkeypatch: pytest.MonkeyPatch,
    run_id: str,
) -> list[dict[str, Any]]:
    """Count the downstream Expert Agent boundary for replay assertions."""
    calls: list[dict[str, Any]] = []

    async def fake_research(
        admission: ResearchHttpAdmissionInput,
        bundle: Any,
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], int]:
        calls.append({"admission": admission, "bundle": bundle})
        return running_agent_run_body(run_id, "research"), 202

    monkeypatch.setattr(
        agent_runs_module, "invoke_research_http_run", fake_research
    )
    return calls


def _patch_expert_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    """Count Expert router calls and return one permitted selection."""
    calls: list[dict[str, Any]] = []

    async def select_agent(
        user_query: str,
        history: Any = (),
        *,
        allowed_tools: Any = None,
        forced_tool: Any = None,
    ) -> ToolSelection:
        calls.append(
            {
                "user_query": user_query,
                "history": list(history),
                "allowed_tools": list(allowed_tools or ()),
                "forced_tool": forced_tool,
            }
        )
        return ToolSelection(
            "InSilicoResearchAgent",
            _selection_args("InSilicoResearchAgent"),
        )

    monkeypatch.setattr(api_app, "select_agent_tool", select_agent)
    return calls


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [("operation", "replace"), ("base_business_context_version", 1)],
)
async def test_expert_context_replay_mismatch_keeps_502_and_skips_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    changed_field: str,
    changed_value: Any,
) -> None:
    """Expert replay mismatches retain the old selection failure response."""
    context, assets, key = _enable_expert_context_assets(monkeypatch, tmp_path)
    resolver_calls = _patch_expert_resolver(monkeypatch)
    invoke_calls = _patch_expert_invocation(monkeypatch, "expert-mismatch")
    request = {
        "user_query": "original expert query",
        "allowed_tools": ["InSilicoResearchAgent"],
        "attachments": _attachments_for(assets, "mixed"),
        "conversation": _expert_context_envelope(
            allowed=["InSilicoResearchAgent"],
            requested="InSilicoResearchAgent",
            request_id="expert-context-mismatch",
            content="original expert query",
        ),
    }
    mismatched = json.loads(json.dumps(request))
    mismatched["conversation"][changed_field] = changed_value

    async with open_asgi_client(
        monkeypatch, assets.app, base_url="http://api.expert-mismatch.test"
    ) as client:
        first = await client.post(
            "/v1/query/route",
            headers=_auth(key),
            json=request,
        )
        rows_after_first = _context_row_counts(context.db_path)
        retry = await client.post(
            "/v1/query/route",
            headers=_auth(key),
            json=mismatched,
        )

    _assert_selection_mismatch(first, retry)
    assert len(resolver_calls) == 1
    assert len(invoke_calls) == 1
    assert _context_row_counts(context.db_path) == rows_after_first


async def test_expert_context_exact_replay_skips_preparation_and_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An exact Expert replay skips resolver, projection, routing, and
    writes.
    """
    _context, assets, key = _enable_expert_context_assets(
        monkeypatch,
        tmp_path,
    )
    resolver_calls = _patch_expert_resolver(monkeypatch)
    preparation_calls = _patch_expert_preparation(monkeypatch)
    selection_calls = _patch_expert_selection(monkeypatch)
    invoke_calls = _patch_expert_invocation(monkeypatch, "expert-replay")
    mutation_calls = _count_context_mutations(monkeypatch)
    request = {
        "user_query": "original expert query",
        "allowed_tools": ["InSilicoResearchAgent"],
        "attachments": _attachments_for(assets, "mixed"),
        "conversation": _expert_context_envelope(
            allowed=["InSilicoResearchAgent"],
            requested=None,
            request_id="expert-context-replay",
            content="original expert query",
        ),
    }
    replay_request = json.loads(json.dumps(request))
    replay_request["attachments"] = [{"asset_id": "file_aaaaaaaaaaaaaaaa"}]

    async with open_asgi_client(
        monkeypatch, assets.app, base_url="http://api.expert-replay.test"
    ) as client:
        first = await client.post(
            "/v1/query/route",
            headers=_auth(key),
            json=request,
        )
        replay = await client.post(
            "/v1/query/route",
            headers=_auth(key),
            json=replay_request,
        )

    assert first.status_code == 202, first.text
    assert replay.status_code == 202, replay.text
    assert replay.json() == first.json()
    assert len(resolver_calls) == 1
    assert len(preparation_calls) == 1
    assert len(selection_calls) == 1
    assert len(invoke_calls) == 1
    assert mutation_calls == {
        "begin_turn": 1,
        "stage_turn": 1,
        "mark_turn_failed": 0,
    }


async def test_expert_context_dataset_authorization_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Context Expert rejects unsupported or emptied attachment allowlists."""
    context, assets, key = _enable_expert_context_assets(monkeypatch, tmp_path)
    router_calls, agent_calls = _forbid_router_and_agent(monkeypatch)
    cases = (
        {
            "allowed_tools": ["DataAgent", "BriefGeneAgent"],
            "conversation": _expert_context_envelope(
                allowed=["DataAgent", "BriefGeneAgent"],
                requested="DataAgent",
                request_id="expert-context-authz",
                content="blocked expert context",
            ),
        },
        {
            "allowed_tools": ["AnalystAgent", "ChatAgent"],
            "conversation": _expert_context_envelope(
                allowed=["DataAgent"],
                requested="DataAgent",
                request_id="expert-context-authz",
                content="blocked expert context",
            ),
        },
    )
    async with open_asgi_client(
        monkeypatch, assets.app, base_url="http://api.expert-ctx-authz.test"
    ) as client:
        for case in cases:
            response = await client.post(
                "/v1/query/route",
                headers=_auth(key),
                json={
                    "user_query": "blocked expert context",
                    "allowed_tools": case["allowed_tools"],
                    "attachments": [{"asset_id": assets.dataset_id}],
                    "conversation": case["conversation"],
                },
            )
            assert response.status_code == 422, response.text
            assert response.json()["error"]["code"] == (
                "attachment_not_supported"
            )
    assert not router_calls
    assert not agent_calls
    assert not RunRegistry(context.db_path).list_runs(owner="u1")
    assert _context_row_counts(context.db_path) == (0, 0)


async def _run_expert_context_parity(
    monkeypatch: pytest.MonkeyPatch,
    assets: _ExpertAssets,
    *,
    api_key: str,
) -> tuple[Any, Any, list[dict[str, Any]]]:
    """Drive context + ordinary + replay Expert calls for parity asserts."""
    invoke_calls: list[dict[str, Any]] = []

    async def fake_research(
        admission: ResearchHttpAdmissionInput,
        bundle: Any,
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], int]:
        invoke_calls.append({"admission": admission, "bundle": bundle})
        run_id = f"expert-ctx-{len(invoke_calls)}"
        return running_agent_run_body(run_id, "research"), 202

    monkeypatch.setattr(
        agent_runs_module, "invoke_research_http_run", fake_research
    )
    _patch_select(
        monkeypatch,
        ToolSelection(
            "InSilicoResearchAgent",
            {
                "user_query": "selector rewrite",
                "obs_file_list": ["/obs/selector-private"],
                "data_list": {"/obs/selector-private.csv": "x"},
            },
        ),
    )
    request_body = {
        "user_query": "original expert query",
        "allowed_tools": [
            "AnalystAgent",
            "InSilicoResearchAgent",
            "DigitalDesignAgent",
            "ChatAgent",
        ],
        "attachments": _attachments_for(assets, "mixed"),
        "conversation": _expert_context_envelope(
            allowed=[
                "DigitalDesignAgent",
                "InSilicoResearchAgent",
                "AnalystAgent",
            ],
            requested="InSilicoResearchAgent",
            request_id="expert-context-parity",
            content="original expert query",
        ),
    }
    async with open_asgi_client(
        monkeypatch, assets.app, base_url="http://api.expert-ctx-parity.test"
    ) as client:
        response = await client.post(
            "/v1/query/route",
            headers={
                **_auth(api_key),
                "Idempotency-Key": "expert-context-alias-key",
            },
            json=request_body,
        )
        ordinary = await client.post(
            "/v1/query/route",
            headers=_auth(api_key),
            json={
                "user_query": "original expert query",
                "allowed_tools": [
                    "AnalystAgent",
                    "InSilicoResearchAgent",
                ],
                "attachments": _attachments_for(assets, "mixed"),
                "forced_tool": "InSilicoResearchAgent",
            },
        )
        replay = await client.post(
            "/v1/query/route",
            headers={
                **_auth(api_key),
                "Idempotency-Key": "expert-context-alias-key",
            },
            json=request_body,
        )
    assert response.status_code == 202, response.text
    assert ordinary.status_code == 202, ordinary.text
    assert replay.status_code == 202, replay.text
    return response, replay, invoke_calls


async def test_expert_context_preserves_payload_order_and_parity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Context intersection keeps payload order and matches ordinary prep."""
    _context, assets, key = _enable_expert_context_assets(
        monkeypatch, tmp_path
    )
    response, replay, invoke_calls = await _run_expert_context_parity(
        monkeypatch, assets, api_key=key
    )
    assert len(invoke_calls) == 2
    assert invoke_calls[0]["admission"].idempotency_key == (
        "expert-context-alias-key"
    )
    for call in invoke_calls:
        _assert_research_attachment_call(
            call,
            document_ref=assets.document_ref,
            dataset_ref=assets.dataset_ref,
        )
    stage = response.json().get("conversation_context")
    assert stage is not None
    dumped = json.dumps(stage)
    assert assets.dataset_id not in dumped
    assert assets.document_id not in dumped
    assert assets.dataset_ref not in dumped
    assert "data_list" not in dumped
    assert "obs_file_list" not in dumped
    assert replay.json().get("conversation_context") == stage
