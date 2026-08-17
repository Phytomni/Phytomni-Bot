# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Edge coverage for OBS download helpers."""

# pylint: disable=protected-access, too-few-public-methods

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from mcp_server_phytomni.runtime.outbound import ObsClientRuntime
from mcp_server_phytomni.storage import downloads as downloads_module
from mcp_server_phytomni.storage.downloads import (
    ObsDownloadOptions,
    ObsTransferContext,
    download_list_convert,
    download_upload_context,
)

pytestmark = pytest.mark.unit


def _context(
    server_dir: str, *, max_retries: int = 0, max_concurrency: int = 1
) -> ObsTransferContext:
    """Build a local transfer context for isolated helper tests."""
    return ObsTransferContext(
        server_dir=server_dir,
        download=ObsDownloadOptions(max_retries=max_retries),
        max_concurrency=max_concurrency,
    )


class _Executor:
    """Process-pool stand-in that records shutdown."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self.shutdown_wait: bool | None = None

    def __enter__(self) -> _Executor:
        return self

    def __exit__(self, *args: Any) -> None:
        del args

    def map(self, func: Any, items: Any) -> list[Any]:
        """Return placeholder conversion results."""
        del func
        return [f"md:{item}" for item in items]

    def shutdown(self, wait: bool = True) -> None:
        """Record the managed-executor shutdown path."""
        self.shutdown_wait = wait


async def test_download_upload_context_empty_and_formatted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty uploads skip conversion; non-empty files are formatted."""

    async def _convert(**kwargs: Any) -> list[str]:
        del kwargs
        return ["alpha"]

    monkeypatch.setattr(downloads_module, "download_list_convert", _convert)
    empty = await download_upload_context([], SimpleNamespace())
    assert empty == ("", 0)

    config = SimpleNamespace(
        TEMP_DIR="/tmp",
        BUCKET_NAME="bucket",
        PART_SIZE=1,
        TASK_NUM=1,
        MAX_RETRIES=0,
        MAX_CONCURRENCY=1,
        MAX_WORKERS=1,
        MAX_TOKENS=1000,
    )
    text, length = await download_upload_context(["obs://a.txt"], config)
    assert "alpha" in text
    assert length > 0


def test_transfer_context_returns_injected_instance() -> None:
    """Keyword transfer_context is reused instead of rebuilt."""
    injected = _context("/injected")
    resolved = downloads_module._obs_transfer_context(
        "/other", {"transfer_context": injected}
    )
    assert resolved is injected


def test_obsfs_source_file_swallows_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unreadable obsfs mounts fall back to a missing source."""

    def _raise(*args: Any, **kwargs: Any) -> Path:
        del args, kwargs
        raise OSError("mount")

    monkeypatch.setattr(downloads_module, "obsfs_path_for", _raise)
    found = downloads_module._obsfs_source_file(
        "obs://bucket/a.txt", _context("/tmp")
    )
    assert found is None


async def test_sdk_download_requires_obs_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SDK download fails closed when the outbound OBS pool is absent."""
    monkeypatch.setattr(
        downloads_module,
        "current_outbound_runtime",
        lambda: SimpleNamespace(obs=None),
    )
    with pytest.raises(OSError, match="OBS runtime is unavailable"):
        await downloads_module._download_obs_file_from_sdk(
            "uploads/notes.txt", _context(str(tmp_path))
        )


def test_temp_download_group_defaults_without_parent() -> None:
    """A single-segment OBS key uses the uploads group."""
    assert downloads_module._temp_download_group("notes.txt") == "uploads"
    assert downloads_module._temp_download_group("a/b/c.txt") == "b"


