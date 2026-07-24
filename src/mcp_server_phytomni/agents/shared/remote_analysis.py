# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Typed, domain-neutral submission seam for remote Analyst work."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from mcp.shared.exceptions import McpError

from ...graphs.analyst_dispatch_adapters import submit_analyst_via_subgraph
from ...runtime.submission_outcome import (
    AcceptedSubmission,
    RejectedSubmission,
)

__all__ = [
    "RemoteAnalysisRequest",
    "RemoteAnalysisSubmissionError",
    "REMOTE_SUBMISSION_ERRORS",
    "accepted_submission",
    "rejected_submission",
    "submit_remote_analysis",
]


@dataclass(frozen=True, slots=True)
class RemoteAnalysisRequest:
    """Immutable input required for one remote Analyst submission."""

    analysis_type: str
    target_id: str
    output_dir: str | None
    goal_description: str
    meta: str
    data_list: dict[str, Any]
    compute_resource: str


class RemoteAnalysisSubmissionError(ValueError):
    """Raised when the remote submission does not return a task id."""


REMOTE_SUBMISSION_ERRORS: tuple[type[Exception], ...] = (
    McpError,
    RemoteAnalysisSubmissionError,
    TimeoutError,
)


def accepted_submission(result: Mapping[str, Any]) -> AcceptedSubmission:
    """Project one successful adapter result into a typed acceptance."""
    task_id = result.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise RemoteAnalysisSubmissionError(
            "remote analysis submission omitted task_id"
        )
    return AcceptedSubmission(
        task_id=task_id,
        output_dir=str(result.get("output_dir") or ""),
    )


def rejected_submission(goal: str, error: Exception) -> RejectedSubmission:
    """Project a documented upstream exception into a safe rejection code."""
    code = (
        "upstream_timeout"
        if isinstance(error, TimeoutError)
        else "upstream_rejected"
    )
    return RejectedSubmission(goal=goal, code=code)


async def submit_remote_analysis(
    analyst_agent: Any,
    config: Any,
    sensitive_config: Any,
    request: RemoteAnalysisRequest,
    *,
    is_polling: bool = False,
) -> dict[str, Any]:
    """Submit one typed request through the existing Analyst graph adapter.

    This seam only projects the immutable request into the adapter's legacy
    payload shape and validates the returned task id. Prompt selection,
    compute-resource selection, polling, and domain-specific orchestration
    remain with the caller or the adapter.

    Args:
        analyst_agent: Analyst graph instance owned by the caller.
        config: Public Analyst configuration forwarded unchanged.
        sensitive_config: Sensitive configuration forwarded unchanged.
        request: Domain-neutral remote-analysis request.
        is_polling: Whether the adapter should wait for terminal completion.
            Defaults to ``False`` so callers own remote polling explicitly.

    Returns:
        The adapter's projected submission result.

    Raises:
        RemoteAnalysisSubmissionError: If the adapter omits a non-blank task
            id.
        asyncio.CancelledError: Propagated unchanged for task cleanup.
    """
    payload = {
        "analysis_type": request.analysis_type,
        "target_id": request.target_id,
        "output_dir": request.output_dir,
        "prompt_parts": (
            request.goal_description,
            request.meta,
            request.data_list,
        ),
        "compute_resource": request.compute_resource,
    }
    result = await submit_analyst_via_subgraph(
        analyst_agent,
        config,
        sensitive_config,
        payload,
        is_polling=is_polling,
    )
    accepted_submission(result)
    return result
