# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Contract matrix for DeepGenome dispatch and optional-task lifecycle.

These tests characterize the existing coordinator boundary without changing
production code.  The matrix covers logical routing, concrete work-item
identities, durable acceptance and transition ordering, restart reads,
partial outcomes, cancellation propagation, and deterministic state deltas.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from mcp_server_phytomni.agents.deep_genome import dispatch as dispatch_module
from mcp_server_phytomni.agents.deep_genome.coordinator import (
    RemoteSubmission,
    WorkItemOutcome,
    WorkItemPollCallbacks,
    WorkItemPollOptions,
    WorkItemPollRequest,
    concrete_work_item_outcomes,
    derive_workflow_outcome,
    poll_work_item,
)
from mcp_server_phytomni.agents.deep_genome.dispatch import (
    GENERIC_ANALYSIS_NODE_TYPES,
)
from mcp_server_phytomni.agents.deep_genome.tracking import (
    DeepGenomeTransitionSink,
)
from mcp_server_phytomni.agents.deep_genome.work_items import (
    build_work_item_plan,
    section_keys,
)
from mcp_server_phytomni.runtime.deep_genome_store import (
    DeepGenomeReservation,
    DeepGenomeStore,
    DeepGenomeTrackingError,
)
from tests.support.sqlite import closed_sqlite_connection

pytestmark = pytest.mark.agent


def _seed_store(
    tmp_path: Path,
) -> tuple[DeepGenomeStore, DeepGenomeReservation]:
    """Reserve an owner, persist BriefGene, and seed all optional work."""
    store = DeepGenomeStore(str(tmp_path / "tasks.db"))
    reservation = store.reserve_run(
        run_id="run-contract-oracle",
        umbrella_task_id="task-contract-oracle",
        owner="contract-owner",
        output_dir="/obs/contract-oracle",
    )
    store.apply_brief_gene_transition(
        reservation.umbrella_task_id,
        status="succeeded",
        summary_markdown="# Contract profile",
    )
    plan = build_work_item_plan("ath", "AT1G01010", "AT1G01010")
    store.seed_plan(reservation, plan)
    return store, reservation


def test_work_item_plan_contract_matrix() -> None:
    """The twelve concrete jobs keep stable keys, prompts, and report order."""
    plan = build_work_item_plan("osa", "Os01g0100100", "LOC_Os01g0100100")

    assert len(plan) == 12
    assert [item.display_order for item in plan] == list(range(12))
    expected_keys = tuple(
        re.findall(
            r"[a-z_]+",
            "evolution_analysis|gene_expression_tissues|"
            "gene_expression_cultivars|gene_expression_treatments|"
            "gene_expression_genotypes|single_cell_analysis|promoter_analysis|"
            "smep_analysis|smoc_analysis|protein_structure_analysis|"
            "protein_design|promoter_design",
        )
    )
    assert tuple(item.work_item_key for item in plan) == expected_keys
    assert [item.analysis_type for item in plan[-2:]] == [
        "protein_design_analysis",
        "promoter_design_analysis",
    ]
    assert [item.compute_resource for item in plan] == [
        "medium",
        "small",
        "small",
        "small",
        "small",
        "small",
        "small",
        "small",
        "small",
        "medium",
        "medium",
        "small",
    ]
    expected_sections = tuple(
        re.findall(
            r"[a-z_]+",
            "evolution_analysis|gene_expression_tissues|"
            "gene_expression_cultivars|gene_expression_treatments|"
            "gene_expression_genotypes|single_cell_analysis|promoter_analysis|"
            "smep_analysis|smoc_analysis|protein_structure_analysis|"
            "digital_design",
        )
    )
    assert section_keys(plan) == expected_sections
    assert all(item.species_code == "osa" for item in plan)
    assert all(
        item.target_gene == "LOC_Os01g0100100"
        for item in plan
        if item.section_key.startswith("gene_expression_")
    )
    assert all(
        item.target_gene == "Os01g0100100"
        for item in plan
        if not item.section_key.startswith("gene_expression_")
    )


