# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the relay-mode download branch of ``storage/downloads.py``.

When relay mode is on, ``_resolve_obs_file`` takes a relay fast path:
``download_obs_file`` streams the object through the relay straight to a
run-scoped local temp file without instantiating an ``ObsClient`` or
probing the obsfs mount. Normal mode is unaffected.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from mcp_server_phytomni.storage import downloads as downloads_module
from mcp_server_phytomni.storage.downloads import download_obs_file

pytestmark = pytest.mark.unit


def _write_bytes(path: Path | str, content: bytes) -> None:
    """Write fixture bytes through a synchronous test helper."""
    Path(path).write_bytes(content)


def _read_bytes(path: Path | str) -> bytes:
    """Read fixture bytes through a synchronous test helper."""
    return Path(path).read_bytes()


async def test_download_obs_file_uses_relay_in_relay_mode(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Relay mode streams via the relay to a temp file, no ObsClient."""

    async def _stream_to_path(
        obs_path: str, destination: Path, *, message: str
    ) -> None:
        del obs_path, message
        _write_bytes(destination, b"PDF-BYTES")

    relay = Mock()
    relay.get_obs_object_to_path = AsyncMock(side_effect=_stream_to_path)
    monkeypatch.setattr(downloads_module, "relay_mode_enabled", lambda: True)
    monkeypatch.setattr(
        downloads_module, "current_relay_client", lambda: relay
    )
    no_sdk = AsyncMock(side_effect=AssertionError("SDK path was used"))
    monkeypatch.setattr(
        downloads_module, "_download_obs_file_from_sdk", no_sdk
    )

    local_path = await download_obs_file(
        "/obs/phytomni/agent_data/uploads/u/r/up/notes.pdf", str(tmp_path)
    )

    assert relay.get_obs_object_to_path.await_count == 1
    assert relay.get_obs_object_to_path.await_args.args[0].endswith(
        "notes.pdf"
    )
    assert not no_sdk.await_args_list
    assert _read_bytes(local_path) == b"PDF-BYTES"


async def test_download_obs_list_leaves_concurrency_to_outbound_obs_pool(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The batch helper dispatches every download without a local gate."""
    started: list[str] = []
    all_started = asyncio.Event()
    release = asyncio.Event()

    async def fake_download(
        obs_file: str,
        server_dir: str,
        **kwargs: Any,
    ) -> str:
        """Pause each dispatched download below the batch boundary."""
        del server_dir, kwargs
        started.append(obs_file)
        if len(started) == 3:
            all_started.set()
        await release.wait()
        return f"/local/{obs_file}"

    monkeypatch.setattr(downloads_module, "download_obs_file", fake_download)
    task = asyncio.create_task(
        downloads_module.download_obs_list(
            ["one.txt", "two.txt", "three.txt"],
            str(tmp_path),
            max_concurrency=0,
        )
    )

    try:
        await asyncio.wait_for(all_started.wait(), timeout=0.5)
        release.set()
        assert await task == [
            "/local/one.txt",
            "/local/two.txt",
            "/local/three.txt",
        ]
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
