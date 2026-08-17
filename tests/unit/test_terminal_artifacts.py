# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for ``runtime.terminal_artifacts``.

``collect_terminal_artifacts`` walks the reconciled task_results blob
that ``_terminal_payload`` produces and emits one artifact descriptor
per succeeded task that carries an ``output_dir``. Its ``paths`` field
is populated from a prior ``enumerate_artifact_paths`` pass and stays
empty only when that pass has not run (e.g. ``GetTaskStatus``).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.analyst.graph import AnalystGraphMixin
from mcp_server_phytomni.runtime import terminal_artifacts
from mcp_server_phytomni.runtime.artifact_roles import ArtifactRole
from mcp_server_phytomni.runtime.terminal_artifacts import (
    ArtifactLister,
    TerminalArtifactSet,
    collect_terminal_artifacts,
)
from mcp_server_phytomni.storage.artifact_listing import ListedArtifactObject

pytestmark = pytest.mark.unit


@pytest.fixture(name="artifacts_caplog")
def _artifacts_caplog(
    caplog: pytest.LogCaptureFixture,
) -> Iterator[pytest.LogCaptureFixture]:
    """Capture warnings from the terminal_artifacts logger.

    ``common/logging_config.configure_logging`` sets the package logger
    ``propagate=False``, so once an earlier suite test triggers it (e.g.
    via an api/app import) warnings never reach pytest's root-attached
    caplog. Attaching the caplog handler directly to this module's
    logger sidesteps the propagation gap; it is removed at teardown.
    """
    module_logger = terminal_artifacts.logger
    module_logger.addHandler(caplog.handler)
    module_logger.setLevel(logging.WARNING)
    try:
        yield caplog
    finally:
        module_logger.removeHandler(caplog.handler)


def test_collects_succeeded_task_with_output_dir() -> None:
    """One succeeded row with output_dir becomes one artifact entry."""
    rows = [
        {"task_id": "t-1", "status": "succeeded", "output_dir": "/obs/x"},
    ]
    assert collect_terminal_artifacts(rows) == [
        {"task_id": "t-1", "output_dir": "/obs/x", "paths": []},
    ]


def test_skips_failed_tasks() -> None:
    """A failed task contributes no artifact descriptor.

    The artifacts index advertises product paths to clients; failed
    tasks have no products to point at, so emitting an entry would be
    misleading.
    """
    rows = [
        {"task_id": "t-2", "status": "failed", "output_dir": "/obs/y"},
    ]
    assert not collect_terminal_artifacts(rows)


def test_skips_succeeded_without_output_dir() -> None:
    """A succeeded row missing output_dir is skipped, not crashed.

    Defensive against synthesized rows (e.g. retry pathways) that have
    a terminal status but no path field; the helper must stay
    type-safe so the terminal-write hot path never raises.
    """
    rows = [{"task_id": "t-3", "status": "completed"}]
    assert not collect_terminal_artifacts(rows)


def test_accepts_full_success_vocabulary() -> None:
    """Every success-like status maps to an artifact entry.

    Mirrors ``_SUCCESS_STATUSES`` in ``runtime/run_registry`` so the
    index does not silently drop ``success`` / ``completed`` / ``done``
    rows on backends that use those instead of ``succeeded``.
    """
    rows = [
        {"task_id": "a", "status": "succeeded", "output_dir": "/a"},
        {"task_id": "b", "status": "success", "output_dir": "/b"},
        {"task_id": "c", "status": "completed", "output_dir": "/c"},
        {"task_id": "d", "status": "done", "output_dir": "/d"},
    ]
    result = collect_terminal_artifacts(rows)
    assert [entry["task_id"] for entry in result] == ["a", "b", "c", "d"]


def test_mixed_batch_filters_correctly() -> None:
    """A batch with mixed statuses surfaces only the succeeded artifacts."""
    rows = [
        {"task_id": "t-1", "status": "succeeded", "output_dir": "/a"},
        {"task_id": "t-2", "status": "failed", "output_dir": "/b"},
        {"task_id": "t-3", "status": "running", "output_dir": "/c"},
        {"task_id": "t-4", "status": "done", "output_dir": "/d"},
    ]
    assert collect_terminal_artifacts(rows) == [
        {"task_id": "t-1", "output_dir": "/a", "paths": []},
        {"task_id": "t-4", "output_dir": "/d", "paths": []},
    ]


def test_empty_input_returns_empty_list() -> None:
    """No tasks means no artifacts; the helper must accept the empty case."""
    assert not collect_terminal_artifacts([])


@pytest.mark.asyncio
async def test_enumerate_fills_paths_for_succeeded_rows() -> None:
    """Only succeeded rows with an output_dir gain ``artifact_paths``."""
    live = [
        {"task_id": "t1", "status": "succeeded", "output_dir": "/obs/p/r1"},
        {"task_id": "t2", "status": "running", "output_dir": "/obs/p/r2"},
        {"task_id": "t3", "status": "succeeded", "output_dir": ""},
    ]

    async def lister(output_dir: str) -> list[str]:
        return [f"{output_dir}/fig.png"]

    typed_lister: ArtifactLister = lister

    out = await terminal_artifacts.enumerate_artifact_paths(
        live, lister=typed_lister
    )

    assert out[0]["artifact_paths"] == ["/obs/p/r1/fig.png"]
    assert "artifact_paths" not in out[1]  # not succeeded -> untouched
    assert out[2].get("artifact_paths", []) == []  # no output_dir -> skipped


