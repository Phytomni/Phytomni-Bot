# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Focused edge coverage for release-branch lifecycle contracts."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI, HTTPException
from tests.support.http_fakes import build_conversation_context_envelope

from mcp_server_phytomni.api import a2ui_resume, execution_dispatch, stream_log
from mcp_server_phytomni.api.lifecycle_contract import SafeApiError
from mcp_server_phytomni.runtime.async_iterator_v2 import (
    close_async_iterator,
)
from mcp_server_phytomni.runtime.execution_events import (
    ExecutionEventIntent,
    PublicEventSummary,
    PublicPhaseProgressPayload,
)
from mcp_server_phytomni.runtime.execution_instrumentation_v2 import (
    bind_execution_boundary,
)
from mcp_server_phytomni.runtime.execution_provider_join_store_v2 import (
    ProviderJoinLeaseRepositoryMixin,
)
from mcp_server_phytomni.runtime.legacy_event_adapter_v2 import (
    _public_payload,
    adapt_agui_custom_to_v2,
    adapt_legacy_event_intent_to_v2,
)
from mcp_server_phytomni.runtime.resume import NoCheckpointError


class _JoinRepository(ProviderJoinLeaseRepositoryMixin):
    """Validation-only repository without opening a database."""

    db_path = "unused"
    _clock = staticmethod(datetime.now)


@pytest.mark.asyncio
async def test_stream_log_replays_tails_and_expires() -> None:
    """Replay buffered frames, wake a follower, and expire the binding."""
    run_id = "coverage-stream"
    stream_log.drop_run_stream_log(run_id)
    log = stream_log.RunStreamLog()
    stream_log.bind_run_stream_log(run_id, log)
    log.append("first")

    async def collect() -> list[str]:
        return [frame async for frame in log.follow(0)]

    follower = asyncio.create_task(collect())
    await asyncio.sleep(0)
    log.append("second")
    log.close()
    assert await follower == ["first", "second"]
    replay = stream_log.iter_run_stream(run_id, after=1)
    assert replay is not None
    assert [frame async for frame in replay] == ["second"]

    stream_log.schedule_run_stream_log_drop(run_id, delay=0)
    await asyncio.sleep(0.01)
    assert stream_log.iter_run_stream(run_id) is None
    stream_log.drop_run_stream_log(run_id)


@pytest.mark.asyncio
async def test_close_async_iterator_handles_missing_and_async_closers() -> (
    None
):
    """Skip objects without aclose and await supported close hooks."""
    await close_async_iterator(SimpleNamespace())
    state = SimpleNamespace(closed=False)

    async def aclose() -> None:
        """Record asynchronous closure."""
        state.closed = True

    iterator = SimpleNamespace(aclose=aclose)
    await close_async_iterator(iterator)
    assert state.closed is True


def test_legacy_adapters_fail_closed_without_execution_boundary() -> None:
    """Reject private/invalid custom values without a bound runtime."""
    assert not adapt_agui_custom_to_v2("unknown", {})
    assert not adapt_agui_custom_to_v2("phyto.progress", [])
    assert not adapt_agui_custom_to_v2(
        "phyto.progress", {"phase": "x", "completed": -1, "total": 1}
    )
    assert not adapt_agui_custom_to_v2(
        "phyto.progress", {"phase": "x", "completed": 1, "total": 1}
    )
    assert not adapt_agui_custom_to_v2("phyto.reasoning_summary", {"text": ""})
    assert not adapt_agui_custom_to_v2(
        "phyto.decision_note", {"text": "x" * 513}
    )
    assert not adapt_agui_custom_to_v2(
        "phyto.progress", {"phase": "x", "current": 1, "total": 2}
    )
    intent = ExecutionEventIntent(
        kind="phase.started",
        status="running",
        summary=PublicEventSummary(key="phase", text="Phase"),
        payload=PublicPhaseProgressPayload(
            phase="phase", completed=1, total=2
        ),
    )
    assert not adapt_legacy_event_intent_to_v2(intent)
    assert _public_payload("phase.started", {"phase": "phase"}) == {
        "phase": "phase"
    }
    assert _public_payload("phase.failed", {}) == {
        "code": "phase_failed",
        "retryable": False,
    }
    assert _public_payload("todo.snapshot", {"items": []}) == {"items": []}
    assert _public_payload("reasoning.summary", {"text": "summary"}) == {
        "text": "summary"
    }
    assert _public_payload("phase.progress", {"phase": "x"}) is None


