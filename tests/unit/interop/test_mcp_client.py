# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the external MCP LangChain adapter seam."""

# pylint: disable=protected-access, too-few-public-methods

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from types import SimpleNamespace
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
    invoke_external_mcp_tool,
)
from mcp_server_phytomni.interop.models import (
    A2ATarget,
    InteropTarget,
    MCPStdioTarget,
    MCPStreamableHttpTarget,
)
from mcp_server_phytomni.interop.runtime import InteropResourceRuntime

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
    return InteropRegistry(_targets={target.id: target})


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
        _client_cls=_FakeAdapter,
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


def _a2a_target() -> A2ATarget:
    """Build one A2A registry entry that MCP must refuse."""
    return A2ATarget.model_validate(
        {
            "id": "peer-a2a",
            "kind": "a2a",
            "transport": "a2a",
            "card_base_url": "https://a2a.example.test/card",
            "allowed_interface_origins": ["https://a2a.example.test"],
            "allowed_skills": ["search_genes"],
        }
    )


def test_unknown_target_is_sanitized() -> None:
    """A missing registry id never echoes operator or peer detail."""
    with pytest.raises(InteropMCPError) as caught:
        build_mcp_connection(
            _http_target(),
            registry=InteropRegistry(_targets={}),
        )

    assert caught.value.code == "unknown_target"
    assert caught.value.target_id == "peer-http"


def test_registry_mismatch_is_rejected() -> None:
    """Callers cannot swap a same-id target after registry validation."""
    registered = _http_target()
    swapped = _http_target(url="https://other.example.test/v1/mcp")

    with pytest.raises(InteropMCPError) as caught:
        build_mcp_connection(swapped, registry=_registry(registered))

    assert caught.value.code == "target_registry_mismatch"


def test_unknown_target_type_is_unsupported() -> None:
    """A non-MCP, non-A2A object still maps to unsupported_transport."""

    class _UnknownTarget:
        """Minimal stand-in that is not a modeled interop target."""

        id = "peer-http"

    unknown = _UnknownTarget()

    class _Registry:
        """Return the unknown object so equality succeeds."""

        def require_target(self, target_id: str) -> object:
            """Return the unknown target so identity checks can run."""
            del target_id
            return unknown

    with pytest.raises(InteropMCPError) as caught:
        build_mcp_connection(
            cast(InteropTarget, unknown),
            registry=_Registry(),  # type: ignore[arg-type]
        )

    assert caught.value.code == "unsupported_transport"


def _raise_import_error() -> object:
    """Stand-in import_module result that cannot be imported."""
    raise ImportError("missing")


@pytest.mark.parametrize(
    ("module_factory", "loader_name"),
    [
        (_raise_import_error, "client"),
        (SimpleNamespace, "client"),
        (
            lambda: SimpleNamespace(MultiServerMCPClient="not-callable"),
            "client",
        ),
        (_raise_import_error, "tools"),
        (SimpleNamespace, "tools"),
        (lambda: SimpleNamespace(load_mcp_tools=123), "tools"),
    ],
)
def test_official_adapter_import_failures_are_stable(
    monkeypatch: pytest.MonkeyPatch,
    module_factory: Callable[[], object],
    loader_name: str,
) -> None:
    """Missing or malformed adapter modules stay adapter_unavailable."""

    def fake_import(name: str) -> object:
        del name
        return module_factory()

    monkeypatch.setattr(interop_mcp.importlib, "import_module", fake_import)
    loader = (
        interop_mcp._load_official_client
        if loader_name == "client"
        else interop_mcp._load_official_tool_loader
    )
    with pytest.raises(InteropMCPError) as caught:
        loader()

    assert caught.value.code == "adapter_unavailable"


def test_official_adapter_imports_when_installed() -> None:
    """The live adapter symbols are returned when the extra is present."""
    try:
        client_cls = interop_mcp._load_official_client()
        loader = interop_mcp._load_official_tool_loader()
    except InteropMCPError as exc:
        assert exc.code == "adapter_unavailable"
        return
    assert callable(client_cls)
    assert callable(loader)


def test_remote_name_rejects_non_string_and_empty_suffix() -> None:
    """Adapter names that are not prefixed strings cannot be allowlisted."""
    assert interop_mcp._remote_name(123, "peer-http") is None
    assert interop_mcp._remote_name("peer-http__", "peer-http") is None
    assert interop_mcp._remote_name("peer-http_", "peer-http") is None


