# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline end-to-end coverage for the external interop peer boundaries.

The fake peers exercise the same discovery, adapter, and execution seams as
an operator deployment while keeping every request inside the test process.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    Sequence,
)
from typing import Any, cast

import httpx
import pytest
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
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
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool

from mcp_server_phytomni.interop.a2a_client import (
    InteropA2AClientError,
    send_external_a2a_task,
)
from mcp_server_phytomni.interop.a2a_discovery import (
    discover_external_a2a_capabilities,
)
from mcp_server_phytomni.interop.a2a_mapping import MAX_TEXT_BYTES
from mcp_server_phytomni.interop.capabilities import (
    discover_external_mcp_capabilities,
)
from mcp_server_phytomni.interop.mcp_client import (
    InteropMCPToolError,
    invoke_external_mcp_tool,
)
from mcp_server_phytomni.interop.models import (
    A2ATarget,
    MCPStreamableHttpTarget,
)
from mcp_server_phytomni.interop.registry import InteropRegistry

pytestmark = pytest.mark.unit

_REAL_ASYNC_REQUEST = httpx.AsyncClient.request
type _PeerResponse = (
    Message | Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent | bytes
)


@pytest.fixture(autouse=True)
def _allow_fake_peer_transports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep HTTPX's ASGI transport available to the local fake peer."""
    monkeypatch.setattr(httpx.AsyncClient, "request", _REAL_ASYNC_REQUEST)


def _a2a_target() -> A2ATarget:
    """Build one fixed target for the local A2A peer."""
    return A2ATarget.model_validate(
        {
            "id": "fake-a2a",
            "kind": "a2a",
            "transport": "a2a",
            "card_base_url": (
                "https://fake-a2a.example.test/" ".well-known/agent-card.json"
            ),
            "allowed_interface_origins": ["https://fake-a2a.example.test"],
            "allowed_interface_bindings": ["JSONRPC"],
            "allowed_skills": ["annotate"],
            "total_timeout_seconds": 2.0,
            "idle_timeout_seconds": 1.0,
            "response_max_bytes": 1024 * 1024,
        }
    )


def _a2a_registry(target: A2ATarget) -> InteropRegistry:
    """Put one fake A2A target behind the normal registry seam."""
    return InteropRegistry(enabled=True, _targets={target.id: target})