def test_legacy_adapters_publish_only_inside_a_bound_boundary() -> None:
    """Publish valid legacy events through the explicit journal seam."""
    appended: list[object] = []
    context = SimpleNamespace(
        execution_id="execution",
        owner_ref="owner",
        current_span_id="span",
        parent_span_id=None,
    )
    services = SimpleNamespace(
        journal=SimpleNamespace(
            append=lambda *_args, **kwargs: appended.append(kwargs["intent"])
        )
    )
    intent = ExecutionEventIntent(
        kind="phase.progress",
        status="running",
        summary=PublicEventSummary(key="phase", text="Phase"),
        payload=PublicPhaseProgressPayload(
            phase="phase", completed=1, total=2
        ),
    )
    with bind_execution_boundary(cast(Any, context), cast(Any, services)):
        assert adapt_legacy_event_intent_to_v2(intent) is True
        assert (
            adapt_agui_custom_to_v2(
                "phyto.progress",
                {"phase": "phase", "completed": 1, "total": 2},
            )
            is True
        )
        assert (
            adapt_agui_custom_to_v2(
                "phyto.reasoning_summary", {"text": "Reasoning"}
            )
            is True
        )
    assert len(appended) == 3


def test_provider_join_validation_fails_closed() -> None:
    """Reject invalid lease bounds before touching the work database."""
    repository = _JoinRepository()
    with pytest.raises(ValueError, match="limit"):
        repository.list_ready_provider_joins(limit=0)
    with pytest.raises(ValueError, match="limit"):
        repository.list_ready_provider_joins(limit=1001)
    with pytest.raises(ValueError, match="lease_token"):
        repository.claim_provider_join_lease(
            "execution", owner="owner", lease_token="", lease_seconds=1
        )
    with pytest.raises(ValueError, match="lease_seconds"):
        repository.claim_provider_join_lease(
            "execution", owner="owner", lease_token="token", lease_seconds=0
        )
    with pytest.raises(ValueError, match="lease_token"):
        repository.renew_provider_join_lease(
            "execution", owner="owner", lease_token="", lease_seconds=1
        )
    with pytest.raises(ValueError, match="lease_seconds"):
        repository.renew_provider_join_lease(
            "execution", owner="owner", lease_token="token", lease_seconds=0
        )


@pytest.mark.asyncio
async def test_a2ui_graph_resume_maps_checkpoint_and_backend_errors() -> None:
    """Map durable checkpoint loss and backend errors to stable responses."""
    graph_resume_request = cast(
        Any, getattr(a2ui_resume, "_GraphResumeRequest")
    )
    invoke_graph = cast(Any, getattr(a2ui_resume, "_invoke_graph"))
    request = graph_resume_request(
        run_id="run",
        graph=object(),
        payload={},
        resume_graph=lambda *_args: None,
        failure_detail="resume failed",
    )

    async def missing_checkpoint(*_args: object) -> dict[str, object]:
        raise NoCheckpointError("missing")

    with pytest.raises(SafeApiError) as checkpoint:
        await invoke_graph(
            graph_resume_request(
                run_id="run",
                graph=request.graph,
                payload={},
                resume_graph=missing_checkpoint,
                failure_detail="resume failed",
            )
        )
    assert checkpoint.value.status_code == 409

    async def backend_failure(*_args: object) -> dict[str, object]:
        raise RuntimeError("private backend detail")

    with pytest.raises(HTTPException) as failure:
        await invoke_graph(
            graph_resume_request(
                run_id="run",
                graph=request.graph,
                payload={},
                resume_graph=backend_failure,
                failure_detail="resume failed",
            )
        )
    assert failure.value.status_code == 500


