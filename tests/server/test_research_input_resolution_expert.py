# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""RED contracts for converging Expert Research on route preflight."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from tests.server.test_query_route import (
    ToolSelection,
    _auth,
    _stub_tool_handler,
    api_app,
)
from tests.support.http_fakes import (
    build_instant_chat_context_envelope,
    open_asgi_client,
    running_agent_run_body,
)
from tests.support.resumable_asset_fakes import enable_conversation_context_v1

import mcp_server_phytomni.api.agent_runs as agent_runs_module
import mcp_server_phytomni.api.research_input as research_input_module
from mcp_server_phytomni.agents.research.input_contracts import (
    ResearchCoordinatorDependencies,
    ResearchCoordinatorRequest,
)
from mcp_server_phytomni.agents.research.input_coordinator import (
    ResearchInputCoordinator,
)
from mcp_server_phytomni.agents.research.input_parser import (
    parse_research_input,
)
from mcp_server_phytomni.agents.research.input_preparation import (
    PreparedResearchInput,
)
from mcp_server_phytomni.api.research_input import (
    ResearchAdmissionOutcome,
    ResearchHttpAdmissionInput,
    ResearchInputStore,
    ResearchRoutePreflight,
)
from mcp_server_phytomni.api.routes import (
    expert_context as expert_context_routes,
)
from mcp_server_phytomni.api.routes.attachment_inputs import (
    ResolvedAttachmentInput,
    admit_selected_research,
    restrict_expert_candidates_for_research,
)
from mcp_server_phytomni.api.upload_runtime import UploadRuntime
from mcp_server_phytomni.runtime.attachment_assets import (
    ResolvedAsset,
    ResolvedAttachmentBundle,
)
from mcp_server_phytomni.runtime.conversation_context.models import (
    ConversationEnvelopeV1,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry

pytestmark = pytest.mark.server


@pytest.fixture(autouse=True)
async def _publish_outbound_runtime(outbound_runtime: Any) -> None:
    """Keep direct ASGITransport requests inside runtime ownership."""
    del outbound_runtime


@dataclass
class _CapturingPreflight:
    """Record the sole request allowed to reach Research admission."""

    requests: list[ResearchHttpAdmissionInput] = field(default_factory=list)

    async def admit(
        self, request: ResearchHttpAdmissionInput
    ) -> ResearchAdmissionOutcome:
        """Capture the immutable request passed to the preflight seam."""
        self.requests.append(request)
        return ResearchAdmissionOutcome("research-run", False, True, 202)


def _original_request(
    *, conversation: object | None = None
) -> ResearchHttpAdmissionInput:
    """Build caller-owned Research inputs without selector-provided paths."""
    return ResearchHttpAdmissionInput(
        owner="owner-1",
        idempotency_key="expert-research-key",
        conversation=conversation,
        original_query='data: {"managed://asset-1": ""}',
        managed_asset_ids=("asset-1",),
        locale="en-US",
        interop_mode="off",
        interop_targets=(),
        route_source="expert",
    )


def _expert_context_request(
    allowed: list[str],
    requested: str,
    request_id: str,
    content: str,
    *,
    options: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build the public Expert context request for one authority check."""
    options = options or {}
    envelope = build_instant_chat_context_envelope("21")
    conversation = {
        **envelope,
        "mode": "expert",
        "request_id": request_id,
        "requested_agent_id": requested,
        "allowed_agent_ids": allowed,
        "current_message": {
            "content": content,
            "locale": options.get("locale", "en-US"),
        },
    }

    request: dict[str, Any] = {
        "user_query": "untrusted payload query",
        "allowed_tools": allowed,
        "conversation": conversation,
    }
    if forced_tool := options.get("forced_tool"):
        request["forced_tool"] = forced_tool
    return request


@dataclass(frozen=True)
class _HttpCountCase:
    """One combined Research count boundary and its operator limits."""

    total_count: int
    managed_count: int
    pasted_count: int
    managed_limit: int
    pasted_limit: int
    combined_limit: int
    accepted: bool


_HTTP_COUNT_CASES = (
    _HttpCountCase(11, 10, 1, 64, 64, 128, True),
    _HttpCountCase(64, 63, 1, 64, 64, 128, True),
    _HttpCountCase(65, 64, 1, 64, 64, 128, True),
    _HttpCountCase(128, 64, 64, 64, 64, 128, True),
    _HttpCountCase(129, 64, 65, 64, 64, 128, False),
    _HttpCountCase(256, 128, 128, 128, 129, 256, True),
    _HttpCountCase(257, 128, 129, 128, 129, 256, False),
)


def _synthetic_bundle(
    asset_ids: tuple[str, ...],
) -> ResolvedAttachmentBundle:
    """Build completed managed datasets without upload or OBS I/O."""
    return ResolvedAttachmentBundle(
        assets=tuple(
            ResolvedAsset(
                asset_id,
                f"obs://phytomni/managed/{index:03d}.tsv",
                f"{index:03d}.tsv",
                "text/tab-separated-values",
                1,
                "dataset",
                1,
                "2026-08-09T00:00:00+00:00",
            )
            for index, asset_id in enumerate(asset_ids)
        )
    )


def _resolve_synthetic_bundle(
    attachments: list[dict[str, Any]], _owner: str
) -> ResolvedAttachmentBundle:
    """Resolve opaque IDs to one deterministic in-memory bundle."""
    raw_asset_ids = tuple(
        item.get("asset_id")
        for item in attachments
        if isinstance(item, Mapping)
    )
    if any(not isinstance(asset_id, str) for asset_id in raw_asset_ids):
        raise AssertionError("synthetic attachment IDs must be strings")
    return _synthetic_bundle(
        tuple(cast(str, asset_id) for asset_id in raw_asset_ids)
    )


def _resolve_count_context_input(
    attachments: Any,
    *,
    attachment_owner: str,
    resolver: Any,
) -> ResolvedAttachmentInput:
    """Keep Expert context preparation on the synthetic bundle."""
    del resolver
    asset_ids = tuple(
        item.asset_id if hasattr(item, "asset_id") else item["asset_id"]
        for item in attachments
    )
    return ResolvedAttachmentInput(
        attachment_owner,
        _synthetic_bundle(asset_ids),
    )


def _count_query(grammar: str, pasted_count: int) -> str:
    """Build one strict grammar containing exactly ``pasted_count`` paths."""
    values = {
        f"obs://phytomni/pasted/{index:03d}.tsv": f"path-{index}"
        for index in range(pasted_count)
    }
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    if grammar == "trailing_json":
        return f"Study drought response.\ndata: {encoded}"
    if grammar == "fenced_json":
        return f"Study drought response.\ndata:\n```json\n{encoded}\n```"
    if grammar == "standalone_tab":
        lines = "\n".join(
            f"{reference}\t{hint}" for reference, hint in values.items()
        )
        return f"Study drought response.\n{lines}"
    raise AssertionError(f"unknown Research input grammar: {grammar}")


def _count_attachments(count: int) -> list[dict[str, str]]:
    """Return opaque managed IDs for one exact HTTP request count."""
    return [{"asset_id": f"managed-{index:03d}"} for index in range(count)]


async def _post_count_form(
    client: Any,
    path: str,
    headers: dict[str, str],
    key: str,
    body: dict[str, Any],
) -> Any:
    """Post one count-matrix form with one isolated idempotency key."""
    return await client.post(
        path,
        headers={**headers, "Idempotency-Key": key},
        json=body,
    )


async def _post_count_http_forms(
    client: Any,
    headers: dict[str, str],
    request: dict[str, Any],
) -> dict[str, Any]:
    """Post one exact query through all five Research HTTP forms."""
    query = request["query"]
    attachments = request["attachments"]
    prefix = request["key_prefix"]
    native = {"arguments": {"user_query": query}, "attachments": attachments}
    expert = {
        "user_query": query,
        "allowed_tools": ["InSilicoResearchAgent"],
        "attachments": attachments,
    }
    forms = {
        "native": ("/v1/agents/research/runs", native),
        "dedicated": (
            "/v1/agents/research/runs",
            {**native, "conversation": request["dedicated_conversation"]},
        ),
        "explicit": (
            "/v1/query/route",
            {**expert, "forced_tool": "InSilicoResearchAgent"},
        ),
        "autonomous": ("/v1/query/route", expert),
        "conversation": (
            "/v1/query/route",
            {
                **expert,
                "user_query": "untrusted legacy query",
                "conversation": request["conversation"],
            },
        ),
    }
    return {
        name: await _post_count_form(
            client, path, headers, f"{prefix}-{name}", body
        )
        for name, (path, body) in forms.items()
    }


def _install_count_http_seams(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: _HttpCountCase,
) -> tuple[str, _ResearchAdmissionCapture]:
    """Install bounded runtime seams for one synthetic HTTP case."""
    _, key = enable_conversation_context_v1(monkeypatch, tmp_path)
    for name, value in (
        ("API_MAX_ATTACHMENTS_PER_REQUEST", case.managed_limit),
        ("API_MAX_RESEARCH_DATASET_PATHS", case.pasted_limit),
        ("API_MAX_RESEARCH_INPUT_REFERENCES", case.combined_limit),
    ):
        monkeypatch.setenv(name, str(value))
        monkeypatch.setenv(f"PHYTOMNI_{name}", str(value))
    resolver = SimpleNamespace(resolve_bundle=_resolve_synthetic_bundle)
    monkeypatch.setattr(
        UploadRuntime, "get_asset_resolver", lambda _runtime: resolver
    )
    monkeypatch.setattr(
        agent_runs_module,
        "_resolve_attachment_bundle",
        lambda _source, asset_ids: _synthetic_bundle(asset_ids),
    )
    monkeypatch.setattr(
        expert_context_routes,
        "resolve_attachment_input",
        _resolve_count_context_input,
    )
    capture = _ResearchAdmissionCapture()

    async def validate_inventory(*_args: Any) -> None:
        return None

    monkeypatch.setattr(
        agent_runs_module, "launch_research_input_worker", capture
    )
    monkeypatch.setattr(
        agent_runs_module,
        "_research_inventory_validator",
        lambda _config: validate_inventory,
    )
    monkeypatch.setattr(
        agent_runs_module, "research_input_root_worker_ready", lambda: True
    )
    monkeypatch.setattr(
        agent_runs_module,
        "research_input_runtime_capability",
        lambda *_args: type("Capability", (), {"ready": True})(),
    )
    monkeypatch.setattr(api_app, "select_agent_tool", _select_research)
    return key, capture


@pytest.mark.parametrize(
    "grammar", ("trailing_json", "fenced_json", "standalone_tab")
)
@pytest.mark.parametrize(
    "case", _HTTP_COUNT_CASES, ids=lambda case: str(case.total_count)
)
async def test_http_count_boundaries_reach_research_preflight_for_all_forms(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    grammar: str,
    case: _HttpCountCase,
) -> None:
    """All HTTP forms share managed/pasted count and hard-limit behavior."""
    key, capture = _install_count_http_seams(monkeypatch, tmp_path, case)
    query = _count_query(grammar, case.pasted_count)
    attachments = _count_attachments(case.managed_count)
    dedicated = _matrix_conversation(query, "23")
    conversation = _matrix_conversation(query, "24")
    app = api_app.create_app()
    async with open_asgi_client(
        monkeypatch, app, base_url="http://api.research-count.test"
    ) as client:
        responses = await _post_count_http_forms(
            client,
            _auth(key),
            {
                "query": query,
                "attachments": attachments,
                "dedicated_conversation": dedicated,
                "conversation": conversation,
                "key_prefix": f"count-{case.total_count}-{grammar}",
            },
        )

    if case.accepted:
        assert {response.status_code for response in responses.values()} == {
            202
        }
        assert len(capture.admissions) == 5
        assert all(
            len(admission.managed_asset_ids) == case.managed_count
            for admission in capture.admissions
        )
        parsed = parse_research_input(query, "phytomni")
        assert len(parsed.candidates) == case.pasted_count
        assert len(parsed.candidates) + case.managed_count == case.total_count
        assert {span.grammar for span in parsed.removed_spans} == {grammar}
        return

    assert not capture.admissions
    assert {response.status_code for response in responses.values()} == {413}
    assert {
        response.json()["error"]["code"] for response in responses.values()
    } == {"research_input_limit_exceeded"}


def _research_alias(db_path: str, run_id: str) -> str | None:
    """Read the persisted opaque alias only for this HTTP contract check."""
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT alias_digest FROM research_idempotency_bindings "
            "WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    return None if row is None else cast(str | None, row[0])


def test_research_syntax_probe_only_narrows_a_positive_candidate_set() -> None:
    """The pre-selector probe has no authority beyond ordered candidates."""
    allowed = ("KnowledgeAgent", "InSilicoResearchAgent", "ChatAgent")

    assert restrict_expert_candidates_for_research(
        'data: {"managed://asset-1": ""}', "dev-bucket", allowed
    ) == ("InSilicoResearchAgent",)
    assert (
        restrict_expert_candidates_for_research(
            "explain photosynthesis", "dev-bucket", allowed
        )
        == allowed
    )
    assert (
        restrict_expert_candidates_for_research(
            'data: {"managed://asset-1": ""}',
            "dev-bucket",
            allowed,
            "DataAgent",
        )
        == allowed
    )


@pytest.mark.parametrize("form", ("forced", "autonomous", "conversation"))
async def test_selected_research_discards_selector_path_authority(
    form: str,
) -> None:
    """Every Expert form admits only immutable original caller inputs."""
    conversation = object() if form == "conversation" else None
    original = _original_request(conversation=conversation)
    preflight = _CapturingPreflight()
    selector_arguments: dict[str, Any] = {
        "user_query": "changed selector query",
        "data_list": {"obs://evil-bucket/x.csv": "invented"},
        "obs_file_list": ["obs://evil-bucket/paper.pdf"],
        "attachments": [{"path": "/tmp/evil"}],
    }

    outcome = await admit_selected_research(
        original,
        "InSilicoResearchAgent",
        selector_arguments,
        cast(ResearchRoutePreflight, preflight),
    )

    assert outcome == ResearchAdmissionOutcome(
        "research-run", False, True, 202
    )
    assert preflight.requests == [original]
    assert preflight.requests[0].original_query == original.original_query
    assert preflight.requests[0].managed_asset_ids == ("asset-1",)


async def test_context_requested_data_keeps_its_candidate_over_syntax_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An explicit Data turn must not be narrowed to Research by syntax."""
    _context, key = enable_conversation_context_v1(monkeypatch, tmp_path)

    async def forbid_select(*_args: Any, **_kwargs: Any) -> ToolSelection:
        raise AssertionError("explicit context selection must bypass routing")

    monkeypatch.setattr(api_app, "select_agent_tool", forbid_select)
    _stub_tool_handler(monkeypatch, "DataAgent", {"answer": "ok"})
    request = _expert_context_request(
        ["DataAgent", "InSilicoResearchAgent"],
        "DataAgent",
        "expert-context-explicit-data",
        'data: {"managed://dataset": ""}',
        options={"forced_tool": "InSilicoResearchAgent"},
    )

    async with open_asgi_client(
        monkeypatch, api_app.create_app(), base_url="http://api.context.test"
    ) as client:
        response = await client.post(
            "/v1/query/route", headers=_auth(key), json=request
        )

    assert response.status_code == 200, response.text


async def test_context_research_admission_uses_current_message_locale(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A Research context admission owns the envelope locale, not en-US."""
    _context, key = enable_conversation_context_v1(monkeypatch, tmp_path)
    captured: list[ResearchHttpAdmissionInput] = []

    async def fake_research(
        admission: ResearchHttpAdmissionInput,
        _bundle: Any,
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], int]:
        captured.append(admission)
        return running_agent_run_body("context-research", "research"), 202

    monkeypatch.setattr(
        agent_runs_module, "invoke_research_http_run", fake_research
    )
    request = _expert_context_request(
        ["InSilicoResearchAgent"],
        "InSilicoResearchAgent",
        "expert-context-zh-locale",
        'data: {"managed://dataset": ""}',
        options={"locale": "zh-CN"},
    )

    async with open_asgi_client(
        monkeypatch, api_app.create_app(), base_url="http://api.context.test"
    ) as client:
        response = await client.post(
            "/v1/query/route", headers=_auth(key), json=request
        )

    assert response.status_code == 202, response.text
    assert captured[0].locale == "zh-CN"


async def test_context_replay_attaches_alias_without_input_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A header alias binds an existing Research turn without replay I/O."""
    context, key = enable_conversation_context_v1(monkeypatch, tmp_path)
    request = _expert_context_request(
        ["InSilicoResearchAgent"],
        "InSilicoResearchAgent",
        "expert-context-alias-attach",
        "summarize rice drought literature",
    )
    app = api_app.create_app()

    async with open_asgi_client(
        monkeypatch, app, base_url="http://api.context-alias.test"
    ) as client:
        first = await client.post(
            "/v1/query/route", headers=_auth(key), json=request
        )

        def forbid_input_resolution(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("context replay must not resolve attachments")

        async def forbid_selector(
            *_args: Any, **_kwargs: Any
        ) -> ToolSelection:
            raise AssertionError("context replay must not select an agent")

        async def forbid_research(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("context replay must not invoke Research")

        monkeypatch.setattr(
            expert_context_routes,
            "resolve_attachment_input",
            forbid_input_resolution,
        )
        monkeypatch.setattr(api_app, "select_agent_tool", forbid_selector)
        monkeypatch.setattr(
            research_input_module,
            "parse_research_input",
            forbid_input_resolution,
        )
        monkeypatch.setattr(
            agent_runs_module, "invoke_research_http_run", forbid_research
        )
        replay = await client.post(
            "/v1/query/route",
            headers={**_auth(key), "Idempotency-Key": "context-alias-1"},
            json=request,
        )

    assert first.status_code == 202, first.text
    assert replay.status_code == 202, replay.text
    assert _research_alias(context.db_path, first.json()["run_id"]) is not None


async def test_context_replay_rejects_alias_bound_to_another_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A replay cannot steal an alias atomically bound by another turn."""
    context, key = enable_conversation_context_v1(monkeypatch, tmp_path)
    request = _expert_context_request(
        ["InSilicoResearchAgent"],
        "InSilicoResearchAgent",
        "expert-context-alias-conflict",
        "summarize rice drought literature",
    )
    envelope = ConversationEnvelopeV1.model_validate(request["conversation"])
    other_turn = envelope.model_copy(
        update={
            "conversation_key": uuid4(),
            "turn_id": "other-turn",
            "request_id": "other-request",
        }
    )
    RunRegistry(context.db_path)
    preflight = ResearchRoutePreflight(
        store=ResearchInputStore(context.db_path), worker_launcher=None
    )
    await preflight.admit(
        ResearchHttpAdmissionInput(
            owner="u1",
            idempotency_key="occupied-context-alias",
            conversation=other_turn,
            original_query="summarize rice drought literature",
            managed_asset_ids=(),
            locale="en-US",
            interop_mode="off",
            interop_targets=(),
            route_source="expert",
        )
    )

    async with open_asgi_client(
        monkeypatch,
        api_app.create_app(),
        base_url="http://api.context-conflict.test",
    ) as client:
        first = await client.post(
            "/v1/query/route", headers=_auth(key), json=request
        )
        replay = await client.post(
            "/v1/query/route",
            headers={
                **_auth(key),
                "Idempotency-Key": "occupied-context-alias",
            },
            json=request,
        )

    assert first.status_code == 202, first.text
    assert replay.status_code == 409, replay.text
    assert replay.json()["error"]["code"] == "research_idempotency_conflict"


async def test_context_replay_rejects_malformed_alias_without_input_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A malformed alias is rejected before any context replay I/O."""
    _context, key = enable_conversation_context_v1(monkeypatch, tmp_path)
    request = _expert_context_request(
        ["InSilicoResearchAgent"],
        "InSilicoResearchAgent",
        "expert-context-alias-invalid",
        "summarize rice drought literature",
    )
    app = api_app.create_app()

    async with open_asgi_client(
        monkeypatch, app, base_url="http://api.context-invalid.test"
    ) as client:
        first = await client.post(
            "/v1/query/route", headers=_auth(key), json=request
        )

        def forbid_input_resolution(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError(
                "malformed replay must not resolve attachments"
            )

        monkeypatch.setattr(
            expert_context_routes,
            "resolve_attachment_input",
            forbid_input_resolution,
        )
        replay = await client.post(
            "/v1/query/route",
            headers={**_auth(key), "Idempotency-Key": ""},
            json=request,
        )

    assert first.status_code == 202, first.text
    assert replay.status_code == 400, replay.text
    assert (
        replay.json()["error"]["code"] == "research_idempotency_key_required"
    )


_HTTP_RESEARCH_INPUT_FORMS = (
    pytest.param(
        "trailing_json",
        "Study drought response.\n"
        'data: {"obs://phytomni/input.tsv": "expression matrix"}',
        id="trailing-json",
    ),
    pytest.param(
        "fenced_json",
        "Study drought response.\n"
        "data:\n"
        "```json\n"
        '{"obs://phytomni/input.tsv": "expression matrix"}\n'
        "```",
        id="fenced-json",
    ),
    pytest.param(
        "standalone_tab",
        "Study drought response.\n"
        "obs://phytomni/input.tsv\texpression matrix",
        id="standalone-tab",
    ),
)


def _matrix_conversation(content: str, turn_id: str) -> dict[str, Any]:
    """Build one shared authoritative Expert context envelope."""
    envelope = build_instant_chat_context_envelope(turn_id)
    envelope.update(
        {
            "mode": "expert",
            "request_id": "matrix-request",
            "requested_agent_id": "InSilicoResearchAgent",
            "allowed_agent_ids": ["InSilicoResearchAgent"],
            "current_message": {"content": content, "locale": "en-US"},
            "history_delta": [
                {
                    "turn_id": turn_id,
                    "role": "user",
                    "content": content,
                }
            ],
        }
    )
    return envelope


@dataclass
class _ResearchAdmissionCapture:
    """Run the real coordinator root with deterministic typed dependencies."""

    admissions: list[ResearchHttpAdmissionInput] = field(default_factory=list)
    prepared: list[PreparedResearchInput] = field(default_factory=list)

    async def __call__(
        self, _request: Any, outcome: Any, admission: Any
    ) -> bool:
        """Run a coordinator root after HTTP preflight owns a run."""
        self.admissions.append(admission)
        inventory = object()

        async def metadata(_context: Any) -> Any:
            return inventory

        async def extract(_context: Any) -> Any:
            return object()

        async def resolve(_context: Any) -> Any:
            return object()

        async def revalidate(_context: Any) -> Any:
            return inventory

        def join(_inventory: Any, _resolution: Any) -> PreparedResearchInput:
            return PreparedResearchInput(
                effective_query=admission.parsed_input.effective_query,
                obs_file_list=(),
                data_list=MappingProxyType({"opaque-dataset": "dataset"}),
                inventory_digest="matrix-inventory",
                evidence_digest="matrix-evidence",
                execution_fingerprint=admission.client_fingerprint,
                authority_ids=(),
            )

        def validate_native(
            value: PreparedResearchInput,
        ) -> PreparedResearchInput:
            self.prepared.append(value)
            return value

        async def persist(_run_id: str, **_values: Any) -> None:
            return None

        coordinator = ResearchInputCoordinator(
            ResearchCoordinatorRequest(
                run_id=outcome.run_id,
                inventory_request=inventory,
                evidence=object(),
                resolution=object(),
                dependencies=ResearchCoordinatorDependencies(
                    build_inventory=metadata,
                    extract_evidence=extract,
                    resolve_descriptions=resolve,
                    validate_native=validate_native,
                    revalidate_inventory=revalidate,
                    persist_planning=persist,
                    join_prepared=join,
                ),
            )
        )
        await coordinator.run(outcome.run_id, "matrix-coordinator")
        return True


async def _select_research(
    *_args: Any,
    **_kwargs: Any,
) -> ToolSelection:
    """Choose Research while leaving caller-owned inputs untouched."""
    return ToolSelection(
        "InSilicoResearchAgent",
        {"user_query": "selector rewrite", "data_list": {}},
    )


async def _post_research_http_matrix(
    client: Any,
    headers: dict[str, str],
    query: str,
    dedicated_conversation: dict[str, Any],
    conversation: dict[str, Any],
) -> dict[str, Any]:
    """Post the five public HTTP entry forms for one exact caller input."""
    return {
        "native": await client.post(
            "/v1/agents/research/runs",
            headers={**headers, "Idempotency-Key": "matrix-native"},
            json={"arguments": {"user_query": query}},
        ),
        "dedicated": await client.post(
            "/v1/agents/research/runs",
            headers={**headers, "Idempotency-Key": "matrix-dedicated"},
            json={
                "arguments": {"user_query": query},
                "conversation": dedicated_conversation,
            },
        ),
        "explicit": await client.post(
            "/v1/query/route",
            headers={**headers, "Idempotency-Key": "matrix-explicit"},
            json={
                "user_query": query,
                "allowed_tools": ["InSilicoResearchAgent"],
                "forced_tool": "InSilicoResearchAgent",
            },
        ),
        "autonomous": await client.post(
            "/v1/query/route",
            headers={**headers, "Idempotency-Key": "matrix-autonomous"},
            json={
                "user_query": query,
                "allowed_tools": ["InSilicoResearchAgent"],
            },
        ),
        "conversation": await client.post(
            "/v1/query/route",
            headers={**headers, "Idempotency-Key": "matrix-conversation"},
            json={
                "user_query": "untrusted legacy query",
                "allowed_tools": ["InSilicoResearchAgent"],
                "conversation": conversation,
            },
        ),
    }


async def _capture_five_research_http_forms(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    query: str,
) -> _ResearchAdmissionCapture:
    """Exercise every HTTP ingress through its Research admission seam."""
    _context, key = enable_conversation_context_v1(monkeypatch, tmp_path)
    capture = _ResearchAdmissionCapture()

    async def validate_inventory(*_args: Any) -> None:
        return None

    monkeypatch.setattr(
        agent_runs_module, "launch_research_input_worker", capture
    )
    monkeypatch.setattr(
        agent_runs_module,
        "_research_inventory_validator",
        lambda _config: validate_inventory,
    )
    monkeypatch.setattr(
        agent_runs_module, "research_input_root_worker_ready", lambda: True
    )
    monkeypatch.setattr(
        agent_runs_module,
        "research_input_runtime_capability",
        lambda *_args: type("Capability", (), {"ready": True})(),
    )
    monkeypatch.setattr(api_app, "select_agent_tool", _select_research)
    dedicated_conversation = _matrix_conversation(query, "23")
    conversation = _matrix_conversation(query, "24")
    app = api_app.create_app()

    async with open_asgi_client(
        monkeypatch, app, base_url="http://api.research-matrix.test"
    ) as client:
        responses = await _post_research_http_matrix(
            client,
            _auth(key),
            query,
            dedicated_conversation,
            conversation,
        )

    for form, response in responses.items():
        assert response.status_code == 202, f"{form}: {response.text}"
    return capture


@pytest.mark.parametrize(("grammar", "query"), _HTTP_RESEARCH_INPUT_FORMS)
async def test_five_http_forms_preserve_one_research_admission_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    grammar: str,
    query: str,
) -> None:
    """HTTP routing must not rewrite pasted inputs before Research admission.

    This fails if a route takes selector-owned arguments, drops the original
    query, or includes transport-only route source in the caller fingerprint.
    """
    capture = await _capture_five_research_http_forms(
        monkeypatch, tmp_path, query
    )
    admissions = capture.admissions

    assert len(admissions) == 5
    assert [admission.route_source for admission in admissions] == [
        "native",
        "dedicated_web",
        "expert",
        "expert",
        "expert",
    ]
    assert [
        (
            admission.original_query,
            admission.managed_asset_ids,
            admission.locale,
            admission.interop_mode,
            admission.interop_targets,
        )
        for admission in admissions
    ] == [(query, (), "en-US", "off", ())] * 5
    parsed = parse_research_input(query, "phytomni")
    assert [span.grammar for span in parsed.removed_spans] == [grammar]
    assert [candidate.exact_reference for candidate in parsed.candidates] == [
        "obs://phytomni/input.tsv"
    ]
    assert [candidate.user_hint for candidate in parsed.candidates] == [
        "expression matrix"
    ]

    fingerprints = [admission.client_fingerprint for admission in admissions]
    assert fingerprints[0] == fingerprints[2] == fingerprints[3]
    assert fingerprints[1] != fingerprints[4]
    assert fingerprints[0] != fingerprints[1]
    assert len(capture.prepared) == 5
    assert {
        (
            prepared.effective_query,
            prepared.inventory_digest,
            prepared.execution_fingerprint,
            prepared.authority_ids,
        )
        for prepared in capture.prepared
    } == {
        (
            parsed.effective_query,
            "matrix-inventory",
            capture.prepared[0].execution_fingerprint,
            (),
        )
    }
