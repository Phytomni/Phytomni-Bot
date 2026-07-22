# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the mcp.handlers scratch resolver shim.

Verifies that the shared scratch_server_dir helper routes through obsfs
when the bucket is mounted and falls back to the configured local
TEMP_DIR when it is not.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.support.config_fakes import fake_scratch_config

from mcp_server_phytomni.mcp import handlers as mcp_handlers
from mcp_server_phytomni.storage import scratch as scratch_module

scratch_server_dir = mcp_handlers.scratch_server_dir

pytestmark = pytest.mark.unit


def testscratch_server_dir_uses_obsfs_when_bucket_mounted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify obsfs paths are returned when the bucket is mounted.

    Args:
        tmp_path: Pytest fixture used as the (unused) fallback root.
        monkeypatch: Pytest monkeypatch fixture used to force the
            scratch resolver's obsfs availability probe to True.
    """
    monkeypatch.setattr(
        scratch_module,
        "obsfs_bucket_available",
        lambda *_args, **_kwargs: True,
    )
    captured: dict[str, Path] = {}

    def fake_obsfs_path_for(
        key: str, _bucket: str, _mount: str | Path
    ) -> Path:
        """Return a writable tmp_path-rooted shadow of the OBS key."""
        path = tmp_path / "obsfs" / key.strip("/")
        captured["path"] = path
        return path

    monkeypatch.setattr(scratch_module, "obsfs_path_for", fake_obsfs_path_for)

    result = scratch_server_dir(fake_scratch_config(tmp_path), "chat")

    assert result.startswith("/obs/phytomni/agent_data/user_data/anonymous/")
    assert result.endswith("/chat/tmp/")
    assert captured["path"].is_dir()


def testscratch_server_dir_falls_back_locally_when_bucket_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify a local TEMP_DIR-scoped path is returned without obsfs.

    Args:
        tmp_path: Pytest fixture used as a fallback root.
        monkeypatch: Pytest monkeypatch fixture used to force the obsfs
            availability probe to False.
    """
    monkeypatch.setattr(
        scratch_module,
        "obsfs_bucket_available",
        lambda *_args, **_kwargs: False,
    )
    config = fake_scratch_config(tmp_path)

    result = scratch_server_dir(config, "knowledge")

    fallback_root = Path(config.TEMP_DIR)
    assert Path(result).is_relative_to(fallback_root)
    assert Path(result).name == "knowledge"
    assert Path(result).is_dir()
