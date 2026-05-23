# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Deserialize formatted Phytomni MCP tool responses.

The MCP server wraps every dispatch in a
``{"formatted": ..., "raw": ...}`` envelope; this module reconstructs
``FormattedToolResult`` from envelope or legacy flat payloads while
leaving ``raw`` for callers that want the full server body.
``format_tool_result`` is a backward-compatible shim that ignores
``tool_name`` and legacy keyword arguments.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from mcp_server_phytomni.mcp.result_formatting import FormattedToolResult


def format_tool_result(
    tool_name: str,
    payload: Any,
    *,
    arguments: Mapping[str, Any] | None = None,
    **_legacy: Any,
) -> FormattedToolResult:
    """Parse an already-formatted MCP payload (back-compat shim).

    The server formats tool output upstream; this only deserializes it.
    ``tool_name``, ``arguments`` and legacy ``field_mapper`` /
    ``reference_resolver`` keyword arguments are accepted for backward
    compatibility and ignored.

    Args:
        tool_name: Unused; kept for signature compatibility.
        payload: The formatted payload returned by the MCP server.
        arguments: Unused; kept for signature compatibility.
        **_legacy: Ignored legacy keyword arguments.

    Returns:
        The reconstructed client-facing result.
    """
    del tool_name, arguments, _legacy
    return parse_formatted_result(payload)


def parse_formatted_result(payload: Any) -> FormattedToolResult:
    """Reconstruct a FormattedToolResult from a formatted payload.

    Accepts three shapes:

    1. An already-built ``FormattedToolResult`` (returned unchanged).
    2. The envelope dict ``{"formatted": {...}, "raw": {...}}`` emitted
       by the current MCP server; the nested ``formatted`` block is
       parsed recursively and the sibling ``raw`` is left for callers
       that want the full server body.
    3. The legacy flat dict ``{"answer", "follow_up_questions",
       "metadata", "references"}`` from historical dumps or
       pre-envelope servers (backward compatibility).

    Args:
        payload: The formatted mapping emitted by the MCP server, or an
            already-built FormattedToolResult.

    Returns:
        The normalized client-facing result.
    """
    if isinstance(payload, FormattedToolResult):
        return payload
    if not isinstance(payload, Mapping):
        return FormattedToolResult(answer=str(payload))

    nested = payload.get("formatted")
    if isinstance(nested, Mapping):
        return parse_formatted_result(nested)

    metadata = payload.get("metadata")
    tabular = payload.get("tabular")
    return FormattedToolResult(
        answer=str(payload.get("answer", "")),
        follow_up_questions=tuple(
            str(question)
            for question in _list_sequence(payload.get("follow_up_questions"))
        ),
        metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
        references=_mapping_sequence(payload.get("references")),
        tabular=dict(tabular) if isinstance(tabular, Mapping) else None,
        output_dirs=tuple(
            str(entry) for entry in _list_sequence(payload.get("output_dirs"))
        ),
    )


def _list_sequence(value: Any) -> tuple[Any, ...]:
    """Return list-like items, excluding strings."""
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()
    return tuple(value)


def _mapping_sequence(value: Any) -> tuple[Mapping[str, Any], ...]:
    """Return a sequence containing only mapping items."""
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))
