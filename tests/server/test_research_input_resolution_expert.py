# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""RED contracts for converging Expert Research on route preflight."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
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
    admit_selected_research,
    restrict_expert_candidates_for_research,
)
from mcp_server_phytomni.runtime.conversation_context.models import (
    ConversationEnvelopeV1,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry

pytestmark = pytest.mark.server


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
