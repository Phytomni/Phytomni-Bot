# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``storage/uploads.py`` HTTP upload helper.

Covers the happy obsfs path (mount available), the SDK fallback path
(mount missing), filename sanitization (Unicode, path traversal, hidden
shell metacharacters), and the size/empty-body guard rails that the
``POST /v1/files`` route depends on for 400/413 mapping.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from mcp_server_phytomni.storage import obs_relay_ops as obs_relay_ops_module
from mcp_server_phytomni.storage import uploads as uploads_module
from mcp_server_phytomni.storage.uploads import (
    InvalidUploadError,
    UploadRecord,
    UploadRequest,
    UploadStorageOptions,
    UploadTooLargeError,
    safe_upload_filename,
    upload_user_file,
    validated_format,
    validated_media_type,
)

pytestmark = pytest.mark.unit


async def test_upload_user_file_uses_sdk_fallback_when_obsfs_missing(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_obs_client_factory: Callable[..., Any],
) -> None:
    """SDK fallback writes the object and returns the public OBS path."""
    fake = fake_obs_client_factory()
    monkeypatch.setattr(obs_relay_ops_module, "ObsClient", fake)

    record = await upload_user_file(
        UploadRequest(
            file_bytes=b"hello-bytes",
            original_filename="report.pdf",
            user_id="alice",
            request_id="req-abc",
            storage=UploadStorageOptions(
                max_bytes=1024,
                prefix="agent_data/uploads",
                bucket_name="phytomni",
                obsfs_mount_root=str(tmp_path / "no-mount"),
            ),
        )
    )

    assert isinstance(record, UploadRecord)
    assert record.filename == "report.pdf"
    assert record.bytes == len(b"hello-bytes")
    assert record.obs_path.startswith(
        "/obs/phytomni/agent_data/uploads/alice/req-abc/"
    )
    assert record.obs_path.endswith("/report.pdf")
    assert record.file_id in record.obs_path

    put_kwargs = fake.captured["put_content"]
    assert put_kwargs["bucketName"] == "phytomni"
    assert put_kwargs["content"] == b"hello-bytes"
    assert put_kwargs["objectKey"].startswith(
        "agent_data/uploads/alice/req-abc/"
    )
    assert put_kwargs["objectKey"].endswith("/report.pdf")


async def test_upload_user_file_writes_to_obsfs_when_mounted(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_obs_client_factory: Callable[..., Any],
) -> None:
    """When obsfs is mounted the upload writes to the mount path directly."""
    mount = tmp_path / "obs"
    (mount / "phytomni").mkdir(parents=True)
    fake = fake_obs_client_factory()
    monkeypatch.setattr(obs_relay_ops_module, "ObsClient", fake)

    record = await upload_user_file(
        UploadRequest(
            file_bytes=b"data",
            original_filename="notes.txt",
            user_id="bob",
            request_id="req-xyz",
            storage=UploadStorageOptions(
                max_bytes=1024,
                prefix="agent_data/uploads",
                bucket_name="phytomni",
                obsfs_mount_root=str(mount),
            ),
        )
    )

    assert "put_content" not in fake.captured
    written = (
        mount
        / "phytomni"
        / "agent_data"
        / "uploads"
        / "bob"
        / "req-xyz"
        / record.file_id
        / "notes.txt"
    )
    assert written.read_bytes() == b"data"
    assert record.obs_path == (
        "/obs/phytomni/agent_data/uploads/bob/req-xyz/"
        f"{record.file_id}/notes.txt"
    )


async def test_upload_user_file_rejects_empty_body(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_obs_client_factory: Callable[..., Any],
) -> None:
    """An empty payload raises InvalidUploadError (HTTP 400)."""
    monkeypatch.setattr(
        obs_relay_ops_module, "ObsClient", fake_obs_client_factory()
    )
    with pytest.raises(InvalidUploadError, match="empty"):
        await upload_user_file(
            UploadRequest(
                file_bytes=b"",
                original_filename="file.bin",
                user_id="u",
                request_id="r",
                storage=UploadStorageOptions(
                    max_bytes=1024,
                    prefix="agent_data/uploads",
                    obsfs_mount_root=str(tmp_path / "no-mount"),
                ),
            )
        )


