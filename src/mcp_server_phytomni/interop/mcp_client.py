# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Load one operator-approved external MCP target through LangChain.

The adapter is deliberately kept behind this small boundary.  Callers select
an operator registry target id, never a URL or process command.  A target is
loaded on demand and the resulting executable tools are intentionally not
cached: the official adapter's tools carry connection closures and may carry
credential-bearing HTTP client factories.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable, Mapping
from typing import Any, Literal, cast

import httpx
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool

from ..config.settings import SensitiveConfig
from .http_transport import httpx_client_factory
from .models import (
    A2ATarget,
    InteropTarget,
    MCPStdioTarget,
    MCPStreamableHttpTarget,
)
from .registry import InteropRegistry, InteropRegistryError
from .security import AsyncDNSResolver, resolve_host


class InteropMCPError(RuntimeError):
    """Sanitized failure at the external MCP integration boundary."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        target_id: str | None = None,
        tool_name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.target_id = target_id
        self.tool_name = tool_name


class InteropMCPToolError(InteropMCPError):
    """Stable error for an external MCP tool execution failure."""


def _target_error(
    code: str,
    target_id: str,
    *,
    tool_name: str | None = None,
) -> InteropMCPError:
    """Build a fixed, peer-detail-free error message."""
    return InteropMCPError(
        f"external MCP {code}",
        code=code,
        target_id=target_id,
        tool_name=tool_name,
    )


def _tool_error(target_id: str, tool_name: str) -> InteropMCPToolError:
    """Build the stable error used for a remote ``isError`` result."""
    return InteropMCPToolError(
        "external MCP remote tool failed",
        code="remote_tool_error",
        target_id=target_id,
        tool_name=tool_name,
    )


def _resolve_target(
    target_id: str, registry: InteropRegistry
) -> InteropTarget:
    """Resolve one target without echoing untrusted registry details."""
    if not registry.enabled:
        raise _target_error("disabled", target_id)
    try:
        return registry.require_target(target_id)
    except InteropRegistryError:
        raise _target_error("unknown_target", target_id) from None


def _minimal_stdio_env(target: MCPStdioTarget) -> dict[str, str]:
    """Copy only the four explicitly allowlisted environment variables."""
    return {
        key: value
        for key in target.env_keys
        if (value := os.environ.get(key)) is not None
    }


def _http_factory(
    target: MCPStreamableHttpTarget,
    *,
    registry: InteropRegistry,
    sensitive_config: SensitiveConfig | None,
    resolver: AsyncDNSResolver,
) -> Callable[..., httpx.AsyncClient]:
    """Create an ephemeral adapter callback backed by the hardened factory."""

    def create_client(
        headers: Mapping[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        """Return a policy-bound client, ignoring adapter caller headers."""
        # The target registry, not an adapter caller, owns credentials.  Keep
        # this callback's accepted arguments for the official adapter
        # protocol, but deliberately do not forward headers/auth/timeout.
        del headers, timeout, auth
        return httpx_client_factory(
            target.id,
            registry=registry,
            sensitive_config=sensitive_config,
            resolver=resolver,
        )

    return create_client


def build_mcp_connection(
    target: InteropTarget,
    *,
    registry: InteropRegistry,
    sensitive_config: SensitiveConfig | None = None,
    resolver: AsyncDNSResolver = resolve_host,
) -> dict[str, Any]:
    """Build one official-adapter connection from a validated target.

    The returned dictionary contains no credential values.  HTTP credentials
    are injected by the C3.2 transport only after endpoint validation; stdio
    gets a fresh explicit environment containing at most the operator's
    allowlisted locale/path variables.
    """
    registered = _resolve_target(target.id, registry)
    if registered != target:
        raise _target_error("target_registry_mismatch", target.id)
    if isinstance(target, MCPStreamableHttpTarget):
        return {
            "transport": "streamable_http",
            "url": target.url,
            "httpx_client_factory": _http_factory(
                target,
                registry=registry,
                sensitive_config=sensitive_config,
                resolver=resolver,
            ),
            "timeout": target.total_timeout_seconds,
            "sse_read_timeout": target.idle_timeout_seconds,
            "terminate_on_close": True,
        }
    if isinstance(target, MCPStdioTarget):
        return {
            "transport": "stdio",
            "command": target.command,
            "args": list(target.args),
            "env": _minimal_stdio_env(target),
        }
    if isinstance(target, A2ATarget):
        raise _target_error("unsupported_transport", target.id)
    raise _target_error("unsupported_transport", target.id)


def _load_official_client() -> type[Any]:
    """Import the official adapter lazily after the feature gate is enabled."""
    try:
        module = importlib.import_module("langchain_mcp_adapters.client")
        client_cls = getattr(module, "MultiServerMCPClient")
    except (ImportError, AttributeError):
        raise InteropMCPError(
            "external MCP adapter dependency is unavailable",
            code="adapter_unavailable",
        ) from None
    if not callable(client_cls):
        raise InteropMCPError(
            "external MCP adapter dependency is unavailable",
            code="adapter_unavailable",
        )
    return cast(type[Any], client_cls)


def _remote_name(tool_name: object, target_id: str) -> str | None:
    """Extract the original remote name from the official qualified name."""
    if not isinstance(tool_name, str):
        return None
    for prefix in (f"{target_id}__", f"{target_id}_"):
        if tool_name.startswith(prefix):
            remote_name = tool_name[len(prefix) :]
            return remote_name or None
    return None


def _has_remote_error(result: object) -> bool:
    """Recognize error-shaped results without inspecting peer text."""
    if isinstance(result, ToolMessage):
        return result.status == "error"
    if isinstance(result, Mapping):
        return result.get("isError") is True or result.get("is_error") is True
    return bool(
        getattr(result, "isError", False) or getattr(result, "is_error", False)
    )


def _temporary_tool(
    tool: BaseTool,
    *,
    target_id: str,
    remote_name: str,
) -> BaseTool:
    """Wrap one official executable with sanitized execution failures."""

    async def invoke(**arguments: Any) -> Any:
        """Delegate one invocation and reject remote error results."""
        try:
            result = await tool.ainvoke(arguments)
        except Exception:
            # The adapter's ``handle_tool_errors=False`` raises for MCP
            # ``isError=True``.  Do not expose its peer-derived exception text.
            raise _tool_error(target_id, remote_name) from None
        if _has_remote_error(result):
            raise _tool_error(target_id, remote_name)
        return result

    args_schema = getattr(tool, "args_schema", None)
    infer_schema = args_schema is None
    response_format: Literal["content", "content_and_artifact"] = cast(
        Literal["content", "content_and_artifact"],
        getattr(tool, "response_format", "content"),
    )
    if response_format not in {"content", "content_and_artifact"}:
        response_format = "content"
    metadata = getattr(tool, "metadata", None)
    return StructuredTool.from_function(
        coroutine=invoke,
        name=cast(str, getattr(tool, "name", remote_name)),
        description=getattr(tool, "description", "") or "",
        args_schema=args_schema,
        infer_schema=infer_schema,
        response_format=response_format,
        metadata=metadata,
        return_direct=bool(getattr(tool, "return_direct", False)),
    )


async def load_external_mcp_tools(
    target_id: str,
    *,
    registry: InteropRegistry,
    sensitive_config: SensitiveConfig | None = None,
    resolver: AsyncDNSResolver = resolve_host,
    _client_cls: type[Any] | None = None,
) -> tuple[BaseTool, ...]:
    """Load allowed executable tools for one operator target.

    Exactly one ``MultiServerMCPClient`` is constructed and asked for the
    named server.  This avoids the official client's all-target concurrent
    discovery path, where an unavailable peer can fail unrelated targets.
    """
    if not registry.enabled:
        return ()
    target = _resolve_target(target_id, registry)
    if isinstance(target, A2ATarget):
        raise _target_error("unsupported_transport", target.id)
    connection = build_mcp_connection(
        target,
        registry=registry,
        sensitive_config=sensitive_config,
        resolver=resolver,
    )
    client_cls = _client_cls or _load_official_client()
    try:
        client = client_cls(
            {target.id: connection},
            tool_name_prefix=True,
            handle_tool_errors=False,
        )
        discovered = await client.get_tools(server_name=target.id)
    except Exception:
        raise _target_error("discovery_failed", target.id) from None

    allowed = frozenset(target.allowed_tools)
    temporary: list[BaseTool] = []
    for tool in discovered:
        remote_name = _remote_name(getattr(tool, "name", None), target.id)
        if remote_name is None or remote_name not in allowed:
            continue
        temporary.append(
            _temporary_tool(
                tool,
                target_id=target.id,
                remote_name=remote_name,
            )
        )
    return tuple(temporary)


async def invoke_external_mcp_tool(
    target_id: str,
    remote_tool_name: str,
    arguments: Mapping[str, Any],
    *,
    registry: InteropRegistry,
    **options: Any,
) -> Any:
    """Invoke one allowlisted external tool without accepting a URL.

    Tools are loaded for this call only; no credential-bearing closure is
    retained after the returned coroutine completes.
    """
    target = _resolve_target(target_id, registry)
    if isinstance(target, A2ATarget):
        raise _target_error("unsupported_transport", target.id)
    if remote_tool_name not in target.allowed_tools:
        raise _target_error(
            "tool_not_allowed", target.id, tool_name=remote_tool_name
        )
    tools = await load_external_mcp_tools(
        target_id,
        registry=registry,
        sensitive_config=cast(
            SensitiveConfig | None, options.get("sensitive_config")
        ),
        resolver=cast(
            AsyncDNSResolver, options.get("resolver") or resolve_host
        ),
        _client_cls=cast(type[Any] | None, options.get("_client_cls")),
    )
    for tool in tools:
        if _remote_name(tool.name, target_id) == remote_tool_name:
            return await tool.ainvoke(dict(arguments))
    raise _target_error(
        "tool_unavailable", target.id, tool_name=remote_tool_name
    )


__all__ = [
    "InteropMCPError",
    "InteropMCPToolError",
    "build_mcp_connection",
    "invoke_external_mcp_tool",
    "load_external_mcp_tools",
]
