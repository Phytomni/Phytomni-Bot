# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for obsfs-first upload download and conversion helpers.

Covers direct obsfs conversion, SDK temporary-file cleanup, OBS download
fallback, and download-list conversion cleanup flags.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.storage import downloads

pytestmark = pytest.mark.unit


def _write_text(path: Path | str, content: str) -> None:
    """Write fixture text through a synchronous test helper."""
    Path(path).write_text(content, encoding="utf-8")


def _read_text(path: Path | str) -> str:
    """Read fixture text through a synchronous test helper."""
    return Path(path).read_text(encoding="utf-8")


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
        _write_text(server_file, "downloaded")
        return server_file

    monkeypatch.setattr(
        downloads,
        "current_outbound_runtime",
        lambda: SimpleNamespace(obs=object()),
    )
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
    assert _read_text(result) == "downloaded"
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
        _write_text(server_file, "downloaded")
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

    monkeypatch.setattr(
        downloads,
        "current_outbound_runtime",
        lambda: SimpleNamespace(obs=object()),
    )
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


async def test_download_upload_context_formats_converted_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-empty uploads convert then format as bounded prompt context."""

    async def fake_convert(**kwargs: Any) -> list[str]:
        assert kwargs["obs_file_list"] == ["uploads/notes.txt"]
        return ["converted notes"]

    monkeypatch.setattr(downloads, "download_list_convert", fake_convert)
    formatted, length = await downloads.download_upload_context(
        ["uploads/notes.txt"],
        SimpleNamespace(
            TEMP_DIR="/tmp/phytomni-downloads",
            BUCKET_NAME="phytomni",
            PART_SIZE=8,
            TASK_NUM=1,
            MAX_RETRIES=0,
            MAX_CONCURRENCY=1,
            MAX_WORKERS=1,
            MAX_TOKENS=200,
        ),
    )
    assert "converted notes" in formatted
    assert length == len(formatted)


async def test_download_obs_file_reuses_explicit_transfer_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keyword transfer_context is returned unchanged to the resolver."""
    context = downloads.ObsTransferContext(
        server_dir=str(tmp_path),
        download=downloads.ObsDownloadOptions(bucket_name="phytomni"),
    )
    seen: list[downloads.ObsTransferContext] = []

    async def fake_resolve(
        obs_file: str, transfer: downloads.ObsTransferContext
    ) -> downloads.ResolvedObsFile:
        del obs_file
        seen.append(transfer)
        return downloads.ResolvedObsFile(str(tmp_path / "x"), False)

    monkeypatch.setattr(downloads, "_resolve_obs_file", fake_resolve)
    result = await downloads.download_obs_file(
        "notes.txt", str(tmp_path), transfer_context=context
    )
    assert result == str(tmp_path / "x")
    assert seen == [context]


async def test_obsfs_source_file_swallows_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An obsfs probe failure degrades to the SDK download path."""

    def boom(*_args: object, **_kwargs: object) -> Path:
        raise OSError("mount missing")

    async def fake_sdk(
        obs_file: str, context: downloads.ObsTransferContext
    ) -> str:
        del context
        return f"/tmp/{obs_file}"

    monkeypatch.setattr(downloads, "obsfs_path_for", boom)
    monkeypatch.setattr(downloads, "relay_mode_enabled", lambda: False)
    monkeypatch.setattr(downloads, "_download_obs_file_from_sdk", fake_sdk)
    assert (
        await downloads.download_obs_file(
            "notes.txt", "/tmp/server", obsfs_mount_root="/missing"
        )
        == "/tmp/notes.txt"
    )


async def test_sdk_download_requires_obs_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing outbound OBS runtime fails closed before the SDK call."""
    monkeypatch.setattr(downloads, "relay_mode_enabled", lambda: False)
    monkeypatch.setattr(
        downloads,
        "current_outbound_runtime",
        lambda: SimpleNamespace(obs=None),
    )
    with pytest.raises(OSError, match="OBS runtime is unavailable"):
        await downloads.download_obs_file(
            "notes.txt",
            str(tmp_path),
            obsfs_mount_root=str(tmp_path / "missing"),
        )


