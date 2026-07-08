# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Async client helpers for calling Phytomni MCP tools.

This module provides command parsing helpers, raw and formatted response
models, `PhytomniMcpClient` for stdio MCP sessions, and
`PhytomniToolRouter` for OpenAI-assisted tool selection.
"""

import json
import os
import sys
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client
from mcp.types import Tool
from openai import AsyncOpenAI

from .tool_result_formatters import (
    FormattedToolResult,
    parse_formatted_result,
)

DEFAULT_SERVER_MODULE = "mcp_server_phytomni.server"
DEFAULT_TOOL_TIMEOUT_SECONDS = 36000

# Environment variables forwarded from the current process into the
# spawned MCP server child, layered on top of the MCP SDK's minimal
# default environment. Deliberately a narrow allowlist: the
# encrypted-.env distribution path needs PHYTOMNI_LICENSE_KEY to reach
# the server (the MCP stdio transport otherwise sanitizes it away),
# and PHYTOMNI_TESTING must propagate so spawned-server test runs stay
# offline. A blanket os.environ passthrough is intentionally NOT done:
# it would defeat the transport's deliberate environment sanitization
# and leak unrelated host variables into the child.
_FORWARDED_ENV_VARS = ("PHYTOMNI_LICENSE_KEY", "PHYTOMNI_TESTING")


class ToolCallError(RuntimeError):
    """Raised when an MCP tool returns an error result."""


@dataclass(frozen=True)
class ServerCommand:
    """Command used to start an MCP server subprocess.

    Attributes:
        command: Executable used to launch the server process.
        args: Arguments passed to the executable.
        env: Optional environment mapping for the server subprocess.
    """

    command: str
    args: tuple[str, ...]
    env: Mapping[str, str] | None = None


@dataclass(frozen=True)
class McpToolResponse:
    """Raw and formatted response from one MCP tool call.

    Attributes:
        tool_name: Public MCP tool name that was called.
        arguments: Tool arguments sent to the server.
        raw_text: Text content returned by the MCP server.
        raw_payload: JSON-decoded payload when decoding succeeds.
        formatted: Client-facing normalized response.
    """

    tool_name: str
    arguments: Mapping[str, Any]
    raw_text: str
    raw_payload: Any
    formatted: FormattedToolResult


@dataclass(frozen=True)
class RoutedQueryResult:
    """Result returned after optional LLM tool routing.

    Attributes:
        answer: Final answer shown to the client user.
        tool_response: MCP tool response when a tool was selected.
        follow_up_questions: Suggested follow-up questions from the tool.
    """

    answer: str
    tool_response: McpToolResponse | None = None
    follow_up_questions: tuple[str, ...] = ()


def server_command_from_target(
    target: str = DEFAULT_SERVER_MODULE,
    *,
    python_executable: str = sys.executable,
    env: Mapping[str, str] | None = None,
) -> ServerCommand:
    """Build a server command from a Python file, JS file, or module name.

    Args:
        target: Python file, JavaScript file, or Python module name to run.
        python_executable: Python executable used for Python targets.
        env: Optional environment mapping for the server process.

    Returns:
        Server command suitable for `PhytomniMcpClient`.
    """
    if target.endswith(".py"):
        return ServerCommand(python_executable, (target,), env)
    if target.endswith(".js"):
        return ServerCommand("node", (target,), env)
    return ServerCommand(python_executable, ("-m", target), env)


def _build_server_env(
    explicit: Mapping[str, str] | None,
) -> dict[str, str]:
    """Compose the environment for the spawned MCP server child.

    Starts from the MCP SDK's minimal safe default environment, layers
    the narrow phytomni allowlist read from the current process, then
    applies any caller-supplied explicit mapping last so an explicit
    env always wins. The whole ``os.environ`` is never forwarded.

    Args:
        explicit: Optional caller-provided environment mapping.

    Returns:
        Environment mapping passed to ``StdioServerParameters``.
    """
    env: dict[str, str] = dict(get_default_environment())
    for name in _FORWARDED_ENV_VARS:
        value = os.environ.get(name)
        if value is not None:
            env[name] = value
    if explicit is not None:
        env.update(explicit)
    return env


def parse_tool_payload(text: str) -> Any:
    """Parse MCP text content into JSON when possible.

    Args:
        text: Raw text content returned by an MCP tool.

    Returns:
        JSON-decoded content when possible, otherwise the original text.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


