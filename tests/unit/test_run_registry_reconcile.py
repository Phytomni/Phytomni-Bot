# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Reconciliation and report-synthesis contracts for the run registry."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from tests.support.run_registry_fakes import fixed_run_context
from tests.unit.test_run_registry import (
    _make_registry,
    _seed_async_run,
)

from mcp_server_phytomni.mcp.formatting.models import ReportExecution
from mcp_server_phytomni.runtime import run_registry
from mcp_server_phytomni.runtime.artifact_roles import ArtifactRole
from mcp_server_phytomni.runtime.execution_models import ExecutionWarning
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRequestInfo,
    RunSpec,
)
from mcp_server_phytomni.runtime.task_manager import Submission
from mcp_server_phytomni.runtime.terminal_report import (
    TerminalReportAssembly,
)
from mcp_server_phytomni.storage.artifact_listing import ListedArtifactObject

pytestmark = pytest.mark.unit


async def _empty_lister(output_dir: str) -> list:
    """No-op artifact lister for reconcile tests (avoids real OBS I/O)."""
    assert isinstance(output_dir, str)
    return []


async def _report_object_lister(
    output_dir: str,
) -> list[ListedArtifactObject]:
    """Return one manifest-backed scientific text object."""
    return [
        ListedArtifactObject(
            relative_path="report.md",
            source_path=f"{output_dir}/report.md",
            size_bytes=64,
            download_ref=f"{output_dir}/report.md",
        )
    ]


async def _report_manifest(_output_dir: str) -> dict[str, object]:
    """Declare the report object as scientific text for tests."""
    return {
        "version": "1.0",
        "artifacts": [
            {
                "path": "report.md",
                "role": "scientific_report",
                "media_type": "text/markdown",
            }
        ],
    }


def _final_report_assembly(agent: str) -> TerminalReportAssembly:
    """Build a deterministic report result for one report-capable agent."""
    return TerminalReportAssembly(
        answer=f"# {agent.title()} report\n\nValidated scientific result.",
        report=ReportExecution(
            state="final",
            degraded=False,
            source_artifact_count=1,
        ),
    )


@dataclass(frozen=True)
class _FakeReportResult:
    """Shared test double for terminal report results."""

    final_report: str = ""
    answer: str = ""
    degraded: bool = False
    degraded_reason: str | None = None
    selected_paths: tuple = ()
    skipped_paths: tuple = ()


@pytest.mark.asyncio
async def test_reconcile_terminal_run_does_not_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cached terminal run is returned without any reconcile_task call."""
    registry, _, _ = _make_registry(tmp_path)
    registry.create_run(
        RunSpec("run-sync-2", "alice", "chat", "local"),
        outcome=RunOutcome(status="succeeded", result={"answer": "ok"}),
    )
    calls = {"n": 0}

    async def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Track that the cache guard skips reconcile_task for terminals."""
        _ = (args, kwargs)
        calls["n"] += 1
        return {"status": "succeeded"}

    monkeypatch.setattr(run_registry, "reconcile_task", boom)

    record = await registry.reconcile("run-sync-2", owner="alice")

    assert record is not None
    assert record.status == "succeeded"
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_reconcile_aggregates_all_succeeded_into_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All success-like child statuses settle the run as succeeded."""
    registry, manager, _ = _make_registry(tmp_path)
    _seed_async_run(
        registry,
        manager,
        RunSpec("run-r", "alice", "analyst", "remote"),
        ("t-1", "t-2"),
    )
    statuses = {"t-1": "succeeded", "t-2": "completed"}
    output_dirs = {"t-1": "/obs/a", "t-2": "/obs/b"}

    async def fake(task_id: str) -> dict[str, Any]:
        """Return one terminal status per child task id."""
        return {
            "task_id": task_id,
            "status": statuses[task_id],
            "output_dir": output_dirs[task_id],
        }

    monkeypatch.setattr(run_registry, "reconcile_task", fake)

    record = await registry.reconcile(
        "run-r", owner="alice", lister=_empty_lister
    )

    assert record is not None
    assert record.status == "succeeded"
    assert record.timestamps.expires_at is not None
    assert record.result is not None
    assert set(record.result) == {"formatted", "execution"}
    assert record.result["execution"]["tasks"] == [
        {"id": "t-1", "accepted": True, "status": "succeeded"},
        {"id": "t-2", "accepted": True, "status": "completed"},
    ]
    assert record.result["execution"]["artifacts"] == []
    assert record.result["execution"]["report"]["state"] == "degraded"
    cached = registry.get_run("run-r", owner="alice")
    assert cached is not None
    assert cached.status == "succeeded"


@pytest.mark.asyncio
async def test_reconcile_surfaces_deep_genome_final_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A succeeded DeepGenome child lifts its final_report to the payload.

    DeepGenome's single umbrella child persists the assembled report on
    its row; reconcile_task carries it through, and the terminal payload
    surfaces the first non-empty report under ``final_report`` so a
    client polling /v1/runs/{id} reads the markdown without descending
    into ``task_results``.
    """
    registry, manager, _ = _make_registry(tmp_path)
    _seed_async_run(
        registry,
        manager,
        RunSpec("run-dg", "alice", "deep_genome", "remote"),
        ("dg-1",),
    )
    report_md = "# Deep Genome Analysis of Os01g0177400\n\nbody\n"

    async def fake(task_id: str) -> dict[str, Any]:
        """Return a succeeded child carrying the persisted report."""
        return {
            "task_id": task_id,
            "status": "succeeded",
            "output_dir": "/obs/dg",
            "final_report": report_md,
        }

    monkeypatch.setattr(run_registry, "reconcile_task", fake)

    record = await registry.reconcile(
        "run-dg", owner="alice", lister=_empty_lister
    )

    assert record is not None
    assert record.status == "succeeded"
    assert record.result is not None
    assert record.result["final_report"] == report_md
    assert record.result["formatted"]["answer"]