def test_a2ui_surface_and_resume_body_guards() -> None:
    """Cover A2UI surface conflicts and debug result shaping."""
    record = SimpleNamespace(status="running", result={})
    with pytest.raises(HTTPException, match="not awaiting"):
        a2ui_resume.open_surface_for_action(
            cast(Any, record), surface_id="surface", widget="confirm"
        )
    waiting = SimpleNamespace(status="waiting_input", result={})
    with pytest.raises(HTTPException, match="no open"):
        a2ui_resume.open_surface_for_action(
            cast(Any, waiting), surface_id="surface", widget="confirm"
        )
    waiting.result = {"interrupt": {"draft": {"a2ui": {"surface_id": "x"}}}}
    with pytest.raises(HTTPException, match="surface_id"):
        a2ui_resume.open_surface_for_action(
            cast(Any, waiting), surface_id="surface", widget="confirm"
        )
    waiting.result["interrupt"]["draft"]["a2ui"] = {
        "surface_id": "surface",
        "widget": "form",
    }
    with pytest.raises(HTTPException, match="widget"):
        a2ui_resume.open_surface_for_action(
            cast(Any, waiting), surface_id="surface", widget="confirm"
        )

    interrupt_body = a2ui_resume.review_run_body(
        a2ui_resume.ReviewExecution(
            run_id="run",
            status="waiting_input",
            interrupt={
                "summary": "x",
                "draft": {
                    "a2ui": {
                        "catalog_version": "v1.0",
                        "surface_id": "surface",
                        "widget": "confirm",
                        "props": {"title": "Review", "body": "Continue"},
                    }
                },
            },
        ),
        debug=False,
    )
    assert interrupt_body["status"] == "input_required"
    terminal_body = a2ui_resume.review_run_body(
        a2ui_resume.ReviewExecution(
            run_id="run",
            status="succeeded",
            result={"formatted": {"answer": "ok"}, "raw": {"private": 1}},
        ),
        debug=False,
    )
    assert "raw" not in terminal_body["result"]


def test_execution_dispatch_private_parsing_and_identity_guards() -> None:
    """Cover private projection and canonical identity checks."""
    dispatch_request = cast(
        Any, getattr(execution_dispatch, "_dispatch_request")
    )
    merge_attachment_evidence = cast(
        Any, getattr(execution_dispatch, "_merge_attachment_evidence")
    )
    services = execution_dispatch.ExecutionDispatchServices(
        app=FastAPI(),
        app_attr=lambda _name: None,
        current_user=lambda: "owner",
        tasks_db_path=lambda: "tasks.db",
        routed_reservation_binder=lambda **_kwargs: None,
    )
    request = dispatch_request(
        "ChatAgent",
        {"value": 1, "__attachments": [], "__attachment_owner": "u"},
        {},
        services,
    )
    assert request.identity.attachment_owner == "u"
    assert request.conversation.value is None
    assert request.conversation.route is None
    request.conversation.private_state = {}
    merge_attachment_evidence(request, {"asset": "x"})
    merge_attachment_evidence(request, None)
    assert request.conversation.private_state == {
        "managed_attachment_evidence": {"asset": "x"}
    }

    envelope = build_conversation_context_envelope(
        "1",
        mode="expert",
        content="query",
        requested_agent_id="ChatAgent",
        allowed_agent_ids=("ChatAgent",),
    )
    routed = dispatch_request(
        "ExpertRouter",
        {
            "__conversation": envelope,
            "__query": "query",
            "__allowed_tools": ["ChatAgent"],
            "locale": "zh-CN",
        },
        {"agent_slug": "network"},
        services,
    )
    assert routed.conversation.route is not None
    assert routed.conversation.route.locale == "zh-CN"
    with pytest.raises(ValueError, match="invalid_canonical"):
        dispatch_request(
            "ChatAgent",
            {"__agent_slug": "network"},
            {"agent_slug": "design"},
            services,
        )
    with pytest.raises(ValueError, match="invalid_canonical"):
        getattr(execution_dispatch, "_context_identity")(routed, services)


