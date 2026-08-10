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
from ...runtime.error_types import RemoteAnalysisSubmissionError
from ...runtime.result_run_layout import result_run_root_from_child
from ...runtime.submission_outcome import (
    AcceptedSubmission,
    RejectedSubmission,
)

__all__ = [
    "RemoteAnalysisPrompt",
    "RemoteAnalysisRequest",
    "ResearchGrantUse",
    "RemoteAnalysisSubmissionError",
    "REMOTE_FANOUT_ERRORS",
    "REMOTE_SUBMISSION_ERRORS",
    "accepted_submission",
    "rejected_submission",
    "submit_remote_analysis",
]


@dataclass(frozen=True, slots=True)
class RemoteAnalysisPrompt:
    """Prompt parts forwarded together to the Analyst adapter."""

    goal_description: str
    meta: str
    data_list: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ResearchGrantUse:
    """One opaque Research object grant used by a relay submission.

    The exact reference is carried only inside the private relay sidecar.  It
    is never copied into the Analyst job YAML or a public result projection.
    ``snapshot_digest`` binds the use to the snapshot that was validated
    before the child dispatch.
    """

    dataset_id: str
    exact_reference: str
    grant_id: str
    snapshot_digest: str

    def to_payload(self) -> dict[str, str]:
        """Return the bounded sidecar object shape."""
        return {
            "dataset_id": self.dataset_id,
            "exact_reference": self.exact_reference,
            "grant_id": self.grant_id,
            "snapshot_digest": self.snapshot_digest,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class _RemoteAnalysisOptions:
    """Optional identity and private grant data for one request."""

    dispatch_fingerprint: str | None = None
    research_grants: tuple[ResearchGrantUse, ...] = ()
    parent_run_id: str | None = None


@dataclass(frozen=True, slots=True)
class RemoteAnalysisRequest(_RemoteAnalysisOptions):
    """Immutable input required for one remote Analyst submission."""

    analysis_type: str
    target_id: str
    output_dir: str | None
    prompt: RemoteAnalysisPrompt
    compute_resource: str
    output_dir_is_result_child: bool = False
    obs_file_list: tuple[str, ...] = ()

    @property
    def goal_description(self) -> str:
        """Return the prompt goal through the historical read surface."""
        return self.prompt.goal_description

    @property
    def meta(self) -> str:
        """Return the prompt metadata through the historical read surface."""
        return self.prompt.meta

    @property
    def data_list(self) -> dict[str, Any]:
        """Return prompt datasets through the historical read surface."""
        return self.prompt.data_list

    def research_grant_sidecar(self) -> dict[str, Any] | None:
        """Build the private relay-only grant envelope when one is present.

        ``target_id`` is the compatibility fallback for callers created
        before the durable parent run was threaded into this seam.  New
        Research outbox callers pass ``parent_run_id`` explicitly.
        """
        if not self.research_grants:
            return None
        if not self.dispatch_fingerprint:
            raise RemoteAnalysisSubmissionError(
                "research grant sidecar requires dispatch fingerprint"
            )
        return {
            "schema_version": 1,
            "parent_run_id": self.parent_run_id or self.target_id,
            "execution_fingerprint": self.dispatch_fingerprint,
            "objects": [grant.to_payload() for grant in self.research_grants],
        }


REMOTE_SUBMISSION_ERRORS: tuple[type[Exception], ...] = (
    McpError,
    RemoteAnalysisSubmissionError,
    TimeoutError,
)

# Fan-out callers may convert documented upstream transport failures into a
# safe per-child rejection, but a missing task id is a local contract failure.
REMOTE_FANOUT_ERRORS: tuple[type[Exception], ...] = (McpError, TimeoutError)


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
    if request.output_dir_is_result_child:
        result_run_root_from_child(str(request.output_dir or ""))
    payload: dict[str, Any] = {
        "analysis_type": request.analysis_type,
        "target_id": request.target_id,
        "output_dir": request.output_dir,
        "prompt_parts": (
            request.goal_description,
            request.meta,
            request.data_list,
        ),
        "compute_resource": request.compute_resource,
        "output_dir_is_result_child": request.output_dir_is_result_child,
    }
    if request.dispatch_fingerprint is not None:
        payload["dispatch_fingerprint"] = request.dispatch_fingerprint
    if request.obs_file_list:
        payload["obs_file_list"] = list(request.obs_file_list)
    sidecar = request.research_grant_sidecar()
    if sidecar is not None:
        # This name is deliberately private and is consumed only by the
        # Analyst relay wrapper.  It is not part of any MCP request schema.
        payload["research_grant_sidecar"] = sidecar
    result = await submit_analyst_via_subgraph(
        analyst_agent,
        config,
        sensitive_config,
        payload,
        is_polling=is_polling,
    )
    accepted_submission(result)
    return result
