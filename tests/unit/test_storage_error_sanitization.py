# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Storage layer SDK fallbacks must hide the full traceback from callers.

These tests pin the sanitized contract: the public ``OSError`` message
stays generic, the original exception is preserved via ``__cause__``
for ``logger.exception``, and the sentinel marker that simulates
secret content never appears on the wire.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.agents.analyst import storage as analyst_storage
from mcp_server_phytomni.agents.shared import (
    analysis_storage as shared_storage,
)
from mcp_server_phytomni.storage import downloads as storage_downloads

pytestmark = pytest.mark.unit

_SENTINEL = "INTERNAL_LEAKED_TOKEN_5C2F"
_MISSING_OBSFS_ROOT = "/nonexistent/obsfs-root-for-sanitization-tests"


class _ExplodingObsClient:
    """Fake OBS client whose every SDK method call raises the sentinel.

    Construction must succeed because every storage helper builds the
    client outside its sanitization try-block; the failure has to fire
    inside the method call (putContent/putFile/deleteObject/etc.) so
    the helper's ``except Exception`` runs and returns the sanitized
    ``OSError``. ``__getattr__`` proxies the arbitrary camelCase SDK surface
    so each method behaves identically without re-declaring it; this dynamic
    attribute protocol is why a state-free function or namespace cannot
    replace the class.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Accept and discard the production constructor signature.

        Args:
            *args: Positional config the real ObsClient would consume.
            **kwargs: Keyword config the real ObsClient would consume.
        """
        del args, kwargs

    def __getattr__(self, _name: str) -> Callable[..., Any]:
        """Return a callable that always raises the sentinel error.

        Args:
            _name: Attribute name requested by the storage helper; the
                fake ignores it so every method behaves identically.

        Returns:
            A function that raises ``RuntimeError`` with the marker text.
        """
        del _name
        return self.raise_for_test

    def raise_for_test(self, *args: Any, **kwargs: Any) -> Any:
        """Raise the sentinel error for every synthesized SDK method."""
        del args, kwargs
        raise RuntimeError(f"upstream blew up with token {_SENTINEL}")


def _assert_sanitized(exc: OSError) -> None:
    """Assert the captured OSError stays generic but keeps __cause__.

    Args:
        exc: OSError captured from the public helper under test.
    """
    text = str(exc)
    assert _SENTINEL not in text
    assert "Traceback" not in text
    assert "format_exc" not in text
    assert exc.__cause__ is not None
    assert _SENTINEL in str(exc.__cause__)


@pytest.fixture(autouse=True)
def _force_sdk_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every storage module fall through to the OBS SDK branch.

    Patches each module's outbound runtime with a client whose every SDK
    method raises a sentinel-bearing ``RuntimeError``. The obsfs branch in
    every public helper already raises ``FileNotFoundError`` when
    ``obsfs_mount_root`` points at a missing directory, so the SDK path is
    the one this fake answers from.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """

    async def run(_profile: Any, operation: Any) -> Any:
        """Execute one operation against a fresh exploding client."""
        return operation(_ExplodingObsClient())

    runtime = SimpleNamespace(obs=SimpleNamespace(run=run))
    monkeypatch.setattr(
        shared_storage,
        "current_outbound_runtime",
        lambda: runtime,
    )
    monkeypatch.setattr(
        analyst_storage,
        "current_outbound_runtime",
        lambda: runtime,
    )
    monkeypatch.setattr(
        storage_downloads,
        "current_outbound_runtime",
        lambda: runtime,
    )


async def test_create_output_dir_sanitizes_sdk_error() -> None:
    """Verify the shared create_output_dir helper sanitizes SDK errors."""
    with pytest.raises(OSError) as exc_info:
        await shared_storage.create_output_dir(
            user_id="placeholder-user",
            task="scratch",
            obsfs_mount_root=_MISSING_OBSFS_ROOT,
        )

    _assert_sanitized(exc_info.value)


async def test_upload_content_sanitizes_sdk_error() -> None:
    """Verify upload_analyst_agents_content sanitizes SDK errors."""
    with pytest.raises(OSError) as exc_info:
        await analyst_storage.upload_analyst_agents_content(
            content="payload-body",
            object_name="note.txt",
            obsfs_mount_root=_MISSING_OBSFS_ROOT,
        )

    _assert_sanitized(exc_info.value)


async def test_upload_data_sanitizes_sdk_error() -> None:
    """Verify upload_analyst_agents_data sanitizes SDK errors."""
    with pytest.raises(OSError) as exc_info:
        await analyst_storage.upload_analyst_agents_data(
            analyst_agents_datapath="/tmp/missing-file.txt",
            obsfs_mount_root=_MISSING_OBSFS_ROOT,
        )

    _assert_sanitized(exc_info.value)


async def test_delete_analyst_data_sanitizes_sdk_error() -> None:
    """Verify delete_analyst_agents_data sanitizes SDK errors."""
    with pytest.raises(OSError) as exc_info:
        await analyst_storage.delete_analyst_agents_data(
            analyst_agents_datapath="agent_data/note.txt",
            obsfs_mount_root=_MISSING_OBSFS_ROOT,
        )

    _assert_sanitized(exc_info.value)


async def test_download_obs_file_sanitizes_sdk_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """Verify download_obs_file sanitizes SDK errors after retries.

    The downloads helper resolves obsfs first; pointing the mount at a
    missing directory forces the SDK branch. A monkeypatched
    ``_download_obs_file_once`` raises the sentinel so retry exhaustion
    triggers without backoff sleeps.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap the
            single-shot download primitive.
        tmp_path: Temporary directory fixture for the local server dir.
    """

    async def _explode(*args: Any, **kwargs: Any) -> Any:
        """Raise a marker error so retry exhaustion triggers immediately."""
        del args, kwargs
        raise RuntimeError(f"download blew up with token {_SENTINEL}")

    monkeypatch.setattr(storage_downloads, "_download_obs_file_once", _explode)

    with pytest.raises(OSError) as exc_info:
        await storage_downloads.download_obs_file(
            obs_file="agent_data/file.txt",
            server_dir=str(tmp_path),
            obsfs_mount_root=_MISSING_OBSFS_ROOT,
            bucket_name="phytomni",
            max_retries=0,
        )

    _assert_sanitized(exc_info.value)