def test_route_contract_maps_every_logical_branch() -> None:
    """Mounted and generic branches retain deterministic node names."""
    logical_types = [
        "evolution_analysis",
        *GENERIC_ANALYSIS_NODE_TYPES,
        "digital_design",
    ]
    state: dict[str, Any] = {
        "task_submit_sleep": 0,
        "analysis_tasks": [
            {
                "analysis_type": analysis_type,
                "target_gene": "Os01g0100100",
                "species_code": "osa",
            }
            for analysis_type in logical_types
        ],
    }
    route = getattr(
        dispatch_module.DeepGenomeDispatchMixin, "_route_analyst_tasks"
    )

    sends = route(object(), state)
    by_type = {send.arg["analysis_type"]: send.node for send in sends}

    assert by_type["evolution_analysis"] == "evolution_node"
    assert by_type["digital_design"] == "design_node"
    assert {
        analysis_type: by_type[analysis_type]
        for analysis_type in GENERIC_ANALYSIS_NODE_TYPES
    } == {
        analysis_type: getattr(dispatch_module, "_analyst_node_name")(
            analysis_type
        )
        for analysis_type in GENERIC_ANALYSIS_NODE_TYPES
    }
    assert [send.arg["task_index"] for send in sends] == list(
        range(len(logical_types))
    )


def test_deep_genome_dispatch_requests_same_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepGenome's Analyst dispatch carries the shared manifest contract."""

    def fake_prompt_parts(
        context: Any,
        *,
        prompt_file: str,
        data_file: str,
    ) -> tuple[str, dict[str, Any], str, str]:
        assert context.analysis_type == "smoc_analysis"
        assert prompt_file == "prompt.yaml"
        assert data_file == "data.json"
        return "goal", {}, "meta", "small"

    monkeypatch.setattr(
        dispatch_module.deep_genome_routing,
        "build_analysis_prompt_parts",
        fake_prompt_parts,
    )
    dispatcher = SimpleNamespace(
        deep_genome_config=SimpleNamespace(
            PROMPT_FILE="prompt.yaml",
            DEEPGENOME_DATA="data.json",
        )
    )
    context = dispatch_module.AnalysisDispatchContext(
        "smoc_analysis",
        "osa",
        "Os01g0100100",
        "/obs/output",
    )

    analysis_prompt_parts = getattr(
        dispatch_module.DeepGenomeDispatchMixin, "_analysis_prompt_parts"
    )
    _goal, _data, instructions, _compute = analysis_prompt_parts(
        dispatcher, context
    )

    assert ".phytomni-artifacts.json" in instructions
    assert '"version": "1.0"' in instructions
    assert "Do not classify by filename extension" in instructions


async def test_acceptance_transition_and_restart_contract(
    tmp_path: Path,
) -> None:
    """Accepted IDs and every local transition survive a fresh store read."""
    store, reservation = _seed_store(tmp_path)
    sink = DeepGenomeTransitionSink(store, reservation.umbrella_task_id)
    submission = RemoteSubmission(
        submitted_task_id="caller-smoc",
        poll_task_id="source-smoc",
        output_dir="/obs/smoc",
    )
    await sink.accept_remote_submission("smoc_analysis", submission)
    await sink.persist_work_item_transition(
        "smoc_analysis", submission, "pending", None, None
    )
    pending = store.get_snapshot(reservation.umbrella_task_id)
    assert pending is not None
    await sink.persist_work_item_transition(
        "smoc_analysis", submission, "running", None, None
    )
    running = store.get_snapshot(reservation.umbrella_task_id)
    assert running is not None
    await sink.persist_work_item_transition(
        "smoc_analysis", submission, "succeeded", "# SMOC summary", None
    )
    succeeded = store.get_snapshot(reservation.umbrella_task_id)
    assert succeeded is not None

    assert [
        pending.report_revision,
        running.report_revision,
        succeeded.report_revision,
    ] == [2, 3, 4]
    assert pending.report_stage == "intermediate"
    assert "SMOC" not in (pending.intermediate_report or "")
    assert succeeded.progress["succeeded"] == 1
    assert "SMOC" in (succeeded.intermediate_report or "")
    assert succeeded.final_report is None

    assert (
        DeepGenomeStore(str(tmp_path / "tasks.db")).get_snapshot(
            reservation.umbrella_task_id
        )
        == succeeded
    )
    with closed_sqlite_connection(tmp_path / "tasks.db") as connection:
        assert connection.execute(
            "SELECT status, submitted_task_id, poll_task_id "
            "FROM deep_genome_remote_tasks "
            "WHERE umbrella_task_id = ? AND work_item_key = ?",
            (reservation.umbrella_task_id, "smoc_analysis"),
        ).fetchone() == ("succeeded", "caller-smoc", "source-smoc")

    # A retry after restart is idempotent and cannot replace either identity.
    retry_store = DeepGenomeStore(str(tmp_path / "tasks.db"))
    retry_sink = DeepGenomeTransitionSink(
        retry_store, reservation.umbrella_task_id
    )
    await retry_sink.accept_remote_submission("smoc_analysis", submission)
    retried_snapshot = retry_store.get_snapshot(reservation.umbrella_task_id)
    assert retried_snapshot is not None
    assert retried_snapshot == succeeded


