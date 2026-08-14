# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Offline contracts for the live E2E polling state model."""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from e2e.helpers import polling
from tests.unit.e2e.state_fakes import build_task_state


class _TimeoutCapturingClient:
    """Minimal polling client with observable per-request timeouts."""

    def __init__(self, responses: tuple[dict[str, object], ...]) -> None:
        """Prepare response bodies and an empty timeout capture."""
        self._responses = iter(responses)
        self._timeouts: list[float] = []

    async def get(
        self,
        _path: str,
        **request_options: object,
    ) -> httpx.Response:
        """Return the next response after capturing its read timeout."""
        self._timeouts.append(cast(float, request_options["timeout"]))
        return httpx.Response(200, json=next(self._responses))

    @property
    def timeout(self) -> float | None:
        """Return the most recently captured timeout, if any."""
        return self._timeouts[-1] if self._timeouts else None

    @property
    def timeouts(self) -> list[float]:
        """Return captured request timeouts in call order."""
        return list(self._timeouts)


def test_task_state_carries_report_progress_and_artifact_contract() -> None:
    """The live helper must retain all public terminal-report fields."""
    state = replace(
        build_task_state(),
        status="running",
        analysis_id="analysis-1",
        output_dir="/obs/output",
        intermediate_report="# intermediate",
        report_revision=3,
        report_updated_at="2026-07-15T00:00:00Z",
        progress={"total": 12, "succeeded": 2},
        failures=(
            {
                "work_item_key": "design",
                "status": "failed",
                "message": "analysis task unavailable",
            },
        ),
        artifacts=({"output_dir": "/obs/output", "paths": ("/obs/a",)},),
        output_dirs=("/obs/output",),
    )

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
            "progress": {
                "brief_gene_status": " SUCCEEDED ",
                "running": -1,
                "submitted_task_id": "must-drop",
            },
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
    assert state.progress == {"brief_gene_status": "succeeded"}
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
            self,
            _path: str,
            **request_options: object,
        ) -> Response:
            """Return the next prepared response."""
            self.paths.append(_path)
            assert request_options["timeout"] is not None
            return next(self.responses)

        @property
        def request_count(self) -> int:
            """Return how many polling paths were requested."""
            return len(self.paths)

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
    assert client.request_count == 2
    assert client.paths == ["/v1/runs/run-1", "/v1/runs/run-1"]


@pytest.mark.asyncio
async def test_http_poll_uses_environment_resolved_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP polling resolves its default budget from the E2E environment."""

    monotonic_values = iter((100.0, 101.5))
    monkeypatch.setenv("PHYTOMNI_E2E_POLL_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setattr(
        polling,
        "time",
        SimpleNamespace(monotonic=lambda: next(monotonic_values)),
    )
    client = _TimeoutCapturingClient(({"status": "succeeded", "result": {}},))

    terminal = await polling.poll_http_run_to_terminal(
        cast(httpx.AsyncClient, client),
        "run-environment-timeout",
        headers={"X-Service-Token": "test"},
    )

    assert terminal.status == "succeeded"
    assert client.timeout == pytest.approx(6.0)


@pytest.mark.asyncio
async def test_http_poll_bounds_each_get_by_remaining_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each HTTP read uses only the polling budget still available."""

    client = _TimeoutCapturingClient(
        (
            {"status": "running", "result": {}},
            {"status": "succeeded", "result": {}},
        )
    )

    monotonic_values = iter((10.0, 11.0, 14.0))
    monkeypatch.setattr(
        polling,
        "time",
        SimpleNamespace(monotonic=lambda: next(monotonic_values)),
    )

    terminal = await polling.poll_http_run_to_terminal(
        cast(httpx.AsyncClient, client),
        "run-bounded-reads",
        headers={"X-Service-Token": "test"},
        timeout_seconds=10.0,
        poll_interval_seconds=0.0,
    )

    assert terminal.status == "succeeded"
    assert client.timeouts == pytest.approx([9.0, 6.0])
