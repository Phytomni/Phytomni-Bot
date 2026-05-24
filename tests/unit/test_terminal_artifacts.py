# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for ``runtime.terminal_artifacts``.

``collect_terminal_artifacts`` walks the reconciled task_results blob
that ``_terminal_payload`` produces and emits one artifact descriptor
per succeeded task that carries an ``output_dir``. Failed tasks and
succeeded tasks missing an output path are skipped; the ``paths`` field
is intentionally empty pending an out-of-process glob.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.runtime.terminal_artifacts import (
    collect_terminal_artifacts,
)

pytestmark = pytest.mark.unit


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
