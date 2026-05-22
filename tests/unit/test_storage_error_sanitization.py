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

from typing import Any, Callable, cast

import pytest
from obs import ObsClient

from mcp_server_phytomni.agents.analyst import storage as analyst_storage
from mcp_server_phytomni.agents.shared import (
    analysis_storage as shared_storage,
)
from mcp_server_phytomni.storage import downloads as storage_downloads

pytestmark = pytest.mark.unit

_SENTINEL = "INTERNAL_LEAKED_TOKEN_5C2F"


class _ExplodingObsClient:
    """Fake OBS client whose every operation raises a sentinel error.

    The real ObsClient exposes camelCase methods (putContent, putFile,
    deleteObject, ...); rather than redeclaring each one and tripping
    the camelCase naming rule, every attribute access funnels through
    __getattr__ to the same exploding callable.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Accept the production constructor signature and ignore it.

        Args:
            *args: Positional config the real ObsClient would consume.
            **kwargs: Keyword config the real ObsClient would consume.
        """
        del args, kwargs

    def __getattr__(self, _name: str) -> Callable[..., Any]:
        """Return a callable that always raises the sentinel error.

        Args:
            _name: Attribute name requested by the storage helper; the
                fake intentionally ignores it so every method behaves
                identically.

        Returns:
            A function that raises ``RuntimeError`` with the marker text.
        """

        del _name

        def _explode(*args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            raise RuntimeError(f"upstream blew up with token {_SENTINEL}")

        return _explode


def _access() -> Any:
    """Return a minimal ObsAccessOptions stub for SDK helper calls.

    Returns:
        An ObsAccessOptions populated with deterministic placeholder values
        that never touch the network because the patched client errors out.
    """
    return shared_storage.ObsAccessOptions(
        access_key_id="placeholder-access",
        secret_access_key="placeholder-secret",
        obs_server="https://example.invalid",
        bucket_name="phytomni",
    )


def _assert_sanitized(exc: OSError) -> None:
    """Assert the captured OSError stays generic but keeps __cause__.

    Args:
        exc: OSError captured from the helper under test.
    """
    text = str(exc)
    assert _SENTINEL not in text
    assert "Traceback" not in text
    assert "format_exc" not in text
    assert exc.__cause__ is not None
    assert _SENTINEL in str(exc.__cause__)


@pytest.mark.parametrize(
    ("module", "call"),
    [
        (
            shared_storage,
            lambda: shared_storage._create_output_dir_sdk(
                "agent_data/scratch/run/", _access()
            ),
        ),
        (
            analyst_storage,
            lambda: analyst_storage._upload_content_sdk(
                "payload-body", "agent_data/scratch/note.txt", _access()
            ),
        ),
        (
            analyst_storage,
            lambda: analyst_storage._upload_file_sdk(
                "/tmp/missing.txt", "agent_data/scratch/note.txt", _access()
            ),
        ),
        (
            analyst_storage,
            lambda: analyst_storage._delete_analyst_data_sdk(
                "agent_data/scratch/note.txt", _access()
            ),
        ),
    ],
    ids=[
        "shared_create_output_dir",
        "analyst_upload_content",
        "analyst_upload_file",
        "analyst_delete",
    ],
)
def test_sdk_helpers_sanitize_errors(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    call: Callable[[], Any],
) -> None:
    """Verify each SDK helper sanitizes its OSError surface.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap ObsClient.
        module: Storage module whose ObsClient symbol gets replaced.
        call: Zero-arg lambda that invokes the helper under test.
    """
    monkeypatch.setattr(module, "ObsClient", _ExplodingObsClient)

    with pytest.raises(OSError) as exc_info:
        call()

    _assert_sanitized(exc_info.value)


async def test_download_obs_file_with_retry_sanitizes_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the downloads helper sanitizes after retry exhaustion.

    The wrapper retries every transport error; bounding max_retries at
    zero makes the very first call raise, exercising the sanitized
    except branch without waiting on backoff sleeps.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap the
            single-shot download primitive.
    """

    async def _explode(*args: Any, **kwargs: Any) -> Any:
        """Raise a marker error so retry exhaustion triggers immediately."""
        del args, kwargs
        raise RuntimeError(f"download blew up with token {_SENTINEL}")

    monkeypatch.setattr(storage_downloads, "_download_obs_file_once", _explode)

    context = storage_downloads.ObsTransferContext(
        server_dir="/tmp",
        credentials=storage_downloads.ObsCredentials(
            access_key_id="placeholder-access",
            secret_access_key="placeholder-secret",
        ),
        download=storage_downloads.ObsDownloadOptions(
            bucket_name="phytomni",
            max_retries=0,
        ),
    )

    with pytest.raises(OSError) as exc_info:
        await storage_downloads._download_obs_file_with_retry(
            obs_client=cast(ObsClient, _ExplodingObsClient()),
            object_key="agent_data/scratch/file.txt",
            server_file="/tmp/file.txt",
            context=context,
        )

    _assert_sanitized(exc_info.value)
