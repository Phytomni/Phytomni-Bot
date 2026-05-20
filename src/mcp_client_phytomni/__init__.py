# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Client helpers for interacting with the Phytomni MCP server.

The package re-exports `PhytomniMcpClient`, `PhytomniToolRouter`, response
models, command helpers, and tool-result deserialization helpers for
applications that call Phytomni MCP tools over stdio.
"""

from .client import (
    McpToolResponse,
    PhytomniMcpClient,
    PhytomniToolRouter,
    RoutedQueryResult,
    ServerCommand,
    ToolCallError,
    parse_tool_payload,
    server_command_from_target,
)
from .tool_result_formatters import (
    FormattedToolResult,
    format_tool_result,
)

__all__ = [
    "FormattedToolResult",
    "McpToolResponse",
    "PhytomniMcpClient",
    "PhytomniToolRouter",
    "RoutedQueryResult",
    "ServerCommand",
    "ToolCallError",
    "format_tool_result",
    "parse_tool_payload",
    "server_command_from_target",
]