async def test_mapping_remote_error_is_rejected() -> None:
    """Dict-shaped MCP errors are treated as remote_tool_error."""

    async def remote_failure(**_: Any) -> dict[str, object]:
        return {"isError": True, "content": "peer detail"}

    target = _http_target()
    _FakeAdapter.tools = [_tool("peer-http_search_genes", remote_failure)]
    tools = await load_external_mcp_tools(
        target.id,
        registry=_registry(target),
        _client_cls=_FakeAdapter,
    )

    with pytest.raises(InteropMCPToolError) as caught:
        await tools[0].ainvoke({})

    assert caught.value.code == "remote_tool_error"
    assert "peer detail" not in str(caught.value)


async def test_is_error_alias_and_raised_adapter_failure() -> None:
    """Both is_error aliases and raised adapter errors stay sanitized."""

    async def alias_failure(**_: Any) -> dict[str, object]:
        return {"is_error": True}

    async def raised_failure(**_: Any) -> str:
        raise RuntimeError("peer stack must not escape")

    target = _http_target()
    _FakeAdapter.tools = [_tool("peer-http_search_genes", alias_failure)]
    tools = await load_external_mcp_tools(
        target.id,
        registry=_registry(target),
        _client_cls=_FakeAdapter,
    )
    with pytest.raises(InteropMCPToolError):
        await tools[0].ainvoke({})

    _FakeAdapter.instances.clear()
    _FakeAdapter.tools = [_tool("peer-http_search_genes", raised_failure)]
    tools = await load_external_mcp_tools(
        target.id,
        registry=_registry(target),
        _client_cls=_FakeAdapter,
    )
    with pytest.raises(InteropMCPToolError) as caught:
        await tools[0].ainvoke({})
    assert "peer stack" not in str(caught.value)


async def test_invalid_response_format_falls_back_to_content() -> None:
    """Unknown official response formats are coerced before wrapping."""

    class _WeirdTool:
        """Stand-in whose response_format is outside the official set."""

        name = "peer-http_search_genes"
        description = "fixture"
        args_schema = None
        response_format = "unexpected"
        metadata = {"source": "peer"}
        return_direct = True

        async def ainvoke(self, arguments: Mapping[str, Any]) -> Any:
            """Echo tool arguments for schema-normalization coverage."""
            return arguments

    target = _http_target()
    _FakeAdapter.tools = [_WeirdTool()]
    tools = await load_external_mcp_tools(
        target.id,
        registry=_registry(target),
        _client_cls=_FakeAdapter,
    )

    assert len(tools) == 1
    assert await tools[0].ainvoke({}) == {"arguments": None}


async def test_non_string_tool_name_is_dropped() -> None:
    """A discovered tool without a string name is not executable."""
    target = _http_target()
    _FakeAdapter.tools = [SimpleNamespace(name=123)]
    tools = await load_external_mcp_tools(
        target.id,
        registry=_registry(target),
        _client_cls=_FakeAdapter,
    )
    assert tools == ()


async def test_client_construction_failure_is_discovery_failed() -> None:
    """Official client construction exceptions stay discovery_failed."""

    class _BoomAdapter:
        """Fail before any peer request is issued."""

        def __init__(self, *_: object, **__: object) -> None:
            raise RuntimeError("peer constructor detail")

    target = _http_target()
    with pytest.raises(InteropMCPError) as caught:
        await load_external_mcp_tools(
            target.id,
            registry=_registry(target),
            _client_cls=_BoomAdapter,
        )
    assert caught.value.code == "discovery_failed"
    assert "constructor" not in str(caught.value)


async def test_a2a_target_cannot_load_or_invoke_mcp_tools() -> None:
    """A2A registry entries never enter the MCP adapter or invoke path."""
    target = _a2a_target()
    registry = _registry(target)

    with pytest.raises(InteropMCPError) as caught:
        await load_external_mcp_tools(
            target.id,
            registry=registry,
            _client_cls=_FakeAdapter,
        )
    assert caught.value.code == "unsupported_transport"

    with pytest.raises(InteropMCPError) as caught:
        await invoke_external_mcp_tool(
            target.id,
            "search_genes",
            {},
            registry=registry,
            _client_cls=_FakeAdapter,
        )
    assert caught.value.code == "unsupported_transport"


async def test_runtime_unavailable_without_injected_client() -> None:
    """Loading without a client class requires a process-owned runtime."""
    target = _http_target()
    with pytest.raises(InteropMCPError) as caught:
        await load_external_mcp_tools(target.id, registry=_registry(target))
    assert caught.value.code == "runtime_unavailable"


