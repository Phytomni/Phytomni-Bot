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
from typing import Any

from .call_output import render_call_output
from .client import PhytomniMcpClient, server_command_from_target


def main() -> None:
    """Run the command-line client.

    Returns:
        None. Parsed command output is printed to stdout.
    """
    asyncio.run(_main())


async def _main() -> None:
    """Parse CLI arguments and run the selected command."""
    parser = _build_parser()
    args = parser.parse_args()
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
            return

        if args.command == "call":
            result = await client.call_tool(
                args.tool_name,
                _json_object(args.arguments),
            )
            print(render_call_output(result.formatted))
            return

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
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list-tools", help="List available MCP tools.")

    call_parser = subparsers.add_parser("call", help="Call one MCP tool.")
    call_parser.add_argument("tool_name", help="Name of the MCP tool to call.")
    call_parser.add_argument(
        "arguments",
        help="JSON object containing tool arguments.",
    )
    return parser


def _json_object(raw_value: str) -> dict[str, Any]:
    """Parse a JSON object from a CLI argument."""
    value = json.loads(raw_value)
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("arguments must be a JSON object")
    return value


if __name__ == "__main__":
    main()
