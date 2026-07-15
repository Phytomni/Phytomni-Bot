# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the HTTP-backed Phytomni CLI commands."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from mcp_client_phytomni import main as cli_main
from mcp_client_phytomni.http_client import (
    HttpClientError,
    RunSnapshot,
    SubmittedRun,
)
from mcp_client_phytomni.main import _build_parser, _main

pytestmark = pytest.mark.unit


class _StubHttpClient:
    """Async context manager standing in for the HTTP run client."""

    def __init__(
        self,
        *,
        snapshot: RunSnapshot | None = None,
        error: Exception | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.error = error
        self.submitted: tuple[str, dict[str, Any]] | None = None

    async def __aenter__(self) -> _StubHttpClient:
        """Return this stub for one CLI invocation."""
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: Any,
    ) -> None:
        """Leave the stub available for assertions."""
        return None

    async def submit(
        self,
        agent: str,
        arguments: dict[str, Any],
    ) -> SubmittedRun:
        """Record and return one accepted run."""
        if self.error is not None:
            raise self.error
        self.submitted = (agent, arguments)
        return SubmittedRun("run-1", ("task-1",), "running")

    async def get_run(self, run_id: str) -> RunSnapshot:
        """Return the configured snapshot or error."""
        if self.error is not None:
            raise self.error
        if self.snapshot is None:
            raise AssertionError("snapshot fixture missing")
        assert run_id == self.snapshot.run_id
        return self.snapshot


def _partial_snapshot(*, status: str = "running") -> RunSnapshot:
    """Build a minimal snapshot with the best available report."""
    return replace(
        RunSnapshot("run-1", status),
        intermediate_report="# partial report",
        report_revision=2,
        degraded=True,
        degraded_reason="1 of 12 optional analyses unavailable",
        progress={"total": 12, "running": 3},
    )


async def _run_http_cli(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    stub: _StubHttpClient,
) -> int:
    """Run the HTTP CLI with environment and client seams patched."""
    monkeypatch.setenv("PHYTOMNI_API_KEY", "top-secret")
    monkeypatch.setattr(
        cli_main,
        "PhytomniHttpClient",
        lambda *_args, **_kwargs: stub,
    )
    return await _main(argv)


async def test_submit_http_command_does_not_start_stdio_client(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Submit uses HTTP and prints the id/status on separate streams."""

    def stdio(*_args: Any, **_kwargs: Any) -> Any:
        """Fail if the HTTP command opens the stdio client."""
        raise AssertionError("stdio client must not start")

    monkeypatch.setattr(cli_main, "PhytomniMcpClient", stdio)
    stub = _StubHttpClient()

    code = await _run_http_cli(
        monkeypatch,
        [
            "--api-url",
            "https://bot.invalid",
            "submit",
            "deep_genome",
            '{"gene_id":"x"}',
        ],
        stub,
    )

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "run-1\n"
    assert "task_ids=task-1" in captured.err
    assert "status=running" in captured.err
    assert stub.submitted == ("deep_genome", {"gene_id": "x"})


async def test_status_keeps_report_on_stdout_and_reason_on_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Status keeps Markdown pipe-safe and metadata on stderr."""
    stub = _StubHttpClient(snapshot=_partial_snapshot())

    code = await _run_http_cli(
        monkeypatch,
        ["--api-url", "https://bot.invalid", "status", "run-1"],
        stub,
    )

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "# partial report\n"
    assert "status=running" in captured.err
    assert "revision=2" in captured.err
    assert "1 of 12 optional analyses unavailable" in captured.err


async def test_status_failed_returns_one_but_prints_intermediate_report(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Failed runs retain their best report while returning code one."""
    stub = _StubHttpClient(snapshot=_partial_snapshot(status="failed"))

    code = await _run_http_cli(
        monkeypatch,
        ["--api-url", "https://bot.invalid", "status", "run-1"],
        stub,
    )

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == "# partial report\n"
    assert "status=failed" in captured.err


async def test_http_cli_maps_client_errors_to_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Transport/protocol errors become a fixed nonzero CLI code."""
    stub = _StubHttpClient(error=HttpClientError("HTTP request failed"))

    code = await _run_http_cli(
        monkeypatch,
        ["--api-url", "https://bot.invalid", "status", "run-1"],
        stub,
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "error: HTTP request failed\n"


def test_parser_has_no_plaintext_api_key_option() -> None:
    """The parser never accepts a key that could enter argv/process lists."""
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["--api-key", "secret", "status", "run-1"])


def test_console_main_propagates_async_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The installed console wrapper preserves the async return code."""

    async def fake_main(_argv: list[str] | None = None) -> int:
        return 2

    monkeypatch.setattr(cli_main, "_main", fake_main)
    with pytest.raises(SystemExit) as excinfo:
        cli_main.main()
    assert excinfo.value.code == 2


def test_installed_status_without_key_exits_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing environment key fails before any network request."""
    monkeypatch.delenv("PHYTOMNI_API_KEY", raising=False)
    monkeypatch.setenv("PHYTOMNI_API_URL", "https://bot.invalid")

    assert asyncio.run(_main(["status", "run-1"])) == 2
