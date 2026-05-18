# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for MCP client lifecycle helpers.

Covers server target parsing for module and file targets, plus raw JSON/text
tool payload parsing used by the CLI client.
"""

import sys

import pytest

from mcp_client_phytomni.client import (
    _build_server_env,
    parse_tool_payload,
    server_command_from_target,
)

pytestmark = pytest.mark.unit


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


def test_build_server_env_forwards_license_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify PHYTOMNI_LICENSE_KEY reaches the spawned server child.

    Args:
        monkeypatch: Pytest fixture used to set the process env.
    """
    monkeypatch.setenv("PHYTOMNI_LICENSE_KEY", "cust-key-001")

    env = _build_server_env(None)

    assert env["PHYTOMNI_LICENSE_KEY"] == "cust-key-001"


def test_build_server_env_is_a_narrow_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify unrelated host variables are not leaked to the child.

    Args:
        monkeypatch: Pytest fixture used to set the process env.
    """
    monkeypatch.setenv("PHYTOMNI_LICENSE_KEY", "cust-key-001")
    monkeypatch.setenv("UNRELATED_HOST_SECRET", "do-not-forward")

    env = _build_server_env(None)

    assert "UNRELATED_HOST_SECRET" not in env
    assert env["PHYTOMNI_LICENSE_KEY"] == "cust-key-001"


def test_build_server_env_omits_unset_allowlisted_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify allowlisted names absent from os.environ stay absent.

    Args:
        monkeypatch: Pytest fixture used to clear the process env.
    """
    monkeypatch.delenv("PHYTOMNI_LICENSE_KEY", raising=False)
    monkeypatch.delenv("PHYTOMNI_TESTING", raising=False)

    env = _build_server_env(None)

    assert "PHYTOMNI_LICENSE_KEY" not in env
    assert "PHYTOMNI_TESTING" not in env


def test_build_server_env_explicit_mapping_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a caller-supplied env overrides the allowlisted value.

    Args:
        monkeypatch: Pytest fixture used to set the process env.
    """
    monkeypatch.setenv("PHYTOMNI_LICENSE_KEY", "from-process")

    env = _build_server_env({"PHYTOMNI_LICENSE_KEY": "from-caller"})

    assert env["PHYTOMNI_LICENSE_KEY"] == "from-caller"
