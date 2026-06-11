# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for obsfs-first scratch directory resolution.

Covers both availability branches and both kind values to ensure scratch
paths land on obsfs when the bucket is mounted and on local disk
otherwise.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mcp_server_phytomni.storage import scratch as scratch_module
from mcp_server_phytomni.storage.path_policy import (
    IdFactory,
    RunIdentity,
)
from mcp_server_phytomni.storage.scratch import (
    ScratchKind,
    ScratchTarget,
    resolve_scratch_dir,
)

pytestmark = pytest.mark.unit


def _fixed_identity() -> RunIdentity:
    """Return a deterministic RunIdentity for scratch path assertions."""
    factory = IdFactory(
        now=lambda: datetime(2026, 5, 7, 1, 2, 3, tzinfo=timezone.utc),
        token_factory=lambda _: "abcdef01",
    )
    return RunIdentity.create("alice", "scratch-run", factory)


@pytest.mark.parametrize(
    ("kind", "leaf"),
    [
        ("downloads", "downloads"),
        ("tmp", "tmp"),
    ],
)
def test_resolve_scratch_dir_uses_obsfs_when_bucket_mounted(
    kind: ScratchKind,
    leaf: str,
    tmp_path: Path,
):
    """Verify obsfs paths are returned and created when the bucket exists.

    Args:
        kind: Scratch kind exercised in this parametrized run.
        leaf: Expected leaf segment on the returned OBS path.
        tmp_path: Pytest fixture supplying a fake obsfs mount root.
    """
    bucket = "phytomni"
    (tmp_path / bucket).mkdir()
    local_fallback = tmp_path / "fallback"
    identity = _fixed_identity()
    target = ScratchTarget(
        bucket_name=bucket,
        local_fallback=local_fallback,
        obsfs_mount_root=tmp_path,
    )

    result = resolve_scratch_dir(
        kind,
        identity,
        "task-one",
        target,
    )

    expected_obs_path = (
        "/obs/phytomni/agent_data/user_data/alice/runs/20260507/"
        "20260507T010203Z-scratch-run-alice-abcdef01/task-one/"
        f"{leaf}/"
    )
    expected_local_dir = (
        tmp_path
        / bucket
        / "agent_data"
        / "user_data"
        / "alice"
        / "runs"
        / "20260507"
        / "20260507T010203Z-scratch-run-alice-abcdef01"
        / "task-one"
        / leaf
    )

    assert result == expected_obs_path
    assert expected_local_dir.is_dir()
    assert not local_fallback.exists()


def test_resolve_scratch_dir_falls_back_to_local_when_bucket_missing(
    tmp_path: Path,
):
    """Verify local scratch dirs are created when obsfs is unavailable.

    Args:
        tmp_path: Pytest fixture used as a mount root with no bucket dir.
    """
    bucket = "phytomni"
    local_fallback = tmp_path / "fallback"
    identity = _fixed_identity()
    target = ScratchTarget(
        bucket_name=bucket,
        local_fallback=local_fallback,
        obsfs_mount_root=tmp_path,
    )

    result = resolve_scratch_dir(
        "downloads",
        identity,
        "task-two",
        target,
    )

    expected_dir = (
        local_fallback
        / "20260507T010203Z-scratch-run-alice-abcdef01"
        / "task-two"
    )
    assert result == str(expected_dir)
    assert expected_dir.is_dir()
    assert not (tmp_path / bucket).exists()


def test_resolve_scratch_dir_falls_back_when_obsfs_raises_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An OSError from the obsfs mkdir routes to the local fallback.

    Pins the defensive try/except around _resolve_obsfs_scratch_dir:
    if the obsfs mount disappears mid-call (NFS hiccup, EACCES on the
    parent dir), the resolver must still produce a usable scratch dir
    on local disk rather than bubble the OSError up to the agent, and
    it must log a WARNING so the silent fallback stays operator-visible.
    """
    bucket = "phytomni"
    (tmp_path / bucket).mkdir()
    local_fallback = tmp_path / "fallback"

    def boom(*_args: object, **_kwargs: object) -> str:
        raise OSError("obsfs unavailable")

    monkeypatch.setattr(scratch_module, "_resolve_obsfs_scratch_dir", boom)
    # configure_logging sets propagate=False on the package logger; re-enable
    # it through monkeypatch so caplog's root handler sees the WARNING.
    package_logger = logging.getLogger("mcp_server_phytomni")
    monkeypatch.setattr(package_logger, "propagate", True)

    with caplog.at_level(
        logging.WARNING, logger="mcp_server_phytomni.storage.scratch"
    ):
        result = resolve_scratch_dir(
            "tmp",
            _fixed_identity(),
            "task-three",
            ScratchTarget(
                bucket_name=bucket,
                local_fallback=local_fallback,
                obsfs_mount_root=tmp_path,
            ),
        )

    expected_dir = (
        local_fallback
        / "20260507T010203Z-scratch-run-alice-abcdef01"
        / "task-three"
    )
    assert result == str(expected_dir)
    assert expected_dir.is_dir()
    fallback_warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
        and "obsfs scratch unavailable" in record.getMessage()
    ]
    assert fallback_warnings, "expected a WARNING about the obsfs fallback"
    assert bucket in fallback_warnings[0].getMessage()
