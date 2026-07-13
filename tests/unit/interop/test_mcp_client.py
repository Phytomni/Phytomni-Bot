# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the feature-gated external MCP LangChain adapter seam."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, cast

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool

from mcp_server_phytomni.interop import (
    InteropRegistry,
    load_external_mcp_tools,
)
from mcp_server_phytomni.interop import mcp_client as interop_mcp
from mcp_server_phytomni.interop.mcp_client import (
    InteropMCPError,
    InteropMCPToolError,
    build_mcp_connection,
)
from mcp_server_phytomni.interop.models import (
    A2ATarget,
    InteropTarget,
    MCPStdioTarget,
    MCPStreamableHttpTarget,
)

pytestmark = pytest.mark.unit


def _http_target(**overrides: object) -> MCPStreamableHttpTarget:
    """Build one operator-owned streamable HTTP target."""
    payload: dict[str, object] = {
        "id": "peer-http",
        "kind": "mcp",
        "transport": "streamable_http",
        "url": "https://mcp.example.test/v1/mcp",
        "allowed_tools": ["search_genes"],
    }
    payload.update(overrides)
    return MCPStreamableHttpTarget.model_validate(payload)


def _stdio_target(**overrides: object) -> MCPStdioTarget:
    """Build one operator-owned stdio target."""
    payload: dict[str, object] = {
        "id": "peer-stdio",
        "kind": "mcp",
        "transport": "stdio",
        "command": "/opt/phytomni-peer",
        "args": ["--mode", "stdio"],
        "env_keys": ["LANG", "PATH"],
        "allowed_tools": ["annotate_gene"],
    }
    payload.update(overrides)
    return MCPStdioTarget.model_validate(payload)


def _registry(target: InteropTarget) -> InteropRegistry:
    """Build an enabled registry containing one validated target."""
    return InteropRegistry(enabled=True, _targets={target.id: target})


def _tool(
    name: str,
    callback: Callable[..., Awaitable[Any]],
) -> StructuredTool:
    """Build a minimal fake LangChain executable tool."""
    return StructuredTool.from_function(
        coroutine=callback,
        name=name,
        description="fixture tool",
        infer_schema=False,
    )


class _FakeAdapter:
    """Offline stand-in for the official MultiServerMCPClient."""

    instances: list[_FakeAdapter] = []
    tools: list[Any] = []

    def __init__(self, connections: Mapping[str, object], **kwargs: object):
        """Record one fake official-client construction."""
        self.connections = dict(connections)
        self.kwargs = kwargs
        self.get_tools_calls: list[str | None] = []
        type(self).instances.append(self)

    async def get_tools(self, *, server_name: str | None = None) -> list[Any]:
        """Return the configured fake tools for the requested server."""
        self.get_tools_calls.append(server_name)
        return list(type(self).tools)

    @property
    def connection_names(self) -> tuple[str, ...]:
        """Expose deterministic connection keys for fixture assertions."""
        return tuple(sorted(self.connections))


@pytest.fixture(autouse=True)
def _reset_fake_adapter() -> None:
    """Keep fake adapter state isolated between tests."""
    _FakeAdapter.instances.clear()
    _FakeAdapter.tools = []


async def test_one_off_client_uses_fixed_adapter_policy() -> None:
    """Loading one target never invokes official all-target gathering."""
    target = _http_target()
    _FakeAdapter.tools = [
        _tool("peer-http_search_genes", _answer),
    ]

    tools = await load_external_mcp_tools(
        target.id,
        registry=_registry(target),
        _client_cls=_FakeAdapter,
    )

    assert len(_FakeAdapter.instances) == 1
    client = _FakeAdapter.instances[0]
    assert set(client.connections) == {target.id}
    assert client.get_tools_calls == [target.id]
    assert client.kwargs == {
        "tool_name_prefix": True,
        "handle_tool_errors": False,
    }
    assert [tool.name for tool in tools] == ["peer-http_search_genes"]


async def test_allowed_tools_are_filtered_using_original_remote_name() -> None:
    """A prefixed adapter name is checked against the registry allowlist."""
    target = _http_target(allowed_tools=("search_genes",))
    _FakeAdapter.tools = [
        _tool("peer-http_search_genes", _answer),
        _tool("peer-http_delete_everything", _answer),
        _tool("other-server_search_genes", _answer),
    ]

    tools = await load_external_mcp_tools(
        target.id,
        registry=_registry(target),
        _client_cls=_FakeAdapter,
    )

    assert [tool.name for tool in tools] == ["peer-http_search_genes"]