@pytest.mark.asyncio
async def test_reconcile_final_report_none_without_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A report-capable run without scientific text degrades safely."""
    registry, manager, _ = _make_registry(tmp_path)
    _seed_async_run(
        registry,
        manager,
        RunSpec("run-an", "alice", "analyst", "remote"),
        ("t-1",),
    )

    async def fake(task_id: str) -> dict[str, Any]:
        """Return a succeeded child with no persisted report."""
        return {
            "task_id": task_id,
            "status": "succeeded",
            "output_dir": "/obs/a",
            "final_report": None,
        }

    monkeypatch.setattr(run_registry, "reconcile_task", fake)
    record = await registry.reconcile(
        "run-an", owner="alice", lister=_empty_lister
    )

    assert record is not None
    assert record.result is not None
    assert set(record.result) == {"formatted", "execution"}
    assert record.result["formatted"]["answer"].strip()
    assert record.result["execution"]["report"] == {
        "state": "degraded",
        "degraded": True,
        "source_artifact_count": 0,
    }


@pytest.mark.asyncio
async def test_reconcile_propagates_failure_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One failed child marks the run failed and records the error."""
    registry, manager, _ = _make_registry(tmp_path)
    _seed_async_run(
        registry,
        manager,
        RunSpec("run-f", "alice", "analyst", "remote"),
        ("t-1", "t-2"),
    )
    statuses = {"t-1": "succeeded", "t-2": "failed"}

    async def fake(task_id: str) -> dict[str, Any]:
        """Return mixed terminal statuses to trigger failure aggregation."""
        return {"task_id": task_id, "status": statuses[task_id]}

    monkeypatch.setattr(run_registry, "reconcile_task", fake)

    record = await registry.reconcile("run-f", owner="alice")

    assert record is not None
    assert record.status == "failed"
    assert record.error is not None
    assert "t-2" in record.error
    assert record.result is not None
    assert set(record.result) == {"formatted", "execution"}
    assert record.result["execution"]["tasks"] == [
        {"id": "t-1", "accepted": True, "status": "succeeded"},
        {"id": "t-2", "accepted": True, "status": "failed"},
    ]
    assert not record.result["execution"]["artifacts"]
    assert record.result["execution"]["diagnostics"] == [
        {"code": "task_failed", "retryable": False, "stage": "reconcile"}
    ]