class _ScriptedObsRuntime:
    """Return scripted download responses for retry-path tests."""

    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)

    async def run(self, _profile: object, operation: Any) -> Any:
        operation(SimpleNamespace(downloadFile=lambda **_kwargs: None))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


async def test_sdk_download_retries_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient SDK error sleeps once and then returns the local file."""
    runtime = _ScriptedObsRuntime(
        [OSError("timeout"), SimpleNamespace(status=200)]
    )
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(downloads, "relay_mode_enabled", lambda: False)
    monkeypatch.setattr(
        downloads,
        "current_outbound_runtime",
        lambda: SimpleNamespace(obs=runtime),
    )
    monkeypatch.setattr(downloads.asyncio, "sleep", fake_sleep)
    result = await downloads.download_obs_file(
        "notes.txt",
        str(tmp_path),
        obsfs_mount_root=str(tmp_path / "missing"),
        max_retries=1,
    )
    assert Path(result).name == "notes.txt"
    assert "uploads" in result
    assert slept == [1.0]


async def test_sdk_download_maps_error_response_after_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-2xx SDK response becomes download failed after retries expire."""
    runtime = _ScriptedObsRuntime(
        [
            SimpleNamespace(
                status=500,
                requestId="req-1",
                errorCode="ObsError",
                errorMessage="unavailable",
            )
        ]
    )
    monkeypatch.setattr(downloads, "relay_mode_enabled", lambda: False)
    monkeypatch.setattr(
        downloads,
        "current_outbound_runtime",
        lambda: SimpleNamespace(obs=runtime),
    )
    with pytest.raises(OSError, match="download failed"):
        await downloads.download_obs_file(
            "owner/run/notes.txt",
            str(tmp_path),
            obsfs_mount_root=str(tmp_path / "missing"),
            max_retries=0,
        )


def test_convert_multi_files_uses_process_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The multi-file helper maps conversion across one process pool."""

    class ImmediatePool:
        def __init__(self, max_workers: int | None = None) -> None:
            self.max_workers = max_workers

        def __enter__(self) -> ImmediatePool:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def map(self, fn: Any, items: list[str]) -> list[str]:
            return [fn(item) for item in items]

    monkeypatch.setattr(downloads, "ProcessPoolExecutor", ImmediatePool)
    monkeypatch.setattr(
        downloads, "convert_single_file", lambda path: f"md:{path}"
    )
    assert downloads.convert_multi_files(
        ["a.txt", "b.txt"], max_workers=2
    ) == [
        "md:a.txt",
        "md:b.txt",
    ]


async def test_download_list_convert_rejects_zero_concurrency(
    tmp_path: Path,
) -> None:
    """A zero concurrency override fails before any executor starts."""
    with pytest.raises(ValueError, match="max_concurrency must be at least 1"):
        await downloads.download_list_convert(
            ["notes.txt"], str(tmp_path), max_concurrency=0
        )


async def test_download_list_convert_shuts_down_owned_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting an executor creates a pool and shuts it down afterward."""
    owned: list[ThreadPoolExecutor] = []

    def fake_pool(max_workers: int | None = None) -> ThreadPoolExecutor:
        executor = ThreadPoolExecutor(max_workers=max_workers or 1)
        owned.append(executor)
        return executor

    async def fake_resolve(
        obs_file: str, context: downloads.ObsTransferContext
    ) -> downloads.ResolvedObsFile:
        del context
        return downloads.ResolvedObsFile(f"/tmp/{obs_file}", False)

    monkeypatch.setattr(downloads, "ProcessPoolExecutor", fake_pool)
    monkeypatch.setattr(downloads, "convert_single_file", lambda *_a: "md")
    monkeypatch.setattr(downloads, "relay_mode_enabled", lambda: False)
    monkeypatch.setattr(downloads, "_resolve_obs_file", fake_resolve)
    result = await downloads.download_list_convert(
        ["notes.txt"], str(tmp_path)
    )
    assert result == ["md"]
    assert owned
    assert owned[0]._shutdown is True
