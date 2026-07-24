# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for the thin terminal answer synthesizer."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.runtime import terminal_answer
from mcp_server_phytomni.runtime.terminal_answer import (
    AnswerSynthesizer,
    TerminalAnswerContext,
)

pytestmark = pytest.mark.unit


def _live(*statuses):
    """Build reconciled rows with the given statuses."""
    return [
        {"task_id": f"t{i}", "status": s, "output_dir": f"/obs/p/r{i}"}
        for i, s in enumerate(statuses)
    ]


def _artifacts(paths_per_task):
    """Build artifact descriptors with the given paths per task."""
    return [
        {"task_id": f"t{i}", "output_dir": f"/obs/p/r{i}", "paths": paths}
        for i, paths in enumerate(paths_per_task)
    ]


def _ctx(agent, status, live, artifacts, query):
    """Build a TerminalAnswerContext for the given inputs."""
    return TerminalAnswerContext(
        agent=agent,
        status=status,
        live=live,
        artifacts=artifacts,
        query=query,
    )


@pytest.mark.asyncio
async def test_succeeded_answer_lists_counts_dirs_and_figures():
    """A succeeded run names counts, output dirs, and figure files."""
    answer = await terminal_answer.synthesize_terminal_answer(
        _ctx(
            "research",
            "succeeded",
            _live("succeeded", "succeeded"),
            _artifacts(
                [
                    ["/obs/p/r0/fig1.png"],
                    ["/obs/p/r1/fig2.svg", "/obs/p/r1/t.csv"],
                ]
            ),
            "study FT in rice",
        )
    )
    assert "complete" in answer.lower()
    assert "2/2" in answer
    assert "study FT in rice" in answer
    assert "/obs/p/r0" in answer and "/obs/p/r1" in answer
    assert "fig1.png" in answer and "fig2.svg" in answer
    assert "t.csv" not in answer  # non-figure excluded from the figures line


@pytest.mark.asyncio
async def test_failed_answer_names_failure_and_keeps_succeeded_outputs():
    """A failed run reports the failure count and still lists products."""
    answer = await terminal_answer.synthesize_terminal_answer(
        _ctx(
            "network",
            "failed",
            _live("succeeded", "failed"),
            _artifacts([["/obs/p/r0/fig.png"]]),
            None,
        )
    )
    assert "failed" in answer.lower()
    assert "1/2" in answer  # 1 of 2 failed
    assert "/obs/p/r0" in answer  # succeeded output still listed


@pytest.mark.asyncio
async def test_none_query_omits_query_line():
    """No captured query means no Query line."""
    answer = await terminal_answer.synthesize_terminal_answer(
        _ctx(
            "design",
            "succeeded",
            _live("succeeded"),
            _artifacts([[]]),
            None,
        )
    )
    assert "Query:" not in answer


@pytest.mark.asyncio
async def test_custom_synthesizer_is_used():
    """An injected synthesizer overrides the thin default."""

    async def fake(context: TerminalAnswerContext) -> str:
        assert context.agent == "analyst"
        return "RICH REPORT"

    synthesizer: AnswerSynthesizer = fake

    answer = await terminal_answer.synthesize_terminal_answer(
        _ctx(
            "analyst",
            "succeeded",
            _live("succeeded"),
            _artifacts([[]]),
            None,
        ),
        synthesizer=synthesizer,
    )
    assert answer == "RICH REPORT"
