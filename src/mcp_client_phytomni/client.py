# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Async client helpers for calling Phytomni MCP tools."""

import json
import os
import sys
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool
from openai import AsyncOpenAI

from .tool_result_formatters import (
    FieldMapper,
    FormattedToolResult,
    ReferenceResolver,
    format_tool_result,
)

DEFAULT_SERVER_MODULE = "mcp_server_phytomni.server"
DEFAULT_TOOL_TIMEOUT_SECONDS = 36000


class ToolCallError(RuntimeError):
    """Raised when an MCP tool returns an error result."""


@dataclass(frozen=True)
class ServerCommand:
    """Command used to start an MCP server subprocess."""

    command: str
    args: tuple[str, ...]
    env: Mapping[str, str] | None = None


@dataclass(frozen=True)
class McpToolResponse:
    """Raw and formatted response from one MCP tool call."""

    tool_name: str
    arguments: Mapping[str, Any]
    raw_text: str
    raw_payload: Any
    formatted: FormattedToolResult


@dataclass(frozen=True)
class RoutedQueryResult:
    """Result returned after optional LLM tool routing."""

    answer: str
    tool_response: McpToolResponse | None = None
    follow_up_questions: tuple[str, ...] = ()


def server_command_from_target(
    target: str = DEFAULT_SERVER_MODULE,
    *,
    python_executable: str = sys.executable,
    env: Mapping[str, str] | None = None,
) -> ServerCommand:
    """Build a server command from a Python file, JS file, or module name."""
    if target.endswith(".py"):
        return ServerCommand(python_executable, (target,), env)
    if target.endswith(".js"):
        return ServerCommand("node", (target,), env)
    return ServerCommand(python_executable, ("-m", target), env)