async def test_runtime_loader_uses_leased_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime discovery lists and calls tools through the Interop lease."""
    session = SimpleNamespace(list_calls=0, call_calls=0)

    async def list_tools(*_: object, **__: object) -> list[str]:
        session.list_calls += 1
        return ["search_genes"]

    async def call_tool(*_: object, **__: object) -> str:
        session.call_calls += 1
        return "called"

    session.list_tools = list_tools
    session.call_tool = call_tool

    class _Runtime:
        """Forward leased operations to the fake MCP session."""

        async def run_mcp(
            self,
            target_id: str,
            operation: Callable[[object], Awaitable[Any]],
        ) -> Any:
            """Forward one leased MCP operation to the fake session."""
            del target_id
            return await operation(session)

    async def fake_loader(
        leased: Any,
        **_: object,
    ) -> list[StructuredTool]:
        await leased.list_tools()
        await leased.call_tool("search_genes")
        return cast(
            list[StructuredTool],
            [
                _tool("peer-http_search_genes", _answer),
                _tool("peer-http_hidden", _answer),
                SimpleNamespace(name=None),
            ],
        )

    def _loader_factory() -> Any:
        """Return the scripted official adapter loader."""
        return fake_loader

    monkeypatch.setattr(interop_mcp, "current_interop_runtime", _Runtime)
    monkeypatch.setattr(
        interop_mcp, "_load_official_tool_loader", _loader_factory
    )
    target = _http_target()
    tools = await load_external_mcp_tools(
        target.id,
        registry=_registry(target),
    )

    assert [tool.name for tool in tools] == ["peer-http_search_genes"]
    assert session.list_calls == 1
    assert session.call_calls == 1


async def test_runtime_loader_failure_is_discovery_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adapter loader exceptions from the runtime path stay sanitized."""

    class _Runtime:
        """Unused runtime; the loader fails before leasing."""

        async def run_mcp(self, *_: object, **__: object) -> Any:
            """Fail if a lease is attempted after the loader exploded."""
            raise AssertionError("lease must not run")

    def boom_loader() -> Any:
        raise RuntimeError("adapter exploded")

    monkeypatch.setattr(interop_mcp, "current_interop_runtime", _Runtime)
    monkeypatch.setattr(
        interop_mcp,
        "_load_official_tool_loader",
        boom_loader,
    )
    target = _http_target()
    with pytest.raises(InteropMCPError) as caught:
        await load_external_mcp_tools(target.id, registry=_registry(target))
    assert caught.value.code == "discovery_failed"


async def test_invoke_rejects_disallowed_and_missing_tools() -> None:
    """Allowlist misses and absent discovered tools stay target-level."""
    target = _http_target(allowed_tools=("search_genes", "other_tool"))
    _FakeAdapter.tools = [_tool("peer-http_search_genes", _answer)]
    registry = _registry(target)

    with pytest.raises(InteropMCPError) as caught:
        await invoke_external_mcp_tool(
            target.id,
            "not_allowed",
            {},
            registry=registry,
            _client_cls=_FakeAdapter,
        )
    assert caught.value.code == "tool_not_allowed"
    assert caught.value.tool_name == "not_allowed"

    with pytest.raises(InteropMCPError) as caught:
        await invoke_external_mcp_tool(
            target.id,
            "other_tool",
            {},
            registry=registry,
            _client_cls=_FakeAdapter,
        )
    assert caught.value.code == "tool_unavailable"
    assert caught.value.tool_name == "other_tool"


async def test_leased_session_proxies_runtime_operations() -> None:
    """The lease proxy forwards list_tools and call_tool to run_mcp."""
    calls: list[tuple[str, str]] = []

    class _Session:
        """Record the inner MCP session method that ran."""

        async def list_tools(self, *args: object, **kwargs: object) -> str:
            """Return a listed-tools marker."""
            del args, kwargs
            return "listed"

        async def call_tool(self, *args: object, **kwargs: object) -> str:
            """Return a called-tool marker."""
            del args, kwargs
            return "called"

    class _Runtime:
        """Capture the target id used for each lease."""

        async def run_mcp(
            self,
            target_id: str,
            operation: Callable[[object], Awaitable[Any]],
        ) -> Any:
            """Record the leased target id and forwarded result."""
            result = await operation(_Session())
            calls.append((target_id, result))
            return result

    leased = interop_mcp._LeasedMcpSession(
        cast(InteropResourceRuntime, _Runtime()), "peer-http"
    )
    assert await leased.list_tools() == "listed"
    assert await leased.call_tool("search_genes") == "called"
    assert calls == [("peer-http", "listed"), ("peer-http", "called")]