def test_execution_dispatch_stage_and_attachment_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover context-stage persistence and attachment resolver failures."""
    dispatch_request = cast(
        Any, getattr(execution_dispatch, "_dispatch_request")
    )
    record_context_stage = cast(
        Any, getattr(execution_dispatch, "_record_context_stage")
    )
    resolve_attachment_input = cast(
        Any, getattr(execution_dispatch, "_resolve_attachment_input")
    )
    services = execution_dispatch.ExecutionDispatchServices(
        app=FastAPI(),
        app_attr=lambda _name: SimpleNamespace(info=lambda *_args: None),
        current_user=lambda: "owner",
        tasks_db_path=lambda: "tasks.db",
        routed_reservation_binder=lambda **_kwargs: nullcontext(),
    )
    request = dispatch_request(
        "ChatAgent", {"value": 1}, {"execution_id": "execution"}, services
    )
    record_context_stage({}, request, services)
    body = {"conversation_context": {"schema_version": 1}}
    monkeypatch.setattr(
        execution_dispatch,
        "SQLiteExecutionReservationRepository",
        lambda _path: SimpleNamespace(
            record_context_stage=lambda **_kwargs: False
        ),
    )
    with pytest.raises(SafeApiError, match="pending"):
        record_context_stage(body, request, services)

    assert resolve_attachment_input(request, services)
    request.attachments = ["attachment"]
    with pytest.raises(RuntimeError, match="asset_resolver"):
        resolve_attachment_input(request, services)


@pytest.mark.asyncio
async def test_execution_dispatch_expert_router_guard() -> None:
    """Reject an ExpertRouter request without a valid conversation route."""
    dispatch_request = cast(
        Any, getattr(execution_dispatch, "_dispatch_request")
    )
    execute_expert_router = cast(
        Any, getattr(execution_dispatch, "_execute_expert_router")
    )
    services = execution_dispatch.ExecutionDispatchServices(
        app=FastAPI(),
        app_attr=lambda name: (
            (
                lambda _payload: asyncio.sleep(
                    0,
                    result=(
                        SimpleNamespace(tool_name="ChatAgent", arguments={}),
                        "chat",
                    ),
                )
            )
            if name == "_select_expert_routing"
            else None
        ),
        current_user=lambda: "owner",
        tasks_db_path=lambda: "tasks.db",
        routed_reservation_binder=lambda **_kwargs: nullcontext(),
    )
    envelope = build_conversation_context_envelope(
        "1",
        mode="expert",
        content="query",
        requested_agent_id="ChatAgent",
        allowed_agent_ids=("ChatAgent",),
    )
    request = dispatch_request(
        "ExpertRouter",
        {
            "__conversation": envelope,
            "__query": "query",
            "__allowed_tools": ["ChatAgent"],
        },
        {"execution_id": "execution", "agent_slug": "router"},
        services,
    )
    request.conversation.value = None
    request.conversation.route = None
    with pytest.raises(ValueError, match="invalid_expert_router"):
        await execute_expert_router(request, services, None)


@pytest.mark.asyncio
async def test_execution_dispatch_native_attachment_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project managed attachment evidence into a native tool call."""
    dispatch_request = cast(
        Any, getattr(execution_dispatch, "_dispatch_request")
    )
    execute_native = cast(Any, getattr(execution_dispatch, "_execute_native"))
    services = execution_dispatch.ExecutionDispatchServices(
        app=FastAPI(),
        app_attr=lambda _name: None,
        current_user=lambda: "owner",
        tasks_db_path=lambda: "tasks.db",
        routed_reservation_binder=lambda **_kwargs: nullcontext(),
    )

    def prepare_native_attachment_arguments(**_kwargs: object):
        """Prepare native arguments and their public evidence."""
        return (
            {"prepared": True},
            SimpleNamespace(evidence={"asset": "evidence"}),
        )

    def prepare_selected_expert_arguments(**_kwargs: object):
        """Prepare selected-expert arguments without public evidence."""
        return (
            {"selected": True},
            SimpleNamespace(evidence=None),
        )

    async def invoke_tool_enveloped(*args: object, **kwargs: object):
        """Return the captured tool name and keyword arguments."""
        return {"tool": args[0], "kwargs": kwargs}

    native_arguments = prepare_native_attachment_arguments
    selected_arguments = prepare_selected_expert_arguments
    attachments = SimpleNamespace(
        prepare_native_attachment_arguments=native_arguments,
        prepare_selected_expert_arguments=selected_arguments,
    )
    mcp_app = SimpleNamespace(
        invoke_tool_enveloped=invoke_tool_enveloped,
    )

    def fake_import(name: str):
        if name.endswith("attachment_inputs"):
            return attachments
        if name.endswith("mcp.app"):
            return mcp_app
        raise AssertionError(name)

    monkeypatch.setattr(execution_dispatch, "import_module", fake_import)
    native = dispatch_request(
        "ChatAgent",
        {"__attachments": ["asset"]},
        {"execution_id": "execution", "agent_slug": "chat"},
        services,
    )
    assert (await execute_native(native, services, None))[
        "tool"
    ] == "ChatAgent"
    assert native.conversation.private_state == {
        "managed_attachment_evidence": {"asset": "evidence"}
    }