async def test_upload_user_file_rejects_oversize(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_obs_client_factory: Callable[..., Any],
) -> None:
    """Exceeding max_bytes raises UploadTooLargeError (HTTP 413)."""
    monkeypatch.setattr(
        obs_relay_ops_module, "ObsClient", fake_obs_client_factory()
    )
    with pytest.raises(UploadTooLargeError, match="exceeds"):
        await upload_user_file(
            UploadRequest(
                file_bytes=b"x" * 11,
                original_filename="x.bin",
                user_id="u",
                request_id="r",
                storage=UploadStorageOptions(
                    max_bytes=10,
                    prefix="agent_data/uploads",
                    obsfs_mount_root=str(tmp_path / "no-mount"),
                ),
            )
        )


def test_safe_upload_filename_strips_traversal_components() -> None:
    """Path components like ``../../etc/passwd`` collapse to the basename."""
    assert safe_upload_filename("../../etc/passwd") == "passwd"
    assert safe_upload_filename(r"..\..\etc\passwd") == "passwd"
    assert safe_upload_filename("/etc/passwd") == "passwd"


def test_safe_upload_filename_sanitizes_unsafe_stem() -> None:
    """Unicode and shell metacharacters in the stem collapse to ``-``."""
    out = safe_upload_filename("my report (final) v2.pdf")
    assert out.endswith(".pdf")
    assert "/" not in out
    assert " " not in out
    assert "(" not in out
    assert ")" not in out


def test_safe_upload_filename_preserves_suffix_case() -> None:
    """The original suffix (including case) is preserved on the safe name."""
    assert safe_upload_filename("DATA.TSV") == "DATA.TSV"


def test_safe_upload_filename_rejects_empty_and_dot_names() -> None:
    """Empty, ``.``, and ``..`` filenames raise InvalidUploadError."""
    for bad in ("", "   ", ".", ".."):
        with pytest.raises(InvalidUploadError):
            safe_upload_filename(bad)


def test_upload_metadata_format_and_media_type_are_deterministic() -> None:
    """Metadata classification uses the sanitized filename and purpose."""
    assert validated_format("report.pdf", "agent_context") == "pdf"
    assert validated_media_type("report.pdf", "agent_context") == (
        "application/pdf"
    )
    assert validated_format("table.csv", "dataset") == "csv"
    assert validated_media_type("table.csv", "dataset") == "text/csv"
    assert validated_format("README", "agent_context") == "binary"


def test_dataset_format_requires_csv_suffix() -> None:
    """The dataset purpose cannot be attached to an unrelated file type."""
    with pytest.raises(InvalidUploadError, match="CSV"):
        validated_format("table.tsv", "dataset")


async def test_upload_user_file_anonymizes_missing_user_id(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_obs_client_factory: Callable[..., Any],
) -> None:
    """Empty user_id falls back to the shared anonymous bucket."""
    monkeypatch.setattr(
        obs_relay_ops_module, "ObsClient", fake_obs_client_factory()
    )
    record = await upload_user_file(
        UploadRequest(
            file_bytes=b"data",
            original_filename="x.bin",
            user_id="",
            request_id="r",
            storage=UploadStorageOptions(
                max_bytes=1024,
                prefix="agent_data/uploads",
                bucket_name="phytomni",
                obsfs_mount_root=str(tmp_path / "no-mount"),
            ),
        )
    )
    assert "/agent_data/uploads/anonymous/r/" in record.obs_path


async def test_upload_user_file_uses_relay_when_relay_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relay mode forwards bytes to the relay and skips the OBS SDK."""
    relay = Mock()
    relay.put_obs_object = AsyncMock(return_value={"obs_path": "/obs/x"})
    monkeypatch.setattr(uploads_module, "relay_mode_enabled", lambda: True)
    monkeypatch.setattr(uploads_module, "current_relay_client", lambda: relay)
    no_local = Mock()
    monkeypatch.setattr(uploads_module, "put_object_bytes", no_local)

    record = await upload_user_file(
        UploadRequest(
            file_bytes=b"payload",
            original_filename="x.pdf",
            user_id="alice",
            request_id="req-1",
            storage=UploadStorageOptions(
                max_bytes=1024,
                prefix="agent_data/uploads",
                bucket_name="phytomni",
            ),
        )
    )

    assert relay.put_obs_object.await_count == 1
    assert relay.put_obs_object.await_args.args[1] == b"payload"
    assert not no_local.called
    assert record.obs_path.startswith(
        "/obs/phytomni/agent_data/uploads/alice/req-1/"
    )