@pytest.mark.parametrize(
    "status",
    ["pending", "running", "succeeded", "failed", "cancelled", "timed_out"],
)
async def test_transition_sink_persists_every_local_status(
    tmp_path: Path,
    status: str,
) -> None:
    """Every coordinator lifecycle status reaches the durable report."""
    store, reservation = _seed_store(tmp_path)
    sink = DeepGenomeTransitionSink(store, reservation.umbrella_task_id)
    submission = RemoteSubmission(
        f"caller-{status}", f"source-{status}", "/obs/status"
    )
    await sink.accept_remote_submission("smoc_analysis", submission)
    summary = "# usable summary" if status == "succeeded" else None
    outcome = await sink.persist_work_item_transition(
        "smoc_analysis", submission, status, summary, "ignored upstream text"
    )

    assert outcome.status == status
    snapshot = store.get_snapshot(reservation.umbrella_task_id)
    assert snapshot is not None
    assert snapshot.report_revision == 2
    with closed_sqlite_connection(tmp_path / "tasks.db") as connection:
        row = connection.execute(
            "SELECT status, summary_markdown, failure_reason "
            "FROM deep_genome_remote_tasks "
            "WHERE umbrella_task_id = ? AND work_item_key = ?",
            (reservation.umbrella_task_id, "smoc_analysis"),
        ).fetchone()
    expected_reason = {
        "failed": "analysis task failed",
        "cancelled": "analysis task cancelled",
        "timed_out": "analysis task timed out",
    }.get(status)
    assert row == (status, summary, expected_reason)


async def test_transition_sink_ignores_invalid_untracked_task_id() -> None:
    """Anonymous or malformed graph state keeps in-memory outcomes only."""
    sink = DeepGenomeTransitionSink.from_state(
        {"task_id": "   "},
        cancel_submission=AsyncMock(),
    )
    submission = RemoteSubmission("caller", "source", "/obs/out")

    await sink.accept_remote_submission("smoc_analysis", submission)
    outcome = await sink.persist_work_item_transition(
        "smoc_analysis", submission, "running", None, None
    )

    assert outcome == WorkItemOutcome("running")
    assert sink.store is None
    assert sink.umbrella_task_id is None


async def test_transition_sink_missing_row_fails_owner_and_cancels(
    tmp_path: Path,
) -> None:
    """An unknown work item cannot be accepted or left remotely running."""
    store, reservation = _seed_store(tmp_path)
    cancel = AsyncMock()
    sink = DeepGenomeTransitionSink(
        store,
        reservation.umbrella_task_id,
        cancel,
    )
    submission = RemoteSubmission("caller-missing", "source-missing", "/obs")

    with pytest.raises(DeepGenomeTrackingError, match="remote analysis"):
        await sink.accept_remote_submission("missing_work_item", submission)

    cancel.assert_awaited_once_with(submission)
    snapshot = store.get_snapshot(reservation.umbrella_task_id)
    assert snapshot is not None
    assert snapshot.status == "failed"


