# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for obsfs-first upload download and conversion helpers.

Covers direct obsfs conversion, SDK temporary-file cleanup, OBS download
fallback, and download-list conversion cleanup flags.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.storage import downloads

pytestmark = pytest.mark.unit


class FakeMarkItDown:
    """Small MarkItDown stand-in that reads plain text files.

    Attributes:
        No state is required; file text is read during conversion.
    """

    def __init__(self, **kwargs: Any):
        """Capture constructor compatibility without external services."""
        del kwargs

    def convert(self, file_path: str):
        """Return file text through the MarkItDown result shape.

        Args:
            file_path: Local file path to read.

        Returns:
            Object with text_content matching the file contents.
        """
        return SimpleNamespace(
            text_content=Path(file_path).read_text(encoding="utf-8")
        )

    def converter_name(self) -> str:
        """Return the fake converter name.

        Returns:
            Stable fake converter name.
        """
        return "fake-markitdown"


def test_convert_single_file_preserves_obsfs_source_when_cleanup_false(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify conversion can leave obsfs source files in place.

    Args:
        tmp_path: Temporary source-file directory.
        monkeypatch: Pytest monkeypatch fixture used to replace MarkItDown.
    """
    source_file = tmp_path / "paper.txt"
    source_file.write_text("paper text", encoding="utf-8")
    monkeypatch.setattr(downloads, "MarkItDown", FakeMarkItDown)

    assert downloads.convert_single_file(str(source_file), cleanup=False) == (
        "paper text"
    )

    assert source_file.exists()


def test_convert_single_file_removes_sdk_temp_when_cleanup_true(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify SDK temporary downloads still get removed after conversion.

    Args:
        tmp_path: Temporary source-file directory.
        monkeypatch: Pytest monkeypatch fixture used to replace MarkItDown.
    """
    source_file = tmp_path / "paper.txt"
    source_file.write_text("paper text", encoding="utf-8")
    monkeypatch.setattr(downloads, "MarkItDown", FakeMarkItDown)

    assert downloads.convert_single_file(str(source_file), cleanup=True) == (
        "paper text"
    )

    assert not source_file.exists()


async def test_download_obs_file_returns_obsfs_source_when_available(tmp_path):
    """Verify obsfs files are returned directly instead of staged locally.

    Args:
        tmp_path: Temporary obsfs mount root and temp directory.
    """
    obsfs_file = tmp_path / "phytomni" / "uploads" / "paper.txt"
    obsfs_file.parent.mkdir(parents=True)
    obsfs_file.write_text("paper text", encoding="utf-8")

    result = await downloads.download_obs_file(
        "/obs/phytomni/uploads/paper.txt",
        server_dir=str(tmp_path / "temp"),
        obsfs_mount_root=str(tmp_path),
    )

    assert result == str(obsfs_file)


async def test_download_list_convert_passes_obsfs_source_without_cleanup(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify upload conversion reads obsfs files in place.

    Args:
        tmp_path: Temporary obsfs mount root and temp directory.
        monkeypatch: Pytest monkeypatch fixture used to replace conversion.

    Returns:
        None after source path and cleanup assertions pass.
    """
    obsfs_file = tmp_path / "phytomni" / "uploads" / "paper.txt"
    obsfs_file.parent.mkdir(parents=True)
    obsfs_file.write_text("paper text", encoding="utf-8")
    captured: list[tuple[str, bool]] = []

    def fake_convert(file_path: str, cleanup: bool = True) -> str:
        """Capture conversion cleanup behavior.

        Args:
            file_path: Source file path passed to conversion.
            cleanup: Whether conversion should delete the source file.

        Returns:
            Static converted text.
        """
        captured.append((file_path, cleanup))
        return "converted paper"

    monkeypatch.setattr(downloads, "convert_single_file", fake_convert)

    with ThreadPoolExecutor(max_workers=1) as executor:
        result = await downloads.download_list_convert(
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
    """Verify unavailable obsfs paths still use the SDK download path.

    Args:
        tmp_path: Temporary download root.
        monkeypatch: Pytest monkeypatch fixture used to replace OBS client.

    Returns:
        None after SDK temp path assertions pass.
    """
    captured: dict[str, Any] = {}

    class FakeObsClient:
        """Minimal OBS client constructor stand-in.

        Attributes:
            Captured constructor settings are stored externally.
        """

        def __init__(self, **kwargs: Any):
            captured["client"] = kwargs

        def client_settings(self) -> dict[str, Any]:
            """Return captured constructor settings.

            Returns:
                Captured OBS client kwargs.
            """
            return captured["client"]

        def client_name(self) -> str:
            """Return a fake client name.

            Returns:
                Stable fake client name.
            """
            return "fake-obs-client"

    async def fake_download_with_retry(
        obs_client: FakeObsClient,
        object_key: str,
        server_file: str,
        context: downloads.ObsTransferContext,
    ) -> str:
        """Write a fake SDK download file.

        Args:
            obs_client: Fake OBS client instance.
            object_key: Normalized OBS object key.
            server_file: Local download target path.
            context: OBS transfer context.

        Returns:
            Local path to the fake downloaded file.
        """
        del obs_client, context
        captured["object_key"] = object_key
        await asyncio.to_thread(
            Path(server_file).write_text, "downloaded", encoding="utf-8"
        )
        return server_file

    monkeypatch.setattr(downloads, "ObsClient", FakeObsClient)
    monkeypatch.setattr(
        downloads,
        "_download_obs_file_with_retry",
        fake_download_with_retry,
    )

    result = await downloads.download_obs_file(
        "agent_data/paper.pdf",
        server_dir=str(tmp_path / "temp"),
        obsfs_mount_root=str(tmp_path / "missing"),
    )

    assert captured["object_key"] == "agent_data/paper.pdf"
    assert (
        await asyncio.to_thread(Path(result).read_text, encoding="utf-8")
        == "downloaded"
    )
    assert str(result).startswith(str(tmp_path / "temp"))
    result_path = Path(result)
    assert result_path.parent.parent.parent == tmp_path / "temp" / "agent_data"
    assert result_path.parent.parent.name.isdigit()
    assert "-obs-download-agent_data-" in result_path.parent.name


async def test_download_list_convert_marks_sdk_downloads_for_cleanup(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify fallback SDK downloads are converted with cleanup enabled.

    Args:
        tmp_path: Temporary download root.
        monkeypatch: Pytest monkeypatch fixture used to replace helpers.

    Returns:
        None after cleanup flag assertions pass.
    """
    captured: dict[str, Any] = {}

    class FakeObsClient:
        """Minimal OBS client constructor stand-in.

        Attributes:
            No instance attributes are needed for this fake.
        """

        def __init__(self, **kwargs: Any):
            del kwargs

        def client_settings(self) -> dict[str, Any]:
            """Return fake constructor settings.

            Returns:
                Empty constructor settings.
            """
            return {}

        def client_name(self) -> str:
            """Return a fake client name.

            Returns:
                Stable fake client name.
            """
            return "fake-obs-client"

    async def fake_download_with_retry(
        obs_client: FakeObsClient,
        object_key: str,
        server_file: str,
        context: downloads.ObsTransferContext,
    ) -> str:
        """Write a fake SDK download file.

        Args:
            obs_client: Fake OBS client instance.
            object_key: Normalized OBS object key.
            server_file: Local download target path.
            context: OBS transfer context.

        Returns:
            Local path to the fake downloaded file.
        """
        del obs_client, object_key, context
        await asyncio.to_thread(
            Path(server_file).write_text, "downloaded", encoding="utf-8"
        )
        return server_file

    def fake_convert(file_path: str, cleanup: bool = True) -> str:
        """Capture SDK conversion cleanup behavior.

        Args:
            file_path: Source file path passed to conversion.
            cleanup: Whether conversion should delete the source file.

        Returns:
            Static converted text.
        """
        captured["file_path"] = file_path
        captured["cleanup"] = cleanup
        return "converted paper"

    monkeypatch.setattr(downloads, "ObsClient", FakeObsClient)
    monkeypatch.setattr(
        downloads,
        "_download_obs_file_with_retry",
        fake_download_with_retry,
    )
    monkeypatch.setattr(downloads, "convert_single_file", fake_convert)

    with ThreadPoolExecutor(max_workers=1) as executor:
        result = await downloads.download_list_convert(
            ["agent_data/paper.pdf"],
            server_dir=str(tmp_path / "temp"),
            obsfs_mount_root=str(tmp_path / "missing"),
            executor=executor,
        )

    assert result == ["converted paper"]
    assert captured["cleanup"] is True
    assert str(captured["file_path"]).startswith(str(tmp_path / "temp"))
    file_path = Path(captured["file_path"])
    assert file_path.parent.parent.parent == tmp_path / "temp" / "agent_data"
    assert file_path.parent.parent.name.isdigit()