async def test_allowed_tools_accept_double_separator() -> None:
    """Capability callers may use the plan's double-separator spelling."""
    target = _http_target()
    _FakeAdapter.tools = [
        _tool("peer-http__search_genes", _answer),
    ]

    tools = await load_external_mcp_tools(
        target.id,
        registry=_registry(target),
        _client_cls=_FakeAdapter,
    )

    assert [tool.name for tool in tools] == ["peer-http__search_genes"]


async def test_public_invoke_uses_target_id_and_allowlisted_remote_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public invocation seam never accepts a peer endpoint directly."""
    target = _http_target()
    _FakeAdapter.tools = [
        _tool("peer-http_search_genes", _answer),
    ]
    monkeypatch.setattr(
        interop_mcp,
        "_load_official_client",
        lambda: _FakeAdapter,
    )

    result = await interop_mcp.invoke_external_mcp_tool(
        target.id,
        "search_genes",
        {"gene": "AT1G01010"},
        registry=_registry(target),
    )

    assert result == "ok"
    assert len(_FakeAdapter.instances) == 1


async def test_http_connection_uses_hardened_factory_without_caller_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The official adapter receives the C3.2 factory, not raw credentials."""
    target = _http_target(credential_ref="peer-auth")
    calls: list[tuple[str, Mapping[str, str] | None, object | None]] = []
    sentinel = object()

    def fake_factory(
        target_id: str,
        *,
        registry: InteropRegistry,
        sensitive_config: object | None = None,
        resolver: object | None = None,
    ) -> object:
        del registry, sensitive_config, resolver
        calls.append((target_id, None, None))
        return sentinel

    monkeypatch.setattr(interop_mcp, "httpx_client_factory", fake_factory)
    connection = build_mcp_connection(target, registry=_registry(target))
    factory = connection["httpx_client_factory"]

    assert callable(factory)
    assert factory(headers={"Authorization": "placeholder"}, timeout=None)
    assert calls == [(target.id, None, None)]


def test_stdio_connection_uses_only_explicit_minimal_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stdio config never inherits the process's complete environment."""
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("PHYTOMNI_OPERATOR_VALUE", "placeholder")
    target = _stdio_target()

    connection = build_mcp_connection(target, registry=_registry(target))

    assert connection["transport"] == "stdio"
    assert connection["command"] == target.command
    assert connection["args"] == list(target.args)
    assert connection["env"] == {"LANG": "C.UTF-8", "PATH": "/usr/bin"}
    assert "PHYTOMNI_OPERATOR_VALUE" not in connection["env"]


async def test_remote_error_is_stable_failure_not_success_tool_message() -> (
    None
):
    """Adapter execution errors become a redacted stable exception."""

    async def remote_failure(**_: Any) -> ToolMessage:
        return ToolMessage(
            content="peer detail should not escape",
            tool_call_id="call-1",
            status="error",
        )

    target = _http_target()
    _FakeAdapter.tools = [
        _tool("peer-http_search_genes", remote_failure),
    ]
    tools = await load_external_mcp_tools(
        target.id,
        registry=_registry(target),
        _client_cls=_FakeAdapter,
    )

    with pytest.raises(InteropMCPToolError) as caught:
        await tools[0].ainvoke({})

    assert caught.value.code == "remote_tool_error"
    assert caught.value.target_id == target.id
    assert caught.value.tool_name == "search_genes"
    assert "peer detail" not in str(caught.value)
    assert "placeholder" not in str(caught.value)


async def test_flag_off_never_imports_or_constructs_adapter() -> None:
    """Flag-off startup stays inert when the package is absent."""
    tools = await load_external_mcp_tools(
        "peer-http",
        registry=InteropRegistry.disabled(),
        _client_cls=cast(
            Any,
            lambda *_args, **_kwargs: pytest.fail(
                "adapter must not be constructed"
            ),
        ),
    )

    assert tools == ()


def test_a2a_target_is_not_consumed_by_the_mcp_adapter() -> None:
    """A2A discovery remains a separate C3.5 transport boundary."""
    target = A2ATarget.model_validate(
        {
            "id": "peer-a2a",
            "kind": "a2a",
            "transport": "a2a",
            "card_base_url": "https://a2a.example.test/card",
            "allowed_interface_origins": ["https://a2a.example.test"],
            "allowed_skills": ["search_genes"],
        }
    )

    with pytest.raises(InteropMCPError) as caught:
        build_mcp_connection(target, registry=_registry(target))

    assert caught.value.code == "unsupported_transport"


async def _answer(**_: Any) -> str:
    """Return a plain fixture value from a fake remote tool."""
    return "ok"