async def test_transition_sink_store_error_fails_owner_and_cancels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transition write error settles the owner and cancels caller work."""
    store, reservation = _seed_store(tmp_path)
    submission = RemoteSubmission("caller-error", "source-error", "/obs")
    await DeepGenomeTransitionSink(
        store,
        reservation.umbrella_task_id,
    ).accept_remote_submission("smoc_analysis", submission)
    cancel = AsyncMock()
    sink = DeepGenomeTransitionSink(
        store,
        reservation.umbrella_task_id,
        cancel,
    )

    monkeypatch.setattr(
        DeepGenomeStore,
        "apply_work_item_transition",
        Mock(side_effect=sqlite3.OperationalError("database is locked")),
    )

    with pytest.raises(DeepGenomeTrackingError, match="remote analysis"):
        await sink.persist_work_item_transition(
            "smoc_analysis", submission, "running", None, None
        )

    cancel.assert_awaited_once_with(submission)
    snapshot = store.get_snapshot(reservation.umbrella_task_id)
    assert snapshot is not None
    assert snapshot.status == "failed"


async def test_transition_sink_rejects_double_terminal_and_records_cancel(
    tmp_path: Path,
) -> None:
    """A terminal row cannot be rewritten as success or failure."""
    store, reservation = _seed_store(tmp_path)
    cancel = AsyncMock()
    sink = DeepGenomeTransitionSink(
        store,
        reservation.umbrella_task_id,
        cancel,
    )
    submission = RemoteSubmission("caller-terminal", "source-terminal", "/obs")
    await sink.accept_remote_submission("smoc_analysis", submission)
    await sink.persist_work_item_transition(
        "smoc_analysis", submission, "succeeded", "# summary", None
    )

    with pytest.raises(DeepGenomeTrackingError, match="remote analysis"):
        await sink.persist_work_item_transition(
            "smoc_analysis", submission, "failed", None, None
        )

    cancel.assert_awaited_once_with(submission)
    snapshot = store.get_snapshot(reservation.umbrella_task_id)
    assert snapshot is not None
    assert snapshot.status == "failed"


async def test_poll_contract_emits_ordered_transitions_and_summary() -> None:
    """Pending/running observations precede one usable terminal result."""
    statuses = iter(("PENDING", "RUNNING", "SUCCEEDED"))
    transitions: list[tuple[str, str | None, str | None]] = []
    seen: list[tuple[str, float]] = []
    submission = RemoteSubmission("caller-1", "source-1", "/obs/out")

    async def read_status(
        task_id: str, request_timeout: float
    ) -> dict[str, str]:
        seen.append((task_id, request_timeout))
        return {"status": next(statuses)}

    async def resolve_result(_submission: RemoteSubmission) -> str:
        return "  # usable summary  "

    async def record(
        status: str,
        summary: str | None,
        reason: str | None,
    ) -> WorkItemOutcome:
        transitions.append((status, summary, reason))
        return WorkItemOutcome(status, summary, reason)

    async def no_sleep(_seconds: float) -> None:
        return None

    outcome = await poll_work_item(
        WorkItemPollRequest(
            submission=submission,
            callbacks=WorkItemPollCallbacks(
                status_reader=read_status,
                result_resolver=resolve_result,
                transition_sink=record,
            ),
            options=WorkItemPollOptions(
                request_timeout=4,
                poll_interval=0,
                deadline_seconds=10,
                sleep=no_sleep,
            ),
        )
    )

    assert outcome == WorkItemOutcome("succeeded", "# usable summary", None)
    assert transitions == [
        ("pending", None, None),
        ("running", None, None),
        ("succeeded", "# usable summary", None),
    ]
    assert seen == [("source-1", 4), ("source-1", 4), ("source-1", 4)]


async def test_poll_optional_failure_and_cancel() -> None:
    """Remote errors degrade one item; cancellation reaches the owner."""
    submission = RemoteSubmission("caller-1", "source-1", "/obs/out")
    failure_transitions: list[tuple[str, str | None, str | None]] = []

    async def broken_status(_task_id: str, _timeout: float) -> None:
        raise RuntimeError("remote provider details must not escape")

    async def record_failure(
        status: str,
        summary: str | None,
        reason: str | None,
    ) -> WorkItemOutcome:
        failure_transitions.append((status, summary, reason))
        return WorkItemOutcome(status, summary, reason)

    async def no_sleep(_seconds: float) -> None:
        return None

    failed = await poll_work_item(
        WorkItemPollRequest(
            submission=submission,
            callbacks=WorkItemPollCallbacks(
                status_reader=broken_status,
                result_resolver=lambda _submission: "unused",
                transition_sink=record_failure,
            ),
            options=WorkItemPollOptions(
                request_timeout=4,
                poll_interval=0,
                deadline_seconds=10,
                sleep=no_sleep,
            ),
        )
    )
    assert failed == WorkItemOutcome(
        "failed", None, "analysis status lookup failed"
    )
    assert failure_transitions == [
        ("failed", None, "analysis status lookup failed")
    ]

    async def cancelled_status(_task_id: str, _timeout: float) -> None:
        raise asyncio.CancelledError

    cancellation_transitions: list[tuple[str, str | None, str | None]] = []

    async def record_cancellation(
        status: str,
        summary: str | None,
        reason: str | None,
    ) -> WorkItemOutcome:
        cancellation_transitions.append((status, summary, reason))
        return WorkItemOutcome(status, summary, reason)

    with pytest.raises(asyncio.CancelledError):
        await poll_work_item(
            WorkItemPollRequest(
                submission=submission,
                callbacks=WorkItemPollCallbacks(
                    status_reader=cancelled_status,
                    result_resolver=lambda _submission: "unused",
                    transition_sink=record_cancellation,
                ),
                options=WorkItemPollOptions(
                    request_timeout=4,
                    poll_interval=0,
                    deadline_seconds=10,
                    sleep=no_sleep,
                ),
            )
        )
    assert not cancellation_transitions


@pytest.mark.parametrize(
    ("outcomes", "expected"),
    [
        (
            [{"status": "pending"}],
            (False, 0, 0, False, False),
        ),
        (
            [
                {"status": "succeeded", "summary_markdown": "# usable"},
                {"status": "failed"},
                {"status": "running"},
            ],
            (False, 1, 1, False, True),
        ),
        (
            [
                {"status": "succeeded", "summary_markdown": "# usable"},
                {"status": "failed"},
            ],
            (True, 1, 1, True, True),
        ),
        (
            [{"status": "failed"}, {"status": "cancelled"}],
            (True, 0, 2, False, True),
        ),
    ],
)
def test_partial_outcome_matrix(
    outcomes: list[dict[str, Any]],
    expected: tuple[bool, int, int, bool, bool],
) -> None:
    """Only terminal usable work unlocks synthesis, with degradation counts."""
    derived = derive_workflow_outcome(outcomes)
    assert (
        derived.all_terminal,
        derived.usable_count,
        derived.unusable_count,
        derived.may_synthesize,
        derived.degraded,
    ) == expected


def test_design_mount_failure_projects_to_both_concrete_jobs() -> None:
    """One digital-design mount failure cannot hide either concrete job."""
    plan = build_work_item_plan("osa", "Os01g0100100", "Os01g0100100")
    aligned = concrete_work_item_outcomes(
        [asdict(item) for item in plan],
        {"mount": {"analysis_type": "digital_design", "status": "failed"}},
    )
    design_rows = {
        row["work_item_key"]: row["status"]
        for row in aligned
        if row["work_item_key"] in {"protein_design", "promoter_design"}
    }
    assert design_rows == {
        "protein_design": "failed",
        "promoter_design": "failed",
    }


def test_outcome_delta_keeps_both_remote_ids_and_sanitized_failure() -> None:
    """State projection retains caller/effective IDs without remote text."""
    submission = RemoteSubmission("caller-1", "source-1", "/obs/out")
    state: dict[str, Any] = {"task_index": 7}
    outcome = WorkItemOutcome(
        "failed",
        failure_reason="analysis task failed",
    )
    projector = getattr(dispatch_module, "_outcome_work_item_delta")

    delta = projector(
        work_item_key="smoc_analysis",
        submission=submission,
        outcome=outcome,
        state=state,
    )

    assert delta["analysis_completed_branches"] == 1
    assert delta["raw_analyst_data"] == {
        "task_7:smoc_analysis": {
            "status": "failed",
            "analysis_type": "smoc_analysis",
            "task_id": "caller-1",
            "poll_task_id": "source-1",
            "output_path": "/obs/out",
            "summary_markdown": None,
            "error": "analysis task failed",
        }
    }


def test_summary_projection_uses_stable_report_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Summary generation receives the immutable work-item figure order."""
    captured: dict[str, Any] = {}

    def build_summary(**kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(data={"smoc_summary": "summary"})

    monkeypatch.setattr(dispatch_module, "build_sub_summary", build_summary)
    harness = SimpleNamespace(
        deep_genome_config=SimpleNamespace(DEEPGENOME_OUT="/tmp/deep-genome")
    )
    state: dict[str, Any] = {
        "work_items": [{"work_item_key": "smoc_analysis", "display_order": 7}],
        "analyst_summaries": {"gene_name": "Os01g0100100"},
    }
    generate = getattr(
        dispatch_module.DeepGenomeDispatchMixin, "_generate_sub_summary"
    )

    result = generate(
        harness,
        "smoc_analysis",
        "Os01g0100100",
        state,
        results_dir="/obs/smoc",
    )

    assert result == {"smoc_summary": "summary"}
    assert captured["figure_index"] == 8
    assert captured["display_order"] == 7
