# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for obsfs-first upload download and conversion helpers."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni import utils

pytestmark = pytest.mark.unit


class FakeMarkItDown:
    """Small MarkItDown stand-in that reads plain text files."""

    def __init__(self, **kwargs: Any):
        """Capture constructor compatibility without external services."""
        del kwargs

    def convert(self, file_path: str):
        """Return file text through the MarkItDown result shape."""
        return SimpleNamespace(
            text_content=Path(file_path).read_text(encoding="utf-8")
        )


def test_convert_single_file_preserves_obsfs_source_when_cleanup_false(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify conversion can leave obsfs source files in place."""
    source_file = tmp_path / "paper.txt"
    source_file.write_text("paper text", encoding="utf-8")
    monkeypatch.setattr(utils, "MarkItDown", FakeMarkItDown)

    assert utils.convert_single_file(str(source_file), cleanup=False) == (
        "paper text"
    )

    assert source_file.exists()


def test_convert_single_file_removes_sdk_temp_when_cleanup_true(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify SDK temporary downloads still get removed after conversion."""
    source_file = tmp_path / "paper.txt"
    source_file.write_text("paper text", encoding="utf-8")
    monkeypatch.setattr(utils, "MarkItDown", FakeMarkItDown)

    assert utils.convert_single_file(str(source_file), cleanup=True) == (
        "paper text"
    )

    assert not source_file.exists()


async def test_download_obs_file_returns_obsfs_source_when_available(tmp_path):
    """Verify obsfs files are returned directly instead of staged locally."""
    obsfs_file = tmp_path / "phytomni" / "uploads" / "paper.txt"
    obsfs_file.parent.mkdir(parents=True)
    obsfs_file.write_text("paper text", encoding="utf-8")

    result = await utils.download_obs_file(
        "/obs/phytomni/uploads/paper.txt",
        server_dir=str(tmp_path / "temp"),
        obsfs_mount_root=str(tmp_path),
    )

    assert result == str(obsfs_file)


async def test_download_list_convert_passes_obsfs_source_without_cleanup(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify upload conversion reads obsfs files in place."""
    obsfs_file = tmp_path / "phytomni" / "uploads" / "paper.txt"
    obsfs_file.parent.mkdir(parents=True)
    obsfs_file.write_text("paper text", encoding="utf-8")
    captured: list[tuple[str, bool]] = []

    def fake_convert(file_path: str, cleanup: bool = True) -> str:
        captured.append((file_path, cleanup))
        return "converted paper"

    monkeypatch.setattr(utils, "convert_single_file", fake_convert)

    with ThreadPoolExecutor(max_workers=1) as executor:
        result = await utils.download_list_convert(
            ["/obs/phytomni/uploads/paper.txt"],
            server_dir=str(tmp_path / "temp"),
            obsfs_mount_root=str(tmp_path),
            executor=executor,
        )

    assert result == ["converted paper"]
    assert captured == [(str(obsfs_file), False)]
    assert obsfs_file.exists()


async def test_download_obs_file_falls_back_to_sdk_temp_path(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify unavailable obsfs paths still use the SDK download path."""
    captured: dict[str, Any] = {}

    class FakeObsClient:
        """Minimal OBS client constructor stand-in."""

        def __init__(self, **kwargs: Any):
            captured["client"] = kwargs

    async def fake_download_with_retry(
        obs_client: FakeObsClient,
        object_key: str,
        server_file: str,
        context: utils.ObsTransferContext,
    ) -> str:
        del obs_client, context
        captured["object_key"] = object_key
        Path(server_file).write_text("downloaded", encoding="utf-8")
        return server_file

    monkeypatch.setattr(utils, "ObsClient", FakeObsClient)
    monkeypatch.setattr(
        utils,
        "_download_obs_file_with_retry",
        fake_download_with_retry,
    )

    result = await utils.download_obs_file(
        "agent_data/paper.pdf",
        server_dir=str(tmp_path / "temp"),
        obsfs_mount_root=str(tmp_path / "missing"),
    )

    assert captured["object_key"] == "agent_data/paper.pdf"
    assert Path(result).read_text(encoding="utf-8") == "downloaded"
    assert str(result).startswith(str(tmp_path / "temp"))


async def test_download_list_convert_marks_sdk_downloads_for_cleanup(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify fallback SDK downloads are converted with cleanup enabled."""
    captured: dict[str, Any] = {}

    class FakeObsClient:
        """Minimal OBS client constructor stand-in."""

        def __init__(self, **kwargs: Any):
            del kwargs

    async def fake_download_with_retry(
        obs_client: FakeObsClient,
        object_key: str,
        server_file: str,
        context: utils.ObsTransferContext,
    ) -> str:
        del obs_client, object_key, context
        Path(server_file).write_text("downloaded", encoding="utf-8")
        return server_file

    def fake_convert(file_path: str, cleanup: bool = True) -> str:
        captured["file_path"] = file_path
        captured["cleanup"] = cleanup
        return "converted paper"

    monkeypatch.setattr(utils, "ObsClient", FakeObsClient)
    monkeypatch.setattr(
        utils,
        "_download_obs_file_with_retry",
        fake_download_with_retry,
    )
    monkeypatch.setattr(utils, "convert_single_file", fake_convert)

    with ThreadPoolExecutor(max_workers=1) as executor:
        result = await utils.download_list_convert(
            ["agent_data/paper.pdf"],
            server_dir=str(tmp_path / "temp"),
            obsfs_mount_root=str(tmp_path / "missing"),
            executor=executor,
        )

    assert result == ["converted paper"]
    assert captured["cleanup"] is True
    assert str(captured["file_path"]).startswith(str(tmp_path / "temp"))