def _a2a_card() -> AgentCard:
    """Return the reduced card the fake peer advertises."""
    return AgentCard(
        name="Offline A2A peer",
        description="Fake peer for the interoperability suite",
        version="1",
        supported_interfaces=[
            AgentInterface(
                url="https://fake-a2a.example.test/a2a",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        capabilities=AgentCapabilities(streaming=True),
        skills=[
            AgentSkill(
                id="annotate",
                name="Annotate",
                description="Annotate a plant-science query",
            )
        ],
    )


async def _resolve_fake_host(_hostname: str, _port: int) -> Sequence[str]:
    """Resolve only to a public fixture address under the normal policy."""
    return ("8.8.8.8",)


def _a2a_peer_factory(
    sequences: Sequence[Sequence[tuple[_PeerResponse, float]]],
) -> tuple[
    Callable[..., httpx.AsyncClient],
    list[dict[str, Any]],
    list[httpx.AsyncClient],
    asyncio.Event,
]:
    """Build an ASGI Agent Card plus JSON-RPC/SSE fake peer."""
    card_payload = json_format.MessageToDict(_a2a_card())
    calls: list[dict[str, Any]] = []
    clients: list[httpx.AsyncClient] = []
    client_ready = asyncio.Event()

    async def app(scope: Mapping[str, Any], receive: Any, send: Any) -> None:
        """Serve the card and one deterministic SSE sequence per POST."""
        if scope["type"] != "http":
            raise AssertionError("fake peer only supports HTTP")
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break
        if scope["method"] == "GET":
            payload = json.dumps(card_payload).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": payload})
            return

        calls.append(json.loads(body))
        index = min(len(calls) - 1, len(sequences) - 1)
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        for response, delay in sequences[index]:
            if delay:
                await asyncio.sleep(delay)
            if isinstance(response, bytes):
                frame = response
            else:
                result = json_format.MessageToDict(
                    to_stream_response(response)
                )
                rpc = json.dumps(
                    {"jsonrpc": "2.0", "id": "1", "result": result}
                )
                frame = f"data: {rpc}\n\n".encode()
            await send(
                {
                    "type": "http.response.body",
                    "body": frame,
                    "more_body": True,
                }
            )
        await send({"type": "http.response.body", "body": b""})

    def factory(
        _target_id: str,
        *,
        registry: InteropRegistry,
        resolver: Any,
        sensitive_config: Any = None,
    ) -> httpx.AsyncClient:
        """Return one isolated ASGI client for each peer lifecycle."""
        del registry, resolver, sensitive_config
        client = httpx.AsyncClient(
            base_url="https://fake-a2a.example.test",
            transport=httpx.ASGITransport(app=app),
            follow_redirects=False,
            trust_env=False,
        )
        clients.append(client)
        client_ready.set()
        return client

    return factory, calls, clients, client_ready


async def _collect_a2a(**kwargs: Any) -> list[Any]:
    """Consume one A2A stream through the public client seam."""
    registry = kwargs.pop("registry")
    events: list[Any] = []
    streamer = cast(Callable[..., AsyncIterator[Any]], send_external_a2a_task)
    async for event in streamer(registry=registry, **kwargs):
        events.append(event)
    return events


def _completed_task() -> Task:
    """Build a terminal task response for the fake peer."""
    return Task(
        id="task-1",
        context_id="context-1",
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        artifacts=[Artifact(parts=[Part(text="completed")])],
    )


async def test_fake_a2a_peer_discovery_stream_and_resume() -> None:
    """Discover, stream input-required, and resume the same fake task."""
    sequences: list[list[tuple[_PeerResponse, float]]] = [
        [
            (
                TaskStatusUpdateEvent(
                    task_id="task-1",
                    context_id="context-1",
                    status=TaskStatus(
                        state=TaskState.TASK_STATE_INPUT_REQUIRED,
                        message=Message(parts=[Part(text="choose")]),
                    ),
                ),
                0,
            )
        ],
        [(_completed_task(), 0)],
    ]
    factory, calls, clients, _ready = _a2a_peer_factory(sequences)
    target = _a2a_target()
    registry = _a2a_registry(target)

    discovered = await discover_external_a2a_capabilities(
        target.id,
        registry=registry,
        resolver=_resolve_fake_host,
        _client_factory=factory,
    )
    assert [item.qualified_name for item in discovered.data] == [
        "fake-a2a__annotate"
    ]
    assert discovered.errors == ()

    first = await _collect_a2a(
        target_id=target.id,
        capability_id="annotate",
        registry=registry,
        text="start",
        resolver=_resolve_fake_host,
        _client_factory=factory,
    )
    second = await _collect_a2a(
        target_id=target.id,
        capability_id="fake-a2a__annotate",
        registry=registry,
        text="approved",
        task_id=first[-1].task_id,
        context_id=first[-1].context_id,
        resolver=_resolve_fake_host,
        _client_factory=factory,
    )

    assert first[-1].state == "TASK_STATE_INPUT_REQUIRED"
    assert second[-1].state == "TASK_STATE_COMPLETED"
    assert len(calls) == 2
    resumed = calls[1]["params"]["message"]
    assert resumed["taskId"] == "task-1"
    assert resumed["contextId"] == "context-1"
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (
            b'data: {"jsonrpc":"2.0","id":"1","result":{}}\n\n',
            "empty_stream_response",
        ),
        (
            TaskStatusUpdateEvent(
                task_id="task-1",
                context_id="context-1",
                status=TaskStatus(
                    state=TaskState.TASK_STATE_WORKING,
                    message=Message(
                        parts=[Part(text="x" * (MAX_TEXT_BYTES + 1))]
                    ),
                ),
            ),
            "text_too_large",
        ),
    ],
)
async def test_fake_a2a_peer_rejects_malformed_and_oversize_frames(
    response: _PeerResponse,
    code: str,
) -> None:
    """Malformed or oversized peer frames fail closed with stable codes."""
    factory, _calls, clients, _ready = _a2a_peer_factory([[(response, 0)]])
    target = _a2a_target()
    with pytest.raises(InteropA2AClientError) as caught:
        await _collect_a2a(
            target_id=target.id,
            capability_id="annotate",
            registry=_a2a_registry(target),
            text="bounded",
            resolver=_resolve_fake_host,
            _client_factory=factory,
        )
    assert caught.value.code == code
    assert caught.value.target_id == target.id
    assert "fake-a2a.example.test" not in str(caught.value)
    assert all(client.is_closed for client in clients)


