# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Regression tests for required Knowledge evidence at Analyst admission."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

import mcp_server_phytomni.agents.analyst.graph as analyst_graph
from mcp_server_phytomni.agents.analyst.graph import AnalystGraphMixin
from mcp_server_phytomni.config.defaults import AnalystConfig

pytestmark = pytest.mark.agent


def _host() -> SimpleNamespace:
    """Return the smallest host surface required by the Analyst node."""
    return SimpleNamespace(analyst_config=AnalystConfig())


def _retrieval_error() -> McpError:
    """Return the fixed upstream failure used by the retrieval seam."""
    return McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message="Knowledge retrieval temporarily unavailable",
        )
    )


@pytest.mark.asyncio
async def test_tool_retrieval_failure_stops_before_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A required tool lookup failure cannot become empty submit context."""
    submit = AsyncMock()
    events: list[str] = []

    async def fail_retrieve(**_kwargs: Any) -> Any:
        events.append("retrieve")
        raise _retrieval_error()

    monkeypatch.setattr(analyst_graph, "retrieve", fail_retrieve)
    state = {"extracted_tools": ["tool-a"]}

    async def run_pipeline() -> None:
        result = await AnalystGraphMixin.tool_retrieve_node(_host(), state)
        events.append("submit")
        await submit(result)

    with pytest.raises(
        McpError, match="Knowledge retrieval temporarily unavailable"
    ):
        await run_pipeline()

    assert events == ["retrieve"]
    submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_tool_retrieval_no_match_keeps_submit_path_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid empty result remains a normal, empty tool context."""

    async def no_match_retrieve(**_kwargs: Any) -> dict[str, Any]:
        return {
            "doc_list": [],
            "total": 0,
            "outcome": "no_match",
            "failures": [],
        }

    monkeypatch.setattr(analyst_graph, "retrieve", no_match_retrieve)

    result = await AnalystGraphMixin.tool_retrieve_node(
        _host(), {"extracted_tools": ["tool-a"]}
    )

    assert result == {
        "tool_usages": "[tool-a Usage START]\n[tool-a Usage END]\n\n\n"
    }


@pytest.mark.asyncio
async def test_tool_retrieval_cancellation_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation is not converted into a normal retrieval result."""

    async def cancel_retrieve(**_kwargs: Any) -> Any:
        raise asyncio.CancelledError

    monkeypatch.setattr(analyst_graph, "retrieve", cancel_retrieve)

    with pytest.raises(asyncio.CancelledError):
        await AnalystGraphMixin.tool_retrieve_node(
            _host(), {"extracted_tools": ["tool-a"]}
        )
