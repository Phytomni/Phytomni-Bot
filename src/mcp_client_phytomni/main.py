# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Command-line entrypoint for the Phytomni MCP client.

This module exposes `main` for the installed CLI. Private helpers build the
argument parser, parse JSON tool arguments, list MCP tools, and call a
selected tool through `PhytomniMcpClient`.
"""

import argparse
import asyncio
import json
import math
import os
import sys
import time
from collections.abc import Sequence
from typing import Any, NoReturn

from .call_output import render_call_output
from .client import PhytomniMcpClient, server_command_from_target
from .http_client import HttpClientError, PhytomniHttpClient, RunSnapshot


def main() -> NoReturn:
    """Run the command-line client.

    Returns:
        Never returns; exits with the asynchronous command's status code.
    """
    raise SystemExit(asyncio.run(_main()))


async def _main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and run the selected command.

    Args:
        argv: Optional argument sequence; ``None`` reads the process argv.

    Returns:
        Process-style exit code. HTTP command failures return ``2`` while
        failed remote runs return ``1``; existing stdio success paths return
        ``0``.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command in {"submit", "status", "follow"}:
        return await _run_http_command(args)

    command = server_command_from_target(args.server)
    async with PhytomniMcpClient(command) as client:
        if args.command == "list-tools":
            tools = [
                {
                    "name": str(getattr(tool.name, "value", tool.name)),
                    "description": tool.description,
                    "input_schema": tool.inputSchema,
                }
                for tool in await client.list_tools()
            ]
            print(json.dumps(tools, ensure_ascii=False, indent=2))
            return 0

        if args.command == "call":
            result = await client.call_tool(
                args.tool_name,
                _json_object(args.arguments),
            )
            print(render_call_output(result.formatted))
            return 0

    parser.error(f"Unsupported command: {args.command}")


def _build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        description="Call tools exposed by the Phytomni MCP server."
    )
    parser.add_argument(
        "--server",
        default="mcp_server_phytomni.server",
        help="Python file, JS file, or module name for the MCP server.",
    )
    parser.add_argument(
        "--api-url",
        default=None,
        help="HTTP API base URL (defaults to PHYTOMNI_API_URL).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list-tools", help="List available MCP tools.")

    call_parser = subparsers.add_parser("call", help="Call one MCP tool.")
    call_parser.add_argument("tool_name", help="Name of the MCP tool to call.")
    call_parser.add_argument(
        "arguments",
        help="JSON object containing tool arguments.",
    )

    submit_parser = subparsers.add_parser(
        "submit", help="Submit an asynchronous HTTP agent run."
    )
    submit_parser.add_argument("agent", help="HTTP agent slug.")
    submit_parser.add_argument(
        "arguments",
        help="JSON object containing agent arguments.",
    )

    status_parser = subparsers.add_parser(
        "status", help="Read one asynchronous HTTP run snapshot."
    )
    status_parser.add_argument("run_id", help="Owner-scoped HTTP run id.")

    follow_parser = subparsers.add_parser(
        "follow", help="Follow one asynchronous HTTP run to completion."
    )
    follow_parser.add_argument("run_id", help="Owner-scoped HTTP run id.")
    follow_parser.add_argument(
        "--poll-interval",
        type=float,
        default=5.0,
        help="Seconds between status reads (default: 5.0).",
    )
    follow_parser.add_argument(
        "--wait-timeout",
        type=float,
        default=3600.0,
        help="Maximum local wait in seconds (default: 3600.0).",
    )
    return parser


def _json_object(raw_value: str) -> dict[str, Any]:
    """Parse a JSON object from a CLI argument."""
    value = json.loads(raw_value)
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("arguments must be a JSON object")
    return value


async def _run_http_command(args: argparse.Namespace) -> int:
    """Dispatch one HTTP command without opening the stdio transport."""
    try:
        api_url, api_key = _resolve_http_config(args.api_url)
        if args.command == "follow" and (
            not _positive_number(args.poll_interval)
            or not _positive_number(args.wait_timeout)
        ):
            raise ValueError("poll interval and wait timeout must be positive")
        async with PhytomniHttpClient(api_url, api_key) as client:
            if args.command == "submit":
                submitted = await client.submit(
                    args.agent,
                    _json_object(args.arguments),
                )
                print(submitted.run_id)
                _print_submit_metadata(submitted.task_ids, submitted.status)
                return 0

            if args.command == "status":
                snapshot = await client.get_run(args.run_id)
                _print_snapshot(snapshot)
                return 1 if snapshot.status == "failed" else 0

            return await _follow_http_run(
                client,
                args.run_id,
                poll_interval=args.poll_interval,
                wait_timeout=args.wait_timeout,
            )
    except (HttpClientError, TypeError, ValueError) as exc:
        print(f"error: {_safe_cli_line(str(exc))}", file=sys.stderr)
        return 2


async def _follow_http_run(
    client: PhytomniHttpClient,
    run_id: str,
    *,
    poll_interval: float,
    wait_timeout: float,
) -> int:
    """Poll one run until terminal or the local monotonic deadline."""
    deadline = time.monotonic() + wait_timeout
    previous_key: tuple[str, int] | None = None
    last_snapshot: RunSnapshot | None = None
    while True:
        snapshot = await client.get_run(run_id)
        last_snapshot = snapshot
        progress_key = (snapshot.status, snapshot.report_revision)
        if progress_key != previous_key:
            _print_snapshot_metadata(snapshot)
            previous_key = progress_key
        if snapshot.status in {"input_required", "succeeded", "failed"}:
            _print_snapshot_report(snapshot)
            return 1 if snapshot.status == "failed" else 0

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _print_snapshot_report(last_snapshot)
            return 3
        await asyncio.sleep(min(poll_interval, remaining))


def _resolve_http_config(api_url: str | None) -> tuple[str, str]:
    """Resolve HTTP URL and key without ever exposing the key in argv."""
    resolved_url = api_url or os.environ.get("PHYTOMNI_API_URL")
    if not isinstance(resolved_url, str) or not resolved_url.strip():
        raise ValueError("PHYTOMNI_API_URL is required")
    api_key = os.environ.get("PHYTOMNI_API_KEY")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("PHYTOMNI_API_KEY is required")
    return resolved_url, api_key


def _print_submit_metadata(
    task_ids: Sequence[str], status: str | None
) -> None:
    """Print accepted task metadata on stderr, keeping stdout pipe-safe."""
    task_label = ",".join(_safe_cli_line(task_id) for task_id in task_ids)
    if not task_label:
        task_label = "none"
    fields = [f"task_ids={task_label}"]
    if status:
        fields.append(f"status={_safe_cli_line(status)}")
    print(" ".join(fields), file=sys.stderr)


def _print_snapshot(snapshot: RunSnapshot) -> None:
    """Print the best report to stdout and one progress line to stderr."""
    _print_snapshot_report(snapshot)
    _print_snapshot_metadata(snapshot)


def _print_snapshot_report(snapshot: RunSnapshot) -> None:
    """Print the best available report or a status line to stdout."""
    report = snapshot.final_report
    if not isinstance(report, str) or not report.strip():
        report = snapshot.intermediate_report
    if isinstance(report, str) and report.strip():
        sys.stdout.write(report)
        if not report.endswith("\n"):
            sys.stdout.write("\n")
    else:
        print(f"status={snapshot.status}")


def _print_snapshot_metadata(snapshot: RunSnapshot) -> None:
    """Print one status/progress/degradation line to stderr."""
    fields = [
        f"status={_safe_cli_line(snapshot.status)}",
        f"revision={snapshot.report_revision}",
    ]
    if snapshot.progress:
        progress = ",".join(
            f"{_safe_cli_line(key)}={_safe_cli_line(str(value))}"
            for key, value in snapshot.progress.items()
        )
        fields.append(f"progress={progress}")
    if snapshot.degraded_reason:
        fields.append(
            f"degraded_reason={_safe_cli_line(snapshot.degraded_reason)}"
        )
    print(" ".join(fields), file=sys.stderr)


def _positive_number(value: float) -> bool:
    """Return whether one CLI wait option is finite and strictly positive."""
    return math.isfinite(value) and value > 0


def _safe_cli_line(value: str) -> str:
    """Collapse untrusted metadata to a bounded single-line display value."""
    normalized = " ".join(value.split())
    return normalized[:256]


if __name__ == "__main__":
    main()
