# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Offline ASGI-peer tests for the external A2A client boundary."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any, cast

import httpx
import pytest
from a2a.types import (
    Artifact,
    Message,
    Part,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.proto_utils import to_stream_response
from google.protobuf import json_format
from tests.support.a2a_fakes import (
    build_agent_card,
    input_required_resume_sequence,
    read_asgi_body,
    send_json_response,
)

from mcp_server_phytomni.interop import a2a_client as client_module
from mcp_server_phytomni.interop.a2a_client import (
    InteropA2AClientError,
    send_external_a2a_task,
)
from mcp_server_phytomni.interop.models import A2ATarget
from mcp_server_phytomni.interop.registry import InteropRegistry

pytestmark = pytest.mark.unit
_REAL_ASYNC_REQUEST = httpx.AsyncClient.request
type ResponseType = (
    Message | Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent
)


@pytest.fixture(autouse=True)
def _allow_asgi_transport_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore HTTPX dispatch so the ASGI transport stays offline."""
    monkeypatch.setattr(httpx.AsyncClient, "request", _REAL_ASYNC_REQUEST)


def _target(**overrides: object) -> A2ATarget:
    """Return an A2A target whose card and interface share one origin."""
    payload: dict[str, object] = {
        "id": "peer",
        "kind": "a2a",
        "transport": "a2a",
        "card_base_url": "https://peer.example.test/card",
        "allowed_interface_origins": ["https://peer.example.test"],
        "allowed_interface_bindings": ["JSONRPC"],
        "allowed_skills": ["annotate"],
        "total_timeout_seconds": 5.0,
        "idle_timeout_seconds": 2.0,
        "response_max_bytes": 1024,
    }
    payload.update(overrides)
    return A2ATarget.model_validate(payload)


def _registry(target: A2ATarget) -> InteropRegistry:
    """Return an enabled registry containing the fixture peer."""
    return InteropRegistry(enabled=True, _targets={target.id: target})


async def _resolver(_hostname: str, _port: int) -> Sequence[str]:
    """Resolve all fixture names to an allowed public address."""
    return ("8.8.8.8",)


def _app_factory(
    sequences: Sequence[Sequence[tuple[ResponseType, float]]],
) -> tuple[
    Callable[..., Awaitable[None]],
    list[dict[str, Any]],
    list[httpx.AsyncClient],
]:
    """Build an ASGI JSON-RPC/SSE peer and record requests/clients."""
    calls: list[dict[str, Any]] = []
    clients: list[httpx.AsyncClient] = []
    card_payload = json_format.MessageToDict(
        build_agent_card(
            base_url="https://peer.example.test",
            name="Peer agent",
            description="ASGI fixture",
            skill_description="Annotate text",
        )
    )

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            raise AssertionError("fixture only supports HTTP")
        request = httpx.Request(
            scope["method"],
            f"https://peer.example.test{scope['path']}",
        )
        body = await read_asgi_body(receive)
        if request.method == "GET":
            await send_json_response(send, card_payload)
            return
        parsed = json.loads(body)
        calls.append(parsed)
        index = min(len(calls) - 1, len(sequences) - 1)
        events = sequences[index]
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        for response, delay in events:
            if delay:
                await asyncio.sleep(delay)
            result = json_format.MessageToDict(
                to_stream_response(response),
            )
            rpc = json.dumps({"jsonrpc": "2.0", "id": "1", "result": result})
            await send(
                {
                    "type": "http.response.body",
                    "body": f"data: {rpc}\n\n".encode(),
                    "more_body": True,
                }
            )
        await send(
            {
                "type": "http.response.body",
                "body": b"",
            }
        )

    return app, calls, clients


def _factory_for(
    app: Callable[..., Awaitable[None]],
    clients: list[httpx.AsyncClient],
) -> Callable[..., httpx.AsyncClient]:
    """Return one isolated HTTPX ASGI client per SDK/card lifecycle."""

    def factory(
        _target_id: str,
        *,
        registry: InteropRegistry,
        resolver: Any,
        sensitive_config: Any = None,
    ) -> httpx.AsyncClient:
        del registry, resolver, sensitive_config
        client = httpx.AsyncClient(
            base_url="https://peer.example.test",
            transport=httpx.ASGITransport(app=app),
            follow_redirects=False,
            trust_env=False,
        )
        clients.append(client)
        return client

    return factory


def _terminal_task(task_id: str = "task-1") -> Task:
    """Build a completed task response."""
    return Task(
        id=task_id,
        context_id="context-1",
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        artifacts=[Artifact(parts=[Part(text="done")])],
    )


async def _collect(**kwargs: Any) -> list[Any]:
    """Consume one public stream into a list for assertions."""
    events: list[Any] = []
    streamer = cast(
        Callable[..., AsyncIterator[Any]],
        getattr(client_module, "send_external_a2a_task"),
    )
    registry = kwargs.pop("registry")
    async for event in streamer(registry=registry, **kwargs):
        events.append(event)
    return events


async def test_send_streams_and_audits_without_payload() -> None:
    """SSE events map through Client.send_message and close both resources."""
    sequences: list[list[tuple[ResponseType, float]]] = [
        [
            (
                TaskStatusUpdateEvent(
                    task_id="task-1",
                    context_id="context-1",
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                ),
                0,
            ),
            (
                TaskArtifactUpdateEvent(
                    task_id="task-1",
                    context_id="context-1",
                    artifact=Artifact(parts=[Part(text="done")]),
                    last_chunk=True,
                ),
                0,
            ),
        ]
    ]
    app, calls, clients = _app_factory(sequences)
    audit: list[dict[str, object]] = []
    target = _target()

    events = await _collect(
        target_id=target.id,
        capability_id="peer__annotate",
        registry=_registry(target),
        text="annotate this",
        resolver=_resolver,
        audit_sink=audit.append,
        _client_factory=_factory_for(app, clients),
    )

    assert [event.kind for event in events] == [
        "status_update",
        "artifact_update",
    ]
    assert events[-1].terminal is True
    assert len(calls) == 1
    assert calls[0]["params"]["metadata"]["skill_id"] == "annotate"
    request_message = calls[0]["params"]["message"]
    assert request_message["parts"][0]["text"] == "annotate this"
    assert {"request_id", "target_id", "target_kind", "capability"}.issubset(
        audit[0]
    )
    assert audit[-1]["terminal_status"] == "artifact_update"
    assert audit[-1]["error_code"] is None
    assert all(client.is_closed for client in clients)
    assert all(
        "https://" not in repr(item) and "annotate this" not in repr(item)
        for item in audit
    )


async def test_input_required_preserves_ids_for_a_second_resume_call() -> None:
    """An input-required terminal event can seed a later task continuation."""
    sequences = input_required_resume_sequence(_terminal_task())
    app, calls, clients = _app_factory(sequences)
    target = _target()
    factory = _factory_for(app, clients)

    first = await _collect(
        target_id="peer",
        capability_id="annotate",
        registry=_registry(target),
        text="start",
        resolver=_resolver,
        _client_factory=factory,
    )
    second = await _collect(
        target_id="peer",
        capability_id="peer__annotate",
        registry=_registry(target),
        text="yes",
        task_id=first[-1].task_id,
        context_id=first[-1].context_id,
        resolver=_resolver,
        _client_factory=factory,
    )

    assert first[-1].state == "TASK_STATE_INPUT_REQUIRED"
    assert second[-1].state == "TASK_STATE_COMPLETED"
    resumed = calls[1]["params"]["message"]
    assert resumed["taskId"] == "task-1"
    assert resumed["contextId"] == "context-1"


@pytest.mark.parametrize(
    ("total", "idle", "code"),
    [(0.05, 1.0, "total_timeout"), (1.0, 0.02, "idle_timeout")],
)
async def test_total_and_idle_timeouts_are_distinct(
    total: float,
    idle: float,
    code: str,
) -> None:
    """The total deadline covers the iterator while idle bounds each next."""
    app, _calls, clients = _app_factory([[(_terminal_task(), 0.2)]])
    target = _target()
    with pytest.raises(InteropA2AClientError) as caught:
        await _collect(
            target_id="peer",
            capability_id="annotate",
            registry=_registry(target),
            text="slow",
            resolver=_resolver,
            total_timeout_seconds=total,
            idle_timeout_seconds=idle,
            _client_factory=_factory_for(app, clients),
        )
    assert caught.value.code == code
    assert caught.value.target_id == "peer"
    assert "https://" not in str(caught.value)
    assert all(client.is_closed for client in clients)


async def test_cancellation_closes_iterator_and_client() -> None:
    """Cancellation still executes the ordered iterator/client cleanup."""
    app, _calls, clients = _app_factory([[(_terminal_task(), 1.0)]])
    target = _target()
    task = asyncio.create_task(
        _collect(
            target_id="peer",
            capability_id="annotate",
            registry=_registry(target),
            text="cancel",
            resolver=_resolver,
            _client_factory=_factory_for(app, clients),
        )
    )
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert clients
    assert all(client.is_closed for client in clients)


def test_public_signature_does_not_accept_peer_endpoint() -> None:
    """Callers can select only registry targets, never arbitrary URLs."""
    parameters = inspect.signature(send_external_a2a_task).parameters
    assert "url" not in parameters
    assert "command" not in parameters
    assert "target_id" in parameters
    assert "capability_id" in parameters