class PhytomniMcpClient:
    """Manage a stdio MCP session with the Phytomni server.

    Attributes:
        command: Server subprocess command used by `connect`.
        field_mapper: Deprecated and ignored; the MCP server now
            formats tool output upstream.
        reference_resolver: Deprecated and ignored; the MCP server now
            formats tool output upstream.
        session: Active MCP client session after `connect` succeeds.
    """

    def __init__(
        self,
        command: ServerCommand | None = None,
        *,
        field_mapper: Any = None,
        reference_resolver: Any = None,
    ) -> None:
        self.command = command or server_command_from_target()
        self.field_mapper = field_mapper
        self.reference_resolver = reference_resolver
        self._exit_stack = AsyncExitStack()
        self.session: ClientSession | None = None

    async def __aenter__(self) -> "PhytomniMcpClient":
        """Connect the MCP session for context-manager use.

        Returns:
            The connected client instance.
        """
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: Any,
    ) -> None:
        """Close the MCP session when leaving a context manager.

        Args:
            exc_type: Exception type raised inside the context, if any.
            exc: Exception instance raised inside the context, if any.
            traceback: Traceback raised inside the context, if any.

        Returns:
            None. Owned transports are closed before returning.
        """
        del exc_type, exc, traceback
        await self.close()

    async def connect(self) -> None:
        """Start the configured MCP server and initialize a client session.

        Returns:
            None. The active session is stored on `session`.
        """
        if self.session is not None:
            return

        server_params = StdioServerParameters(
            command=self.command.command,
            args=list(self.command.args),
            env=_build_server_env(self.command.env),
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
        """Compatibility wrapper for older callers.

        Args:
            server_script_path: Python file, JavaScript file, or module name
                passed through `server_command_from_target`.

        Returns:
            None. The client connects using the resolved command.
        """
        self.command = server_command_from_target(server_script_path)
        await self.connect()

    async def list_tools(self) -> tuple[Tool, ...]:
        """Return tools exposed by the connected MCP server.

        Returns:
            Tuple of MCP tool definitions reported by the server.
        """
        session = self._require_session()
        response = await session.list_tools()
        return tuple(response.tools)

    async def openai_tools(self) -> list[dict[str, Any]]:
        """Return MCP tools in OpenAI Chat Completions tool format.

        Returns:
            List of OpenAI function-tool dictionaries derived from MCP tools.
        """
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
        progress_callback: Any = None,
    ) -> McpToolResponse:
        """Call one MCP tool and return raw plus formatted output.

        Args:
            tool_name: Public MCP tool name to call.
            arguments: JSON-schema-compatible arguments for the tool.
            read_timeout_seconds: Timeout used by the MCP client call.
            progress_callback: Optional async callback
                ``(progress, total, message)`` invoked per server
                progress notification. Forwarded to the MCP SDK's
                ``ClientSession.call_tool``.

        Returns:
            Raw MCP response plus a normalized formatted representation.

        Raises:
            ToolCallError: If the MCP tool returns an error result.
        """
        session = self._require_session()
        result = await session.call_tool(
            tool_name,
            dict(arguments),
            read_timeout_seconds=timedelta(seconds=read_timeout_seconds),
            progress_callback=progress_callback,
        )
        raw_text = _result_text(result)
        if result.isError:
            raise ToolCallError(raw_text)

        raw_payload = parse_tool_payload(raw_text)
        formatted = parse_formatted_result(raw_payload)
        return McpToolResponse(
            tool_name=tool_name,
            arguments=dict(arguments),
            raw_text=raw_text,
            raw_payload=raw_payload,
            formatted=formatted,
        )

    async def close(self) -> None:
        """Close all transports owned by the client.

        Returns:
            None. The stored session reference is cleared.
        """
        await self._exit_stack.aclose()
        self.session = None

    def _require_session(self) -> ClientSession:
        """Return the active session or raise a clear lifecycle error."""
        if self.session is None:
            raise RuntimeError("MCP client is not connected")
        return self.session


class PhytomniToolRouter:
    """Route natural-language queries to MCP tools with an OpenAI model.

    Attributes:
        mcp_client: Connected MCP client used to list and call tools.
        openai_client: OpenAI-compatible async client used for routing.
        model: Model identifier used for routing completions.
    """

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
        """Create a router from client-specific environment variables.

        Args:
            mcp_client: Connected MCP client used by the router.
            api_key_var: Environment variable containing the OpenAI API key.
            base_url_var: Environment variable containing the optional base
                URL for an OpenAI-compatible endpoint.
            model_var: Environment variable containing the routing model ID.

        Returns:
            Router configured with an `AsyncOpenAI` client.

        Raises:
            RuntimeError: If required API key or model variables are missing.
        """
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
        """Route a query through the model, then call the selected MCP tool.

        Args:
            query: Natural-language user query to route.
            history: Prior chat messages included before the current query.
            forced_tool: Optional MCP tool name to force through OpenAI
                `tool_choice`.

        Returns:
            Answer text with optional MCP tool response and follow-up
            questions.

        Raises:
            ToolCallError: If OpenAI returns an unsupported custom tool call
                or the selected MCP tool reports an error.
        """
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
