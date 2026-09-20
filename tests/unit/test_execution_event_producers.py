# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Lifecycle and remote reconciliation producer tests."""

from __future__ import annotations

from pathlib import Path

from mcp_server_phytomni.runtime.execution_event_producers import (
    emit_input_required,
    emit_input_resolved,
    emit_remote_artifacts,
    emit_remote_progress,
    emit_run_settlement,
)
from mcp_server_phytomni.runtime.execution_event_store import (
    SQLiteExecutionEventStore,
)
from mcp_server_phytomni.runtime.execution_events import PublicArtifactPayload
from mcp_server_phytomni.runtime.run_registry import RunRegistry
from mcp_server_phytomni.runtime.run_registry_models import RunSpec


def _db(tmp_path: Path) -> str:
    path = str(tmp_path / "events.db")
    RunRegistry(path).create_run(
        RunSpec("run-1", "alice", "analyst", "remote")
    )
    return path


def test_lifecycle_producers_are_idempotent_and_ordered(
    tmp_path: Path,
) -> None:
    """Verify lifecycle producers are idempotent and ordered."""
    path = _db(tmp_path)
    emit_input_required(
        path,
        run_id="run-1",
        owner="alice",
        revision=1,
        surface_id="surface-1",
        widget="confirm",
    )
    emit_input_resolved(
        path,
        run_id="run-1",
        owner="alice",
        revision=2,
        surface_id="surface-1",
        outcome="accepted",
    )
    emit_run_settlement(
        path,
        run_id="run-1",
        owner="alice",
        status="succeeded",
        revision=3,
    )
    emit_run_settlement(
        path,
        run_id="run-1",
        owner="alice",
        status="succeeded",
        revision=3,
    )

    page = SQLiteExecutionEventStore(path).list_events("run-1", owner="alice")
    assert page is not None
    assert [event.kind for event in page.items] == [
        "run.waiting_input",
        "input.required",
        "input.resolved",
        "run.resumed",
        "run.succeeded",
    ]


def test_remote_revision_translation_exposes_no_paths(tmp_path: Path) -> None:
    """Verify remote revision translation exposes no paths."""
    path = _db(tmp_path)
    rows = [
        {"task_id": "task-1", "status": "succeeded"},
        {"task_id": "task-2", "status": "running"},
    ]
    emit_remote_progress(
        path,
        run_id="run-1",
        owner="alice",
        revision=4,
        task_rows=rows,
    )
    emit_remote_progress(
        path,
        run_id="run-1",
        owner="alice",
        revision=4,
        task_rows=rows,
    )
    emit_remote_artifacts(
        path,
        run_id="run-1",
        owner="alice",
        revision=5,
        artifacts=[
            {
                "task_id": "task-1",
                "paths": ["obs://private-bucket/alice/run-1/result.csv"],
            }
        ],
    )

    page = SQLiteExecutionEventStore(path).list_events("run-1", owner="alice")
    assert page is not None
    assert [event.kind for event in page.items] == [
        "phase.progress",
        "artifact.published",
    ]
    artifact = page.items[1]
    payload = artifact.payload
    assert isinstance(payload, PublicArtifactPayload)
    assert payload.name == "result.csv"
    assert artifact.target is not None
    assert artifact.target.id.startswith("remote-")
    assert "private-bucket" not in str(artifact.to_public_dict())
