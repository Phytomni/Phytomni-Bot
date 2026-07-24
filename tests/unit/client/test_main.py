# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the mcp_client_phytomni CLI entry point.

Pins ``_build_parser`` defaults, ``_json_object`` validation, and the
``list-tools`` / ``call`` dispatch paths inside ``_main`` by patching
``PhytomniMcpClient`` and ``server_command_from_target`` with stub
implementations. No subprocess is spawned.
"""

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace
from typing import Any

import pytest
from tests.support.asyncio_helpers import run_coroutine_on_owned_loop

from mcp_client_phytomni import main as cli_main
from mcp_client_phytomni.main import _build_parser, _json_object, _main, main
from mcp_client_phytomni.tool_result_formatters import FormattedToolResult

pytestmark = pytest.mark.unit


class _StubMcpClient:
    """Async context manager standing in for PhytomniMcpClient."""

    def __init__(
        self,
        *,
        tools: list[SimpleNamespace] | None = None,
        call_result: SimpleNamespace | None = None,
    ) -> None:
        self.tools = tools or []
        self.call_result = call_result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> _StubMcpClient:
        return self

    async def __aexit__(
        self,
        _exc_type: Any,
        _exc: Any,
        _tb: Any,
    ) -> None:
        return None

    async def list_tools(self) -> list[SimpleNamespace]:
        """Return the canned tool list."""
        return self.tools

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> SimpleNamespace:
        """Record the call and return the canned response."""
        self.calls.append((tool_name, arguments))
        if self.call_result is None:  # defensive: tests must supply one
            raise AssertionError("call_tool fixture missing")
        return self.call_result


def test_build_parser_defaults_to_phytomni_server_module() -> None:
    """The CLI parser defaults --server to the in-repo server module."""
    args = _build_parser().parse_args(["list-tools"])

    assert args.server == "mcp_server_phytomni.server"
    assert args.command == "list-tools"


def test_build_parser_call_subcommand_requires_tool_and_arguments() -> None:
    """The ``call`` subcommand requires both positional args."""
    args = _build_parser().parse_args(
        ["call", "ChatAgent", '{"user_query":"x"}']
    )

    assert args.command == "call"
    assert args.tool_name == "ChatAgent"
    assert args.arguments == '{"user_query":"x"}'


def test_json_object_parses_valid_object() -> None:
    """A JSON object decodes into a dict for downstream call_tool use."""
    assert _json_object('{"a": 1}') == {"a": 1}


def test_json_object_rejects_non_object_payload() -> None:
    """A JSON array (or any non-object) raises ArgumentTypeError.

    Pins the type-check that prevents bad CLI input from reaching
    ``client.call_tool`` as a list / scalar.
    """
    with pytest.raises(argparse.ArgumentTypeError):
        _json_object("[1, 2]")


def test_json_object_rejects_malformed_json() -> None:
    """Malformed JSON propagates as ``json.JSONDecodeError`` to argparse."""
    with pytest.raises(json.JSONDecodeError):
        _json_object("not-json")


@pytest.mark.asyncio
async def test_main_list_tools_prints_tool_metadata(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``list-tools`` prints a JSON array of name/description/inputSchema."""
    tool = SimpleNamespace(
        name="ChatAgent",
        description="General chat",
        inputSchema={"type": "object"},
    )
    stub = _StubMcpClient(tools=[tool])
    monkeypatch.setattr(cli_main, "PhytomniMcpClient", lambda _command: stub)
    monkeypatch.setattr(
        cli_main, "server_command_from_target", lambda target: target
    )
    monkeypatch.setattr("sys.argv", ["phytomni-mcp-client", "list-tools"])

    await _main()

    payload = json.loads(capsys.readouterr().out)
    assert payload == [
        {
            "name": "ChatAgent",
            "description": "General chat",
            "input_schema": {"type": "object"},
        }
    ]


@pytest.mark.asyncio
async def test_main_call_prints_formatted_answer(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``call`` invokes the chosen tool and prints the formatted answer."""
    response = SimpleNamespace(
        formatted=FormattedToolResult(answer="Hello, world.")
    )
    stub = _StubMcpClient(call_result=response)
    monkeypatch.setattr(cli_main, "PhytomniMcpClient", lambda _command: stub)
    monkeypatch.setattr(
        cli_main, "server_command_from_target", lambda target: target
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "phytomni-mcp-client",
            "call",
            "ChatAgent",
            '{"user_query":"hello"}',
        ],
    )

    await _main()

    assert capsys.readouterr().out.strip() == "Hello, world."
    assert stub.calls == [("ChatAgent", {"user_query": "hello"})]


@pytest.mark.asyncio
async def test_main_call_prints_tabular_block(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``call`` prints tabular TSV after the formatted answer."""
    response = SimpleNamespace(
        formatted=FormattedToolResult(
            answer="1 row x 2 columns",
            tabular={
                "headers": ["alias", "gene"],
                "rows": [["ACT2", "AT3G18780"]],
            },
        )
    )
    stub = _StubMcpClient(call_result=response)
    monkeypatch.setattr(cli_main, "PhytomniMcpClient", lambda _command: stub)
    monkeypatch.setattr(
        cli_main, "server_command_from_target", lambda target: target
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "phytomni-mcp-client",
            "call",
            "DataAgent",
            '{"user_query":"genes"}',
        ],
    )

    await _main()

    output = capsys.readouterr().out
    assert "alias" in output
    assert stub.calls == [("DataAgent", {"user_query": "genes"})]


def test_main_entry_runs_async_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``main()`` exits with the return code from ``asyncio.run(_main())``."""
    captured: dict[str, Any] = {}

    async def fake_main(_argv: list[str] | None = None) -> int:
        captured["ran"] = True
        return 0

    run_calls: list[object] = []

    def fake_run(coro: Any) -> int:
        """Run and close the handed-off coroutine on an owned event loop."""
        run_calls.append(coro)
        return run_coroutine_on_owned_loop(coro)

    monkeypatch.setattr(cli_main, "_main", fake_main)
    monkeypatch.setattr(cli_main.asyncio, "run", fake_run)

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert captured == {"ran": True}
    assert len(run_calls) == 1
    assert excinfo.value.code == 0
