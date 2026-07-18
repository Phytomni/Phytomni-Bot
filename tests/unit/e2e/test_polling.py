# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Offline contracts for the live E2E polling state model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import httpx
import pytest
from e2e.helpers import polling


def _state(**overrides: object) -> polling.TaskState:
    """Build one fully populated public polling state."""
    values: dict[str, object] = {
        "task_id": "task-1",
        "status": "running",
        "analysis_id": "analysis-1",
        "output_dir": "/obs/output",
        "intermediate_report": "# intermediate",
        "final_report": None,
        "report_stage": "intermediate",
        "report_completeness": "partial",
        "report_revision": 3,
        "report_updated_at": "2026-07-15T00:00:00Z",
        "progress": {"total": 12, "succeeded": 2},
        "degraded": True,
        "degraded_reason": "1 of 12 optional analyses unavailable",
        "brief_gene_status": "succeeded",
        "failures": (
            {
                "work_item_key": "design",
                "status": "failed",
                "message": "analysis task unavailable",
            },
        ),
        "artifacts": ({"output_dir": "/obs/output", "paths": ("/obs/a",)},),
        "output_dirs": ("/obs/output",),
    }
    values.update(overrides)
    return polling.TaskState(**cast(Any, values))


def test_task_state_carries_report_progress_and_artifact_contract() -> None:
    """The live helper must retain all public terminal-report fields."""
    state = _state()

    assert state.intermediate_report == "# intermediate"
    assert state.report_revision == 3
    assert state.progress["total"] == 12
    assert state.brief_gene_status == "succeeded"
    assert state.degraded is True
    assert state.failures[0]["status"] == "failed"
    assert state.artifacts[0]["paths"] == ("/obs/a",)
    assert state.output_dirs == ("/obs/output",)


def test_task_state_mapping_projects_report_and_failure_fields() -> None:
    """Mapping projects the sanitized public snapshot fields."""
    state = polling.task_state_from_mapping(
        {
            "task_id": "task-1",
            "status": "failed",
            "analysis_id": "",
            "output_dir": "",
            "intermediate_report": "# profile",
            "final_report": None,
            "report_stage": "intermediate",
            "report_completeness": "partial",
            "report_revision": 4,
            "report_updated_at": "2026-07-15T00:00:00Z",
            "progress": {"brief_gene_status": "succeeded"},
            "degraded": True,
            "degraded_reason": "1 of 12 optional analyses unavailable",
            "brief_gene_status": "succeeded",
            "failures": (),
            "artifacts": ({"output_dir": "/obs/report", "paths": ()},),
            "output_dirs": ("/obs/report",),
        },
        task_id="task-1",
    )

    assert state.status == "failed"
    assert state.intermediate_report == "# profile"
    assert state.report_revision == 4
    assert state.brief_gene_status == "succeeded"
    assert state.artifacts[0]["output_dir"] == "/obs/report"


@pytest.mark.asyncio
async def test_http_poll_records_distinct_monotonic_revisions() -> None:
    """HTTP polling records revisions without retaining response bodies."""

    @dataclass(frozen=True)
    class Response:
        """Frozen JSON response record for the polling helper."""

        body: dict[str, object]
        status_code: int = 200

        def json(self) -> dict[str, object]:
            """Return the stored JSON body."""
            return self.body

    class Client:
        """Minimal async HTTP client fake with two status responses."""

        def __init__(self) -> None:
            """Prepare running and terminal responses."""
            self.paths: list[str] = []
            self.responses = iter(
                (
                    Response(
                        {
                            "status": "running",
                            "result": {"report_revision": 1},
                        }
                    ),
                    Response(
                        {
                            "status": "succeeded",
                            "result": {
                                "report_revision": 2,
                                "final_report": "# final",
                            },
                        }
                    ),
                )
            )

        async def get(
            self, _path: str, *, headers: dict[str, str]
        ) -> Response:
            """Return the next prepared response."""
            self.paths.append(_path)
            del headers
            return next(self.responses)

    client = Client()
    terminal = await polling.poll_http_run_to_terminal(
        cast(httpx.AsyncClient, client),
        "run-1",
        headers={"X-Service-Token": "test"},
        timeout_seconds=1.0,
        poll_interval_seconds=0.0,
    )

    assert terminal.status == "succeeded"
    assert terminal.revisions == (1, 2)
    assert terminal.result["final_report"] == "# final"
    assert client.paths == ["/v1/runs/run-1", "/v1/runs/run-1"]
