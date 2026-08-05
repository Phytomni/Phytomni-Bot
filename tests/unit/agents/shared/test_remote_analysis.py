# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the typed remote-analysis submission seam."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace
from typing import Any

import pytest

from mcp_server_phytomni.agents.shared import remote_analysis
from mcp_server_phytomni.agents.shared.remote_analysis import (
    RemoteAnalysisRequest,
    RemoteAnalysisSubmissionError,
    submit_remote_analysis,
)

pytestmark = pytest.mark.unit


@pytest.fixture(name="remote_request")
def _remote_request() -> RemoteAnalysisRequest:
    """Return one domain-neutral remote-analysis request."""
    return RemoteAnalysisRequest(
        analysis_type="example_analysis",
        target_id="gene-1",
        output_dir="/obs/output",
        goal_description="Describe the target.",
        meta="Use the supplied evidence.",
        data_list={"expression.tsv": "expression data"},
        compute_resource="medium",
    )


async def test_request_is_frozen_and_projects_dispatch_shape(
    monkeypatch: pytest.MonkeyPatch,
    remote_request: RemoteAnalysisRequest,
) -> None:
    """The seam maps typed fields to the existing adapter request shape."""
    analyst_agent = object()
    config = object()
    sensitive_config = object()
    captured: dict[str, Any] = {}

    async def fake_submit(
        agent: Any,
        public_config: Any,
        secrets: Any,
        payload: dict[str, Any],
        *,
        is_polling: bool,
    ) -> dict[str, Any]:
        captured.update(
            agent=agent,
            config=public_config,
            sensitive_config=secrets,
            request=payload,
            is_polling=is_polling,
        )
        return {"task_id": "task-1", "output_dir": "/obs/output"}

    monkeypatch.setattr(
        remote_analysis, "submit_analyst_via_subgraph", fake_submit
    )

    result = await submit_remote_analysis(
        analyst_agent,
        config,
        sensitive_config,
        remote_request,
    )

    assert captured == {
        "agent": analyst_agent,
        "config": config,
        "sensitive_config": sensitive_config,
        "request": {
            "analysis_type": "example_analysis",
            "target_id": "gene-1",
            "output_dir": "/obs/output",
            "prompt_parts": (
                "Describe the target.",
                "Use the supplied evidence.",
                {"expression.tsv": "expression data"},
            ),
            "compute_resource": "medium",
            "output_dir_is_result_child": False,
        },
        "is_polling": False,
    }
    assert result["task_id"] == "task-1"

    with pytest.raises(FrozenInstanceError):
        setattr(remote_request, "analysis_type", "changed")


async def test_submission_rejects_missing_task_id(
    monkeypatch: pytest.MonkeyPatch,
    remote_request: RemoteAnalysisRequest,
) -> None:
    """A successful adapter response must expose a usable task id."""

    async def missing_task_id(*_: Any, **__: Any) -> dict[str, Any]:
        return {"output_dir": "/obs/output"}

    monkeypatch.setattr(
        remote_analysis, "submit_analyst_via_subgraph", missing_task_id
    )

    with pytest.raises(RemoteAnalysisSubmissionError, match="task_id"):
        await submit_remote_analysis(
            object(), object(), object(), remote_request
        )


async def test_submission_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    remote_request: RemoteAnalysisRequest,
) -> None:
    """Cancellation remains visible to graph task cleanup."""

    async def cancelled(*_: Any, **__: Any) -> dict[str, Any]:
        raise asyncio.CancelledError

    monkeypatch.setattr(
        remote_analysis, "submit_analyst_via_subgraph", cancelled
    )

    with pytest.raises(asyncio.CancelledError):
        await submit_remote_analysis(
            object(), object(), object(), remote_request
        )


@pytest.mark.parametrize(
    "output_dir",
    ("/obs/output", "", "/obs/run/children/part-000"),
)
async def test_flagged_request_requires_an_exact_child_directory(
    monkeypatch: pytest.MonkeyPatch,
    remote_request: RemoteAnalysisRequest,
    output_dir: str,
) -> None:
    """A flagged request cannot bypass the layout with an arbitrary path."""

    async def unexpected_submit(*_: Any, **__: Any) -> dict[str, Any]:
        raise AssertionError("invalid child request reached the adapter")

    monkeypatch.setattr(
        remote_analysis, "submit_analyst_via_subgraph", unexpected_submit
    )
    request = replace(
        remote_request,
        output_dir=output_dir,
        output_dir_is_result_child=True,
    )

    with pytest.raises(ValueError, match="result child"):
        await submit_remote_analysis(object(), object(), object(), request)