@pytest.mark.asyncio
async def test_reconcile_carries_submit_warnings_to_terminal_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Submit-time partial warnings remain visible after reconciliation."""
    registry, manager, _ = _make_registry(tmp_path)
    spec = RunSpec("run-warn", "alice", "research", "remote")
    registry.create_run(
        spec,
        outcome=RunOutcome(
            result={
                "execution": {
                    "warnings": [
                        {
                            "code": "partial_submission",
                            "retryable": False,
                            "rejected_count": 1,
                        }
                    ]
                }
            }
        ),
    )
    manager.record(
        Submission(
            task_id="t-warn",
            status="submitted",
            output_dir="/obs/research",
            run_context=fixed_run_context(spec),
        )
    )

    async def fake(task_id: str) -> dict[str, Any]:
        """Return one successful child for terminal settlement."""
        return {"task_id": task_id, "status": "succeeded"}

    monkeypatch.setattr(run_registry, "reconcile_task", fake)
    record = await registry.reconcile("run-warn", owner="alice")

    assert record is not None
    assert record.result is not None
    codes = [
        warning["code"] for warning in record.result["execution"]["warnings"]
    ]
    assert codes == [
        "partial_submission",
        "report_no_scientific_text",
    ]


@pytest.mark.asyncio
async def test_reconcile_assembles_answer_and_paths_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First terminal poll globs + synthesizes; later polls reuse the cache."""
    registry, manager, _ = _make_registry(tmp_path)
    _seed_async_run(
        registry,
        manager,
        RunSpec("run-once", "alice", "research", "remote"),
        ("t-1",),
    )

    async def fake(task_id: str) -> dict[str, Any]:
        """Return a succeeded child with no persisted report."""
        return {
            "task_id": task_id,
            "status": "succeeded",
            "output_dir": "/obs/p/r1",
            "final_report": None,
        }

    monkeypatch.setattr(run_registry, "reconcile_task", fake)
    glob_calls = {"n": 0}

    async def counting_lister(output_dir: str) -> list:
        """Count glob invocations and return a single figure path."""
        glob_calls["n"] += 1
        return [f"{output_dir}/fig.png"]

    first = await registry.reconcile(
        "run-once", owner="alice", lister=counting_lister
    )

    assert first is not None
    assert first.status == "succeeded"
    assert first.result is not None
    assert first.result["formatted"]["answer"].startswith(
        "The analysis reached a terminal outcome"
    )
    assert first.result["execution"]["artifacts"] == [
        {
            "role": "unknown",
            "name": "fig.png",
            "media_type": "application/octet-stream",
            "size_bytes": 0,
            "downloadable": True,
            "report_context_eligible": False,
            "download_ref": "/obs/p/r1/fig.png",
        }
    ]

    second = await registry.reconcile(
        "run-once", owner="alice", lister=counting_lister
    )

    assert second is not None
    assert second.result is not None
    assert (
        second.result["formatted"]["answer"]
        == first.result["formatted"]["answer"]
    )
    assert glob_calls["n"] == 1  # settle-once: not re-globbed