async def test_fake_a2a_peer_timeout_and_cancellation_cleanup() -> None:
    """Total timeout and caller cancellation close the peer lifecycle."""
    delayed = TaskStatusUpdateEvent(
        task_id="task-1",
        context_id="context-1",
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )
    factory, _calls, timeout_clients, _ready = _a2a_peer_factory(
        [[(delayed, 0.2)]]
    )
    target = _a2a_target()
    with pytest.raises(InteropA2AClientError) as caught:
        await _collect_a2a(
            target_id=target.id,
            capability_id="annotate",
            registry=_a2a_registry(target),
            text="slow",
            resolver=_resolve_fake_host,
            total_timeout_seconds=0.02,
            _client_factory=factory,
        )
    assert caught.value.code == "total_timeout"
    assert all(client.is_closed for client in timeout_clients)

    factory, _calls, cancel_clients, ready = _a2a_peer_factory(
        [[(delayed, 1.0)]]
    )
    task = asyncio.create_task(
        _collect_a2a(
            target_id=target.id,
            capability_id="annotate",
            registry=_a2a_registry(target),
            text="cancel",
            resolver=_resolve_fake_host,
            _client_factory=factory,
        )
    )
    await asyncio.wait_for(ready.wait(), timeout=1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancel_clients
    assert all(client.is_closed for client in cancel_clients)


def _mcp_target() -> MCPStreamableHttpTarget:
    """Build one fixed target for the fake MCP server."""
    return MCPStreamableHttpTarget.model_validate(
        {
            "id": "fake-mcp",
            "kind": "mcp",
            "transport": "streamable_http",
            "url": "https://fake-mcp.example.test/mcp",
            "allowed_tools": ["search_genes"],
        }
    )


class _FakeMCPServer:
    """Offline MCP peer with lifecycle and tool-catalog capture state."""

    tools: list[Any] = []
    instances: list[_FakeMCPServer] = []

    def __init__(self, connections: Mapping[str, object], **kwargs: object):
        """Record the exact one-target adapter lifecycle."""
        self.connections = dict(connections)
        self.kwargs = kwargs
        self.server_names: list[str | None] = []
        type(self).instances.append(self)

    async def get_tools(self, *, server_name: str | None = None) -> list[Any]:
        """Return the fake peer's current tool catalog."""
        self.server_names.append(server_name)
        return list(type(self).tools)


def _mcp_tool(
    name: str,
    callback: Callable[..., Awaitable[Any]],
) -> StructuredTool:
    """Build one executable fake MCP tool."""
    return StructuredTool.from_function(
        coroutine=callback,
        name=name,
        description="offline fake peer tool",
        infer_schema=True,
    )


def _mcp_registry(target: MCPStreamableHttpTarget) -> InteropRegistry:
    """Put one fake MCP target behind the normal registry seam."""
    return InteropRegistry(enabled=True, _targets={target.id: target})


@pytest.fixture(autouse=True)
def _reset_fake_mcp_server() -> None:
    """Keep fake MCP lifecycles isolated between tests."""
    _FakeMCPServer.instances.clear()
    _FakeMCPServer.tools = []


async def test_fake_mcp_server_discovery_then_invocation() -> None:
    """Discovery and execution share the allowlisted fake MCP peer."""

    async def answer(gene: str) -> str:
        return f"gene={gene}"

    target = _mcp_target()
    _FakeMCPServer.tools = [
        _mcp_tool("fake-mcp_search_genes", answer),
    ]
    registry = _mcp_registry(target)

    discovered = await discover_external_mcp_capabilities(
        target.id,
        registry=registry,
        _client_cls=_FakeMCPServer,
    )
    result = await invoke_external_mcp_tool(
        target.id,
        "search_genes",
        {"gene": "AT1G01010"},
        registry=registry,
        _client_cls=_FakeMCPServer,
    )

    assert [item.qualified_name for item in discovered.data] == [
        "fake-mcp__search_genes"
    ]
    assert discovered.errors == ()
    assert result == "gene=AT1G01010"
    assert len(_FakeMCPServer.instances) == 2
    assert all(
        instance.server_names == [target.id]
        for instance in _FakeMCPServer.instances
    )


async def test_fake_mcp_server_error_is_not_a_success_payload() -> None:
    """A fake MCP ``isError`` result becomes a stable boundary error."""

    async def failure(**_: Any) -> ToolMessage:
        return ToolMessage(
            content="peer stack trace must stay local",
            tool_call_id="fake-call",
            status="error",
        )

    target = _mcp_target()
    _FakeMCPServer.tools = [
        _mcp_tool("fake-mcp_search_genes", failure),
    ]
    with pytest.raises(InteropMCPToolError) as caught:
        await invoke_external_mcp_tool(
            target.id,
            "search_genes",
            {"gene": "AT1G01010"},
            registry=_mcp_registry(target),
            _client_cls=_FakeMCPServer,
        )
    assert caught.value.code == "remote_tool_error"
    assert "peer stack trace" not in str(caught.value)
