# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Span and logical work-unit repository invariants."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


def _seed_execution(db_path: Path, owner: str, execution_id: str) -> None:
    from mcp_server_phytomni.runtime.run_registry import RunRegistry
    from mcp_server_phytomni.runtime.run_registry_models import local_run_spec

    registry = RunRegistry(str(db_path))
    registry.create_run(local_run_spec(f"run-{execution_id}", owner, "chat"))
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE runs SET execution_id = ? WHERE run_id = ?",
            (execution_id, f"run-{execution_id}"),
        )
        connection.commit()


def test_spans_have_stable_identity_parent_validation_and_revision(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_work_store_v2 import (
        ExecutionWorkConflictError,
        ExecutionWorkInvariantError,
        SpanSpec,
        SQLiteExecutionWorkRepository,
    )

    db_path = tmp_path / "spans.db"
    _seed_execution(db_path, "alice", "turn-spans")
    repository = SQLiteExecutionWorkRepository(str(db_path))
    root = repository.create_span(
        SpanSpec(
            owner="alice",
            execution_id="turn-spans",
            span_id="span-root",
            kind="agent",
            label_key="activity.agent",
        )
    )
    assert repository.create_span(root.to_spec()) == root

    child = repository.create_span(
        SpanSpec(
            owner="alice",
            execution_id="turn-spans",
            span_id="span-child",
            parent_span_id="span-root",
            kind="phase",
            label_key="activity.phase",
        )
    )
    running = repository.update_span_status(
        "turn-spans",
        "span-child",
        owner="alice",
        status="running",
        expected_revision=child.revision,
    )
    assert running.status == "running"
    assert running.revision == 1
    with pytest.raises(ExecutionWorkConflictError):
        repository.update_span_status(
            "turn-spans",
            "span-child",
            owner="alice",
            status="succeeded",
            expected_revision=child.revision,
        )
    with pytest.raises(ExecutionWorkInvariantError):
        repository.create_span(
            SpanSpec(
                owner="alice",
                execution_id="turn-spans",
                span_id="span-orphan",
                parent_span_id="span-missing",
                kind="phase",
                label_key="activity.orphan",
            )
        )


def test_work_unit_attempt_lease_provider_cancel_and_deadline(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_work_store_v2 import (
        ExecutionWorkConflictError,
        SpanSpec,
        SQLiteExecutionWorkRepository,
        WorkUnitSpec,
    )

    now = datetime(2026, 8, 19, tzinfo=UTC)
    db_path = tmp_path / "work.db"
    _seed_execution(db_path, "alice", "turn-work")
    repository = SQLiteExecutionWorkRepository(str(db_path), clock=lambda: now)
    repository.create_span(
        SpanSpec(
            owner="alice",
            execution_id="turn-work",
            span_id="span-root",
            kind="agent",
            label_key="activity.agent",
        )
    )
    spec = WorkUnitSpec(
        owner="alice",
        execution_id="turn-work",
        work_unit_id="work-search",
        parent_span_id="span-root",
        operation_key="tool.search",
        driver="local_graph",
        join_policy="best_effort",
        max_attempts=3,
        deadline_at=(now + timedelta(minutes=5)).isoformat(),
    )
    created = repository.create_work_unit(spec)
    assert repository.create_work_unit(spec) == created
    claimed = repository.claim_lease(
        "turn-work",
        "work-search",
        owner="alice",
        worker_id="worker-a",
        lease_seconds=30,
        expected_revision=created.revision,
    )
    assert claimed.lease_owner == "worker-a"
    assert claimed.join_policy == "best_effort"
    with pytest.raises(ExecutionWorkConflictError):
        repository.claim_lease(
            "turn-work",
            "work-search",
            owner="alice",
            worker_id="worker-b",
            lease_seconds=30,
            expected_revision=claimed.revision,
        )

    attempted = repository.start_attempt(
        "turn-work",
        "work-search",
        owner="alice",
        expected_revision=claimed.revision,
    )
    assert attempted.attempt == 2
    bound = repository.bind_provider(
        "turn-work",
        "work-search",
        owner="alice",
        provider_kind="remote-service",
        provider_task_id="provider-task-1",
        provider_revision=4,
        expected_revision=attempted.revision,
    )
    cancelled = repository.set_cancellation_state(
        "turn-work",
        "work-search",
        owner="alice",
        state="requested",
        expected_revision=bound.revision,
    )
    assert cancelled.provider_task_id == "provider-task-1"
    assert cancelled.provider_revision == 4
    assert cancelled.cancellation_state == "requested"
    assert cancelled.deadline_at == spec.deadline_at


def test_provider_trace_state_round_trips_privately_with_cas(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_work_store_v2 import (
        SpanSpec,
        SQLiteExecutionWorkRepository,
        WorkUnitSpec,
    )

    now = datetime(2026, 8, 23, tzinfo=UTC)
    db_path = tmp_path / "provider-trace-state.db"
    _seed_execution(db_path, "alice", "turn-provider-trace")
    repository = SQLiteExecutionWorkRepository(str(db_path), clock=lambda: now)
    repository.create_span(
        SpanSpec(
            owner="alice",
            execution_id="turn-provider-trace",
            span_id="span-root",
            kind="agent",
            label_key="activity.agent",
        )
    )
    created = repository.create_work_unit(
        WorkUnitSpec(
            owner="alice",
            execution_id="turn-provider-trace",
            work_unit_id="work-analysis",
            parent_span_id="span-root",
            operation_key="remote.analysis",
            driver="provider",
        )
    )

    updated = repository.update_provider_trace_state(
        created.execution_id,
        created.work_unit_id,
        owner=created.owner,
        cursor="cursor-12",
        source_revision=12,
        adapter_version="analysis-full-v1",
        overlap_identities=("record-10", "record-11", "record-12"),
        contact_at="2026-08-23T00:00:00+00:00",
        health="healthy",
        expected_revision=created.revision,
    )

    assert updated.provider_trace_cursor == "cursor-12"
    assert updated.provider_trace_revision == 12
    assert updated.provider_trace_adapter_version == "analysis-full-v1"
    assert updated.provider_trace_overlap_identities == (
        "record-10",
        "record-11",
        "record-12",
    )
    assert updated.provider_trace_contact_at == "2026-08-23T00:00:00+00:00"
    assert updated.provider_trace_health == "healthy"


def test_provider_contact_refresh_is_monotonic_without_claiming_revision(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_work_store_v2 import (
        SpanSpec,
        SQLiteExecutionWorkRepository,
        WorkUnitSpec,
    )

    db_path = tmp_path / "provider-contact.db"
    _seed_execution(db_path, "alice", "turn-provider-contact")
    repository = SQLiteExecutionWorkRepository(str(db_path))
    repository.create_span(
        SpanSpec(
            owner="alice",
            execution_id="turn-provider-contact",
            span_id="span-root",
            kind="agent",
            label_key="activity.agent",
        )
    )
    created = repository.create_work_unit(
        WorkUnitSpec(
            owner="alice",
            execution_id="turn-provider-contact",
            work_unit_id="work-analysis",
            parent_span_id="span-root",
            operation_key="remote.analysis",
            driver="provider",
        )
    )

    assert repository.observe_provider_contact(
        created.execution_id,
        created.work_unit_id,
        owner=created.owner,
        observed_at="2026-08-24T08:00:30+00:00",
    )
    refreshed = repository.get_work_unit(
        created.execution_id,
        created.work_unit_id,
        owner=created.owner,
    )
    assert refreshed.provider_trace_contact_at == "2026-08-24T08:00:30+00:00"
    assert refreshed.revision == created.revision
    assert (
        repository.latest_provider_contact_at(
            created.execution_id,
            owner=created.owner,
        )
        == "2026-08-24T08:00:30+00:00"
    )

    assert not repository.observe_provider_contact(
        created.execution_id,
        created.work_unit_id,
        owner=created.owner,
        observed_at="2026-08-24T08:00:00+00:00",
    )
    unchanged = repository.get_work_unit(
        created.execution_id,
        created.work_unit_id,
        owner=created.owner,
    )
    assert (
        unchanged.provider_trace_contact_at
        == refreshed.provider_trace_contact_at
    )
    assert unchanged.revision == created.revision

    checkpointed = repository.update_provider_trace_state(
        created.execution_id,
        created.work_unit_id,
        owner=created.owner,
        cursor="cursor-1",
        source_revision=1,
        adapter_version="analysis-full-v1",
        overlap_identities=("record-1",),
        contact_at="2026-08-24T08:00:10+00:00",
        health="healthy",
        expected_revision=created.revision,
    )
    assert (
        checkpointed.provider_trace_contact_at
        == refreshed.provider_trace_contact_at
    )


def test_invalid_join_policy_or_foreign_execution_fails_closed(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_work_store_v2 import (
        ExecutionWorkNotFoundError,
        SpanSpec,
        SQLiteExecutionWorkRepository,
        WorkUnitSpec,
    )

    db_path = tmp_path / "invalid.db"
    _seed_execution(db_path, "alice", "turn-owned")
    repository = SQLiteExecutionWorkRepository(str(db_path))
    repository.create_span(
        SpanSpec(
            owner="alice",
            execution_id="turn-owned",
            span_id="span-root",
            kind="agent",
            label_key="activity.agent",
        )
    )
    with pytest.raises(ValueError):
        WorkUnitSpec(
            owner="alice",
            execution_id="turn-owned",
            work_unit_id="work-invalid",
            parent_span_id="span-root",
            operation_key="tool.invalid",
            driver="local_graph",
            join_policy="sometimes",  # type: ignore[arg-type]
        )
    with pytest.raises(ExecutionWorkNotFoundError):
        repository.get_span("turn-owned", "span-root", owner="mallory")


def test_provider_due_scan_excludes_in_process_tool_work(
    tmp_path: Path,
) -> None:
    """Provider recovery must not lease work owned by the live request."""
    from mcp_server_phytomni.runtime.execution_work_store_v2 import (
        SpanSpec,
        SQLiteExecutionWorkRepository,
        WorkUnitSpec,
    )

    now = datetime(2026, 8, 22, tzinfo=UTC)
    db_path = tmp_path / "provider-due.db"
    _seed_execution(db_path, "alice", "turn-provider-due")
    repository = SQLiteExecutionWorkRepository(str(db_path), clock=lambda: now)
    repository.create_span(
        SpanSpec(
            owner="alice",
            execution_id="turn-provider-due",
            span_id="span-root",
            kind="agent",
            label_key="agent.review",
        )
    )
    local = repository.create_work_unit(
        WorkUnitSpec(
            owner="alice",
            execution_id="turn-provider-due",
            work_unit_id="work-tool-review",
            parent_span_id="span-root",
            operation_key="tool.review",
            driver="tool",
        )
    )
    repository.update_work_unit_status(
        local.execution_id,
        local.work_unit_id,
        owner=local.owner,
        status="running",
        expected_revision=local.revision,
    )
    provider = repository.create_work_unit(
        WorkUnitSpec(
            owner="alice",
            execution_id="turn-provider-due",
            work_unit_id="work-provider-analysis",
            parent_span_id="span-root",
            operation_key="provider.analysis.submit",
            driver="remote_task",
        )
    )
    repository.bind_provider(
        provider.execution_id,
        provider.work_unit_id,
        owner=provider.owner,
        provider_kind="analysis_task_platform",
        provider_task_id="task-provider-1",
        provider_revision=1,
        expected_revision=provider.revision,
    )

    due = repository.list_due_provider_work_units(now=now, limit=10)

    assert [unit.work_unit_id for unit in due] == ["work-provider-analysis"]