@pytest.mark.asyncio
async def test_reconcile_concurrent_first_polls_are_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two simultaneous first polls assemble twice but settle identically.

    ``reconcile`` has no compare-and-swap: each first poll reads a
    non-terminal run and runs the glob + synth + settle independently, so
    the artifact lister fires once per concurrent poll (at-least-once, NOT
    exactly-once). The guarantee that actually holds is idempotency — the
    duplicated assembly is pure, so both returned records and the
    persisted row agree. A gate forces both coroutines past the
    non-terminal status read before either settles, exercising the race
    deterministically. (Sequential later polls still short-circuit; see
    ``test_reconcile_terminal_run_does_not_poll``.)
    """
    registry, manager, _ = _make_registry(tmp_path)
    _seed_async_run(
        registry,
        manager,
        RunSpec("run-cc", "alice", "network", "remote"),
        ("cc-1",),
    )
    release = asyncio.Event()

    async def gated(task_id: str) -> dict[str, Any]:
        """Hold every poll at the gate, then settle the child succeeded."""
        await release.wait()
        return {
            "task_id": task_id,
            "status": "succeeded",
            "output_dir": "/obs/cc",
            "final_report": None,
        }

    glob_hits = {"n": 0}

    async def counting_lister(output_dir: str) -> list:
        """Count each glob so the at-least-once behaviour is observable."""
        glob_hits["n"] += 1
        return [f"{output_dir}/plot.png"]

    monkeypatch.setattr(run_registry, "reconcile_task", gated)

    first = asyncio.create_task(
        registry.reconcile("run-cc", owner="alice", lister=counting_lister)
    )
    second = asyncio.create_task(
        registry.reconcile("run-cc", owner="alice", lister=counting_lister)
    )
    await asyncio.sleep(0)  # let both reach the gate past the status read
    release.set()
    rec1, rec2 = await asyncio.gather(first, second)

    # At-least-once: both first polls did the work (no CAS to dedupe them).
    assert glob_hits["n"] == 2
    # ...but idempotent: both returns and the cached row carry one answer.
    assert rec1 is not None and rec2 is not None
    assert rec1.status == rec2.status == "succeeded"
    assert rec1.result is not None and rec2.result is not None
    settled = rec1.result["formatted"]["answer"]
    assert rec2.result["formatted"]["answer"] == settled
    assert rec1.result["execution"]["artifacts"][0]["name"] == "plot.png"
    cached = registry.get_run("run-cc", owner="alice")
    assert cached is not None and cached.result is not None
    assert cached.result["formatted"]["answer"] == settled


@pytest.mark.parametrize(
    "agent",
    ["analyst", "research", "design", "network"],
)
@pytest.mark.asyncio
async def test_report_agents_have_manifest_backed_final_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    agent: str,
) -> None:
    """Every analyst-class agent persists one scientific final report."""
    registry, manager, _ = _make_registry(tmp_path)
    spec = RunSpec(f"run-{agent}", "alice", agent, "remote")
    _seed_async_run(registry, manager, spec, (f"task-{agent}",))
    registry.update_request_info(
        spec.run_id,
        owner="alice",
        request_info=RunRequestInfo(query=f"summarize {agent}"),
    )

    async def fake_reconcile(task_id: str) -> dict[str, Any]:
        """Return one successful child with a producer output directory."""
        return {
            "task_id": task_id,
            "status": "succeeded",
            "output_dir": "/obs/bucket/out",
        }

    async def fake_assemble(**kwargs: Any) -> TerminalReportAssembly:
        """Return a deterministic report after checking scientific input."""
        assert kwargs["context"].agent == agent
        artifacts = tuple(kwargs["artifacts"])
        assert len(artifacts) == 1
        assert artifacts[0].role is ArtifactRole.SCIENTIFIC_REPORT
        return _final_report_assembly(agent)

    monkeypatch.setattr(run_registry, "reconcile_task", fake_reconcile)
    monkeypatch.setattr(
        run_registry,
        "assemble_terminal_report",
        fake_assemble,
    )

    record = await registry.reconcile(
        spec.run_id,
        owner="alice",
        object_lister=_report_object_lister,
        manifest_loader=_report_manifest,
    )

    assert record is not None
    assert record.result is not None
    result = record.result
    assert set(result) == {"formatted", "execution"}
    assert result["formatted"]["answer"].strip()
    assert result["execution"]["report"]["state"] == "final"
    assert result["execution"]["report"]["degraded"] is False
    assert result["execution"]["artifacts"][0]["role"] == ("scientific_report")
    assert result["formatted"]["metadata"]["report"] == (
        result["execution"]["report"]
    )
    assert manager.get_task_final_report(f"task-{agent}") == (
        result["formatted"]["answer"]
    )


@pytest.mark.asyncio
async def test_reconcile_terminal_analyst_run_includes_final_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Analyst-class reconcile persists a final_report on the payload."""
    registry, manager, _ = _make_registry(tmp_path)
    spec = RunSpec("run-tr", "alice", "analyst", "remote")
    _seed_async_run(registry, manager, spec, ("task-1",))
    registry.update_request_info(
        "run-tr",
        owner="alice",
        request_info=RunRequestInfo(query="summarize this run"),
    )

    async def fake_reconcile_task(task_id: str) -> dict[str, Any]:
        """Return a succeeded task with the expected output_dir."""
        return {
            "task_id": task_id,
            "status": "succeeded",
            "output_dir": "/obs/bucket/out",
        }

    async def fake_assemble(**kwargs: Any) -> TerminalReportAssembly:
        """Return a canned report result for the analyst agent."""
        assert kwargs["context"].agent == "analyst"
        return TerminalReportAssembly(
            answer="# Analyst Final Report\n\nLLM summary.",
            report=ReportExecution(
                state="final",
                degraded=False,
                source_artifact_count=1,
            ),
        )

    monkeypatch.setattr(run_registry, "reconcile_task", fake_reconcile_task)
    monkeypatch.setattr(
        run_registry,
        "assemble_terminal_report",
        fake_assemble,
    )

    record = await registry.reconcile(
        "run-tr",
        owner="alice",
        object_lister=_report_object_lister,
        manifest_loader=_report_manifest,
    )

    assert record is not None
    assert record.status == "succeeded"
    assert record.result is not None
    assert record.result["formatted"]["answer"] == (
        "# Analyst Final Report\n\nLLM summary."
    )
    assert record.result["execution"]["report"]["state"] == "final"
    assert record.result["execution"]["artifacts"][0]["name"] == ("report.md")
    assert manager.get_task_final_report("task-1") == (
        "# Analyst Final Report\n\nLLM summary."
    )


