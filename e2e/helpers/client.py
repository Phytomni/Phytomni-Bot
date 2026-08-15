# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Stdio MCP client helpers shared by every live e2e test.

`make_client()` returns a `PhytomniMcpClient` configured to launch the
default `mcp_server_phytomni.server` module as a subprocess; tests use
it as an async context manager. `call_tool()` is a thin wrapper that
applies the suite's default per-call timeout so async-tool tests can
still override it when they need a longer window.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mcp_client_phytomni import (
    McpToolResponse,
    PhytomniMcpClient,
    server_command_from_target,
)

DEFAULT_SERVER_MODULE = "mcp_server_phytomni.server"
DEFAULT_TOOL_TIMEOUT_SECONDS = 600
DEFAULT_LONG_TIMEOUT_SECONDS = 1800


def make_client() -> PhytomniMcpClient:
    """Return a fresh client targeting the default server module.

    The returned object is an `async with`-compatible context manager
    that owns the server subprocess for its lifetime.

    Returns:
        Disconnected `PhytomniMcpClient` ready to be entered.
    """
    source_root = Path(__file__).resolve().parents[2] / "src"
    return PhytomniMcpClient(
        server_command_from_target(
            DEFAULT_SERVER_MODULE,
            env={"PYTHONPATH": str(source_root)},
        ),
    )


async def call_tool(
    client: PhytomniMcpClient,
    tool_name: str,
    arguments: Mapping[str, Any],
    *,
    timeout_seconds: int = DEFAULT_TOOL_TIMEOUT_SECONDS,
) -> McpToolResponse:
    """Call one MCP tool with the suite's default timeout.

    Args:
        client: Connected `PhytomniMcpClient`.
        tool_name: Public MCP tool name (e.g. ``"ChatAgent"``).
        arguments: JSON-schema-compatible payload.
        timeout_seconds: Per-call read timeout in seconds.

    Returns:
        Raw plus formatted MCP tool response.
    """
    return await client.call_tool(
        tool_name,
        arguments,
        read_timeout_seconds=timeout_seconds,
    )


def submit_timeout_seconds() -> int:
    """Return the per-call timeout used for asynchronous tool submission.

    Override with the ``PHYTOMNI_E2E_SUBMIT_TIMEOUT_SECONDS`` environment
    variable when running against a slow analysis platform.

    Returns:
        Timeout used by async tool submission calls.
    """
    override = os.environ.get("PHYTOMNI_E2E_SUBMIT_TIMEOUT_SECONDS")
    if override:
        return int(override)
    return DEFAULT_LONG_TIMEOUT_SECONDS
