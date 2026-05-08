# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for MCP client lifecycle helpers.

Covers server target parsing for module and file targets, plus raw JSON/text
tool payload parsing used by the CLI client.
"""

import sys

from mcp_client_phytomni.client import (
    parse_tool_payload,
    server_command_from_target,
)


def test_server_command_from_module_target() -> None:
    """Verify module targets use the current Python executable."""
    command = server_command_from_target("mcp_server_phytomni.server")

    assert command.command == sys.executable
    assert command.args == ("-m", "mcp_server_phytomni.server")


def test_server_command_from_python_file_target() -> None:
    """Verify Python file targets are run directly."""
    command = server_command_from_target(
        "server.py", python_executable="python"
    )

    assert command.command == "python"
    assert command.args == ("server.py",)


def test_parse_tool_payload_keeps_non_json_text() -> None:
    """Verify non-JSON tool output remains available as raw text."""
    assert parse_tool_payload("plain text") == "plain text"
    assert parse_tool_payload('{"ok": true}') == {"ok": True}