@pytest.mark.asyncio
async def test_reconcile_terminal_report_degraded_reaches_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A degraded terminal report surfaces degraded_reason on the payload."""
    registry, manager, _ = _make_registry(tmp_path)
    spec = RunSpec("run-deg", "alice", "design", "remote")
    _seed_async_run(registry, manager, spec, ("task-1",))
    registry.update_request_info(
        "run-deg",
        owner="alice",
        request_info=RunRequestInfo(query="design workflow"),
    )

    async def fake_reconcile_task(task_id: str) -> dict[str, Any]:
        """Return a succeeded task row."""
        return {
            "task_id": task_id,
            "status": "succeeded",
            "output_dir": "/obs/bucket/out",
        }

    _degraded_result = _FakeReportResult(
        final_report="# Digital Design Final Report\n\nFallback.",
        answer="Analysis complete: 1/1 tasks succeeded.",
        degraded=True,
        degraded_reason="LLM summary returned empty content",
    )

    async def fake_assemble(**_kwargs: Any) -> TerminalReportAssembly:
        """Return a degraded report result."""
        return TerminalReportAssembly(
            answer=_degraded_result.final_report,
            report=ReportExecution(
                state="degraded",
                degraded=True,
                source_artifact_count=1,
            ),
            warnings=(
                ExecutionWarning(
                    code="report_synthesis_failed",
                    stage="terminal_report",
                ),
            ),
        )

    monkeypatch.setattr(run_registry, "reconcile_task", fake_reconcile_task)
    monkeypatch.setattr(
        run_registry,
        "assemble_terminal_report",
        fake_assemble,
    )

    record = await registry.reconcile(
        "run-deg",
        owner="alice",
        object_lister=_report_object_lister,
        manifest_loader=_report_manifest,
    )

    assert record is not None
    assert record.result is not None
    assert record.result["execution"]["tracking"]["degraded"] is True
    assert record.result["execution"]["report"] == {
        "state": "degraded",
        "degraded": True,
        "source_artifact_count": 1,
    }
    assert record.result["execution"]["warnings"] == [
        {
            "code": "report_synthesis_failed",
            "retryable": False,
            "stage": "terminal_report",
        }
    ]
    assert manager.get_task_degraded("task-1") == ("report_synthesis_failed")


@pytest.mark.asyncio
async def test_reconcile_non_target_agent_skips_terminal_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-analyst-class agents never invoke terminal report synthesis."""
    registry, manager, _ = _make_registry(tmp_path)
    _seed_async_run(
        registry,
        manager,
        RunSpec("run-chat", "alice", "chat", "remote"),
        ("task-1",),
    )
    called = {"n": 0}

    async def fake_reconcile_task(task_id: str) -> dict[str, Any]:
        """Return a succeeded task row."""
        return {
            "task_id": task_id,
            "status": "succeeded",
            "output_dir": "/obs/bucket/out",
        }

    async def tracking_synthesize(_context: Any) -> Any:
        """Track that synthesis was invoked (should not happen here)."""
        called["n"] += 1
        return _FakeReportResult()

    monkeypatch.setattr(run_registry, "reconcile_task", fake_reconcile_task)
    monkeypatch.setattr(
        run_registry,
        "assemble_terminal_report",
        tracking_synthesize,
    )

    record = await registry.reconcile(
        "run-chat", owner="alice", lister=_empty_lister
    )

    assert record is not None
    assert called["n"] == 0
    assert record.result is not None
    assert record.result["final_report"] is None
