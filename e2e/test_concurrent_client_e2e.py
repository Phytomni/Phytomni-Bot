# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Concurrent regression smoke for five chat-like agents over MCP stdio.

Submits the committed demo payloads for ChatAgent, KnowledgeAgent,
DataAgent, ReviewAgent, and BriefGeneAgent in parallel through one
``PhytomniMcpClient`` session via ``asyncio.gather``, then asserts
each response with its agent-specific validator from
``helpers/assertions.py``. The MCP stdio protocol multiplexes the
five calls over a single subprocess (one request id per call), so
the wall-clock cost approaches ``max(per-agent latency)`` instead of
the sequential sum.

Intended use: a single ``pytest e2e/test_concurrent_client_e2e.py``
invocation before/after a large refactor of the MCP dispatch seam,
result formatter, or agent layering. Returns an aggregated per-agent
PASS/FAIL summary even when some calls error so one bad agent does
not mask the rest.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import pytest

from mcp_client_phytomni import McpToolResponse, PhytomniMcpClient

from .helpers.assertions import (
    assert_brief_gene_answer,
    assert_chat_answer,
    assert_data_answer,
    assert_knowledge_answer,
    assert_review_answer,
)
from .helpers.client import call_tool

pytestmark = pytest.mark.live

# (tool_name, payload filename, validator) for every chat-like agent
# exercised by this concurrent smoke. The same tuple set is consumed
# by the HTTP-side counterpart so client/HTTP coverage stays paired.
AGENT_CASES: tuple[tuple[str, str, Callable[[str], None]], ...] = (
    ("ChatAgent", "chat_agent.json", assert_chat_answer),
    ("KnowledgeAgent", "knowledge_agent.json", assert_knowledge_answer),
    ("DataAgent", "data_agent.json", assert_data_answer),
    ("ReviewAgent", "review_agent.json", assert_review_answer),
    ("BriefGeneAgent", "brief_gene_agent.json", assert_brief_gene_answer),
)


async def _invoke_and_validate(
    client: PhytomniMcpClient,
    tool_name: str,
    payload: Mapping[str, Any],
    validator: Callable[[str], None],
) -> McpToolResponse:
    """Call one MCP tool and validate its answer through ``validator``.

    Args:
        client: Shared connected MCP client.
        tool_name: Public MCP tool name (e.g. ``"ChatAgent"``).
        payload: Pre-rewritten arguments dict from ``load_payload``.
        validator: Agent-specific assertion from ``helpers/assertions``.

    Returns:
        The raw plus formatted MCP tool response on success.

    Raises:
        AssertionError: Propagated from the per-agent validator.
    """
    response = await call_tool(client, tool_name, payload)
    validator(response.formatted.answer)
    return response


async def test_concurrent_client_e2e_five_chat_like_agents(
    mcp_client: PhytomniMcpClient,
    load_payload: Callable[[str], dict[str, Any]],
) -> None:
    """Five chat-like agents pass concurrently over one MCP stdio session.

    Args:
        mcp_client: Session-scoped connected MCP client.
        load_payload: Loader that returns each rewritten payload.
    """
    coros: list[Awaitable[McpToolResponse]] = [
        _invoke_and_validate(
            mcp_client, tool_name, load_payload(payload_name), validator
        )
        for tool_name, payload_name, validator in AGENT_CASES
    ]

    results = await asyncio.gather(*coros, return_exceptions=True)

    failures = [
        (case[0], result)
        for case, result in zip(AGENT_CASES, results)
        if isinstance(result, BaseException)
    ]
    assert not failures, "\n".join(
        f"{tool_name}: {exc!r}" for tool_name, exc in failures
    )
