# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Neutral state builders for cross-agent analysis failure tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

__all__ = [
    "analysis_node_state",
    "assert_partial_submission_outcome",
    "install_async_failure",
    "install_analysis_prompt_parts",
    "mixed_submission_state",
    "research_graph_state",
]


def assert_partial_submission_outcome(outcome: Any) -> None:
    """Assert the shared local-plus-A2A partial-submission contract."""
    assert outcome.kind == "partial"
    assert outcome.task_ids == ("local-task",)
    assert outcome.warnings[0]["rejected_count"] == 1


def install_async_failure(
    monkeypatch: pytest.MonkeyPatch,
    target: Any,
    attribute: str,
    error: Exception,
) -> None:
    """Install one async method failure for a node guard test."""
    monkeypatch.setattr(target, attribute, AsyncMock(side_effect=error))


def mixed_submission_state(local_state: dict[str, Any]) -> dict[str, Any]:
    """Build a local-acceptance plus unresolved-A2A state fragment."""
    return {
        **local_state,
        "phytomni_state": {
            "a2a_pending": [{"task_id": "peer-task"}],
        },
    }


def analysis_node_state(**fields: Any) -> dict[str, Any]:
    """Build the common indexed state prefix for an analysis worker."""
    return {"task_index": 0, **fields}


def install_analysis_prompt_parts(
    monkeypatch: pytest.MonkeyPatch,
    target: Any,
) -> None:
    """Install the deterministic prompt tuple used by dispatch guards."""
    monkeypatch.setattr(
        target,
        "_analysis_prompt_parts",
        lambda *_args: ("goal", "meta", {}),
    )


def research_graph_state(
    *,
    paper_text: str,
    output_dir: str,
    locale: str | None = None,
) -> dict[str, Any]:
    """Build the stable initial state for a research graph invocation."""
    state: dict[str, Any] = {
        "paper_text": paper_text,
        "data_list": {},
        "user_id": "test-user",
        "obs_file_list": [],
        "output_dir": output_dir,
        "goals": [],
        "research_tasks": [],
        "task_ids": {},
        "completed_count": 0,
        "error": None,
    }
    if locale is not None:
        state["locale"] = locale
    return state