def parse_tool_payload(text: str) -> Any:
    """Parse MCP text content into JSON when possible."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


class PhytomniMcpClient:
    """Manage a stdio MCP session with the Phytomni server."""

    def __init__(
        self,
        command: ServerCommand | None = None,
        *,
        field_mapper: FieldMapper | None = None,
        reference_resolver: ReferenceResolver | None = None,
    ) -> None:
        self.command = command or server_command_from_target()
        self.field_mapper = field_mapper
        self.reference_resolver = reference_resolver
        self._exit_stack = AsyncExitStack()
        self.session: ClientSession | None = None

    async def __aenter__(self) -> "PhytomniMcpClient":
        """Connect the MCP session for context-manager use."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: Any,
    ) -> None:
        """Close the MCP session when leaving a context manager."""
        del exc_type, exc, traceback
        await self.close()

    async def connect(self) -> None:
        """Start the configured MCP server and initialize a client session."""
        if self.session is not None:
            return

        server_params = StdioServerParameters(
            command=self.command.command,
            args=list(self.command.args),
            env=(
                dict(self.command.env)
                if self.command.env is not None
                else None
            ),
        )
        stdio_transport = await self._exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        read_stream, write_stream = stdio_transport
        self.session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await self.session.initialize()

    async def connect_to_server(self, server_script_path: str) -> None:
        """Compatibility wrapper for older callers."""
        self.command = server_command_from_target(server_script_path)
        await self.connect()

    async def list_tools(self) -> tuple[Tool, ...]:
        """Return tools exposed by the connected MCP server."""
        session = self._require_session()
        response = await session.list_tools()
        return tuple(response.tools)

    async def openai_tools(self) -> list[dict[str, Any]]:
        """Return MCP tools in OpenAI Chat Completions tool format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": _tool_name(tool),
                    "description": tool.description,
                    "parameters": tool.inputSchema,
                },
            }
            for tool in await self.list_tools()
        ]

    async def call_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        read_timeout_seconds: int = DEFAULT_TOOL_TIMEOUT_SECONDS,
    ) -> McpToolResponse:
        """Call one MCP tool and return raw plus formatted output."""
        session = self._require_session()
        result = await session.call_tool(
            tool_name,
            dict(arguments),
            read_timeout_seconds=timedelta(seconds=read_timeout_seconds),
        )
        raw_text = _result_text(result)
        if result.isError:
            raise ToolCallError(raw_text)

        raw_payload = parse_tool_payload(raw_text)
        formatted = format_tool_result(
            tool_name,
            raw_payload,
            arguments=arguments,
            field_mapper=self.field_mapper,
            reference_resolver=self.reference_resolver,
        )
        return McpToolResponse(
            tool_name=tool_name,
            arguments=dict(arguments),
            raw_text=raw_text,
            raw_payload=raw_payload,
            formatted=formatted,
        )

    async def close(self) -> None:
        """Close all transports owned by the client."""
        await self._exit_stack.aclose()
        self.session = None

    def _require_session(self) -> ClientSession:
        """Return the active session or raise a clear lifecycle error."""
        if self.session is None:
            raise RuntimeError("MCP client is not connected")
        return self.session


class PhytomniToolRouter:
    """Route natural-language queries to MCP tools with an OpenAI model."""

    def __init__(
        self,
        mcp_client: PhytomniMcpClient,
        *,
        openai_client: AsyncOpenAI,
        model: str,
    ) -> None:
        self.mcp_client = mcp_client
        self.openai_client = openai_client
        self.model = model

    @classmethod
    def from_env(
        cls,
        mcp_client: PhytomniMcpClient,
        *,
        api_key_var: str = "OPENAI_API_KEY_CLIENT",
        base_url_var: str = "BASE_URL_CLIENT",
        model_var: str = "MODEL_CLIENT",
    ) -> "PhytomniToolRouter":
        """Create a router from client-specific environment variables."""
        api_key = os.getenv(api_key_var)
        model = os.getenv(model_var)
        if not api_key:
            raise RuntimeError(
                f"Missing required environment variable {api_key_var}"
            )
        if not model:
            raise RuntimeError(
                f"Missing required environment variable {model_var}"
            )
        return cls(
            mcp_client,
            openai_client=AsyncOpenAI(
                api_key=api_key,
                base_url=os.getenv(base_url_var) or None,
            ),
            model=model,
        )

    async def route_query(
        self,
        query: str,
        *,
        history: Sequence[Mapping[str, Any]] = (),
        forced_tool: str | None = None,
    ) -> RoutedQueryResult:
        """Route a query through the model, then call the selected MCP tool."""
        messages = [dict(message) for message in history]
        messages.append({"role": "user", "content": query})

        completion = await self.openai_client.chat.completions.create(
            model=self.model,
            messages=cast(Any, messages),
            tools=cast(Any, await self.mcp_client.openai_tools()),
            tool_choice=_tool_choice(forced_tool),
        )
        ai_message = completion.choices[0].message
        tool_calls = ai_message.tool_calls or []
        if not tool_calls:
            return RoutedQueryResult(answer=ai_message.content or "")

        tool_call = tool_calls[0]
        function = getattr(tool_call, "function", None)
        if function is None:
            raise ToolCallError("Custom OpenAI tool calls are not supported")

        arguments = _tool_arguments(str(function.arguments))
        tool_response = await self.mcp_client.call_tool(
            str(function.name),
            arguments,
        )
        return RoutedQueryResult(
            answer=tool_response.formatted.answer,
            tool_response=tool_response,
            follow_up_questions=tool_response.formatted.follow_up_questions,
        )


def _tool_name(tool: Tool) -> str:
    """Return an MCP tool name as a plain string."""
    value = getattr(tool.name, "value", tool.name)
    return str(value)


def _result_text(result: Any) -> str:
    """Return joined text content from an MCP call result."""
    text_parts = [
        str(text)
        for item in getattr(result, "content", [])
        if (text := getattr(item, "text", None)) is not None
    ]
    return "\n".join(text_parts)


def _tool_choice(tool_name: str | None) -> Any:
    """Return an OpenAI tool_choice value for an optional forced tool."""
    if tool_name is None:
        return None
    return {"type": "function", "function": {"name": tool_name}}


def _tool_arguments(raw_arguments: str) -> Mapping[str, Any]:
    """Parse tool-call arguments as a JSON object."""
    parsed = json.loads(raw_arguments or "{}")
    if not isinstance(parsed, Mapping):
        raise ValueError("Tool call arguments must be a JSON object")
    return parsed