async def test_download_retry_succeeds_after_transient_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing first attempt sleeps, then a 2xx response returns the path."""
    attempts: list[int] = []

    async def _once(*_args: Any, **kwargs: Any) -> SimpleNamespace:
        del kwargs
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("transient")
        return SimpleNamespace(status=200)

    sleeps: list[float] = []

    async def _sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(downloads_module, "_download_obs_file_once", _once)
    monkeypatch.setattr(downloads_module.asyncio, "sleep", _sleep)
    target = str(tmp_path / "notes.txt")
    result = await downloads_module._download_obs_file_with_retry(
        cast(ObsClientRuntime, SimpleNamespace()),
        "notes.txt",
        target,
        _context(str(tmp_path), max_retries=1),
    )
    assert result == target
    assert sleeps == [1.5**0]


async def test_download_retry_raises_after_status_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-2xx responses become OSError after the retry budget."""

    async def _once(*args: Any, **kwargs: Any) -> SimpleNamespace:
        del args, kwargs
        return SimpleNamespace(
            status=500,
            requestId="rid",
            errorCode="ObsError",
            errorMessage="denied",
        )

    monkeypatch.setattr(downloads_module, "_download_obs_file_once", _once)
    with pytest.raises(OSError, match="download failed"):
        await downloads_module._download_obs_file_with_retry(
            cast(ObsClientRuntime, SimpleNamespace()),
            "notes.txt",
            str(tmp_path / "notes.txt"),
            _context(str(tmp_path), max_retries=0),
        )


async def test_download_retry_returns_when_budget_is_negative(
    tmp_path: Path,
) -> None:
    """A negative retry budget skips the loop and returns the target."""
    target = str(tmp_path / "notes.txt")
    result = await downloads_module._download_obs_file_with_retry(
        cast(ObsClientRuntime, SimpleNamespace()),
        "notes.txt",
        target,
        _context(str(tmp_path), max_retries=-1),
    )
    assert result == target


async def test_download_once_uses_owned_runtime(tmp_path: Path) -> None:
    """One SDK download is submitted through the outbound runtime."""
    seen: list[Any] = []

    class _Runtime:
        async def run(self, profile: Any, callback: Any) -> str:
            """Record the outbound profile and return the callback result."""
            seen.append(profile)
            return callback(SimpleNamespace(downloadFile=lambda **k: "ok"))

    context = _context(str(tmp_path))
    result = await downloads_module._download_obs_file_once(
        cast(ObsClientRuntime, _Runtime()),
        "object-key",
        str(tmp_path / "f.txt"),
        context,
    )
    assert result == "ok"
    assert seen


def test_obs_download_error_includes_response_fields() -> None:
    """OBS error text is assembled from the SDK response object."""
    error = downloads_module._obs_download_error(
        SimpleNamespace(
            requestId="rid-1",
            errorCode="AccessDenied",
            errorMessage="nope",
        )
    )
    assert "rid-1" in str(error)
    assert "AccessDenied" in str(error)


def test_convert_multi_files_uses_process_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Batch conversion maps every local path through the pool."""
    monkeypatch.setattr(downloads_module, "ProcessPoolExecutor", _Executor)
    converted = downloads_module.convert_multi_files(
        ["/tmp/a.pdf", "/tmp/b.pdf"], max_workers=1
    )
    assert converted == ["md:/tmp/a.pdf", "md:/tmp/b.pdf"]


async def test_download_list_convert_rejects_zero_concurrency() -> None:
    """A non-positive concurrency setting is rejected before work starts."""
    with pytest.raises(ValueError, match="max_concurrency"):
        await download_list_convert(["obs://a.txt"], "/tmp", max_concurrency=0)


async def test_download_list_convert_shuts_down_owned_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The owned executor is shut down when the caller omits one."""
    created: list[_Executor] = []

    class _Owned(_Executor):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            created.append(self)

    async def _convert(*args: Any, **kwargs: Any) -> str:
        del args, kwargs
        return "markdown"

    monkeypatch.setattr(downloads_module, "ProcessPoolExecutor", _Owned)
    monkeypatch.setattr(downloads_module, "_download_and_convert", _convert)
    texts = await download_list_convert(
        ["obs://a.txt"], "/tmp", max_concurrency=1, max_workers=1
    )
    assert texts == ["markdown"]
    assert created[0].shutdown_wait is True