@pytest.mark.asyncio
async def test_enumerate_swallows_lister_errors(
    artifacts_caplog: pytest.LogCaptureFixture,
) -> None:
    """A listing failure degrades to empty paths and logs, never raises."""
    live = [
        {"task_id": "t1", "status": "succeeded", "output_dir": "/obs/p/r1"},
    ]

    async def boom(output_dir: str) -> list[str]:
        raise OSError(f"obs down for {output_dir}")

    typed_lister: ArtifactLister = boom

    out = await terminal_artifacts.enumerate_artifact_paths(
        live, lister=typed_lister
    )

    assert out[0]["artifact_paths"] == []
    assert any("t1" in rec.message for rec in artifacts_caplog.records)


@pytest.mark.asyncio
async def test_enumerate_caps_and_logs_truncation(
    artifacts_caplog: pytest.LogCaptureFixture,
) -> None:
    """Over-cap results are truncated with a non-silent warning."""
    live = [
        {"task_id": "t1", "status": "succeeded", "output_dir": "/obs/p/r1"},
    ]

    async def many(output_dir: str) -> list[str]:
        return [f"{output_dir}/f{i}.png" for i in range(5)]

    typed_lister: ArtifactLister = many

    out = await terminal_artifacts.enumerate_artifact_paths(
        live, lister=typed_lister, cap=2
    )

    assert len(out[0]["artifact_paths"]) == 2
    assert any(
        "truncated" in rec.message.lower() for rec in artifacts_caplog.records
    )


def test_collect_reads_enumerated_paths() -> None:
    """``collect_terminal_artifacts`` surfaces a prior enumeration."""
    live = [
        {
            "task_id": "t1",
            "status": "succeeded",
            "output_dir": "/obs/p/r1",
            "artifact_paths": ["/obs/p/r1/fig.png"],
        }
    ]
    artifacts = collect_terminal_artifacts(live)
    assert artifacts[0]["paths"] == ["/obs/p/r1/fig.png"]


def test_submit_meta_keeps_manifest_contract_without_zip_authority() -> None:
    """Producers declare artifacts but never construct the Bot archive."""
    submit_meta = getattr(AnalystGraphMixin, "_submit_meta")
    prompt = submit_meta(cast(Any, {"plan": "plan", "tool_usages": "tools"}))
    normalized_prompt = " ".join(prompt.split())

    assert ".phytomni-artifacts.json" in prompt
    assert "scientific_data" in prompt
    assert "`result_archive` is reserved for Bot" in normalized_prompt
    assert "zip -r" not in prompt


def _listed_object(relative_path: str) -> ListedArtifactObject:
    """Build one listed object with private provenance for structured tests."""
    return ListedArtifactObject(
        relative_path=relative_path,
        source_path=f"/private/obsfs/{relative_path}",
        size_bytes=1024,
        download_ref=f"/obs/phytomni/run/{relative_path}",
    )


def _manifest_item(path: str) -> dict[str, str]:
    """Build one manifest declaration for a negative-path test."""
    return {
        "path": path,
        "role": "scientific_text",
        "media_type": "text/plain",
    }


@pytest.mark.asyncio
async def test_unknown_is_listed_but_not_report_eligible() -> None:
    """Unmanifested files remain visible while staying out of reports."""

    async def fake_objects(_output_dir: str) -> list[ListedArtifactObject]:
        return [_listed_object("summary.csv"), _listed_object("analysis.log")]

    async def missing_manifest(
        _output_dir: str,
    ) -> None:
        return None

    result = await collect_terminal_artifacts(
        task_id="task-1",
        output_dir="owner/out",
        lister=fake_objects,
        manifest_loader=missing_manifest,
    )

    assert isinstance(result, TerminalArtifactSet)
    assert [item.role for item in result.artifacts] == [
        ArtifactRole.UNKNOWN,
        ArtifactRole.UNKNOWN,
    ]
    assert not any(item.report_context_eligible for item in result.artifacts)
    assert result.warnings[0].code == "artifact_manifest_missing"


@pytest.mark.parametrize(
    "bad_path",
    ["/etc/passwd", "../escape.txt", "a/../../b.txt", "a\\b.txt"],
)
@pytest.mark.asyncio
async def test_manifest_path_escape_fails_closed(bad_path: str) -> None:
    """Unsafe manifest paths leave every listed object unknown."""

    async def fake_objects(_output_dir: str) -> list[ListedArtifactObject]:
        return [_listed_object("summary.csv")]

    async def manifest_loader(_output_dir: str) -> dict[str, Any]:
        return {
            "version": "1.0",
            "artifacts": [_manifest_item(bad_path)],
        }

    result = await collect_terminal_artifacts(
        task_id="task-1",
        output_dir="owner/out",
        lister=fake_objects,
        manifest_loader=manifest_loader,
    )

    assert isinstance(result, TerminalArtifactSet)
    assert all(item.role is ArtifactRole.UNKNOWN for item in result.artifacts)
    assert result.warnings[0].code == "artifact_manifest_invalid"


@pytest.mark.asyncio
async def test_result_archive_manifest_role_fails_closed() -> None:
    """A producer cannot elevate an object to the Bot-owned archive role."""

    async def fake_objects(_output_dir: str) -> list[ListedArtifactObject]:
        return [_listed_object("results.zip")]

    async def manifest_loader(_output_dir: str) -> dict[str, Any]:
        return {
            "version": "1.0",
            "artifacts": [
                {
                    "path": "results.zip",
                    "role": "result_archive",
                    "media_type": "application/zip",
                }
            ],
        }

    result = await collect_terminal_artifacts(
        task_id="task-1",
        output_dir="owner/out",
        lister=fake_objects,
        manifest_loader=manifest_loader,
    )

    assert result.artifacts[0].role is ArtifactRole.UNKNOWN
    assert result.warnings[0].code == "artifact_manifest_invalid"
