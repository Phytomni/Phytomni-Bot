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

from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.storage import uploads as uploads_module
from mcp_server_phytomni.storage.uploads import (
    InvalidUploadError,
    UploadRecord,
    UploadTooLargeError,
    safe_upload_filename,
    upload_user_file,
)

pytestmark = pytest.mark.unit


class _FakeObsClient:
    """Capture-only OBS SDK stand-in for the SDK-fallback write path."""

    captured: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _FakeObsClient.captured["init"] = kwargs

    def __getattr__(self, name: str) -> Any:
        """Map OBS SDK camelCase methods to snake-case fakes."""
        if name == "putContent":
            return self._put_content
        raise AttributeError(name)

    def _put_content(self, **kwargs: Any) -> Any:
        _FakeObsClient.captured["put_content"] = kwargs
        return SimpleNamespace(
            status=200, requestId="request-id", errorCode=None
        )


@pytest.fixture(autouse=True)
def _reset_fake_obs() -> None:
    """Clear the shared capture dict between tests."""
    _FakeObsClient.captured = {}


def test_upload_user_file_uses_sdk_fallback_when_obsfs_missing(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SDK fallback writes the object and returns the public OBS path."""
    monkeypatch.setattr(uploads_module, "ObsClient", _FakeObsClient)

    record = upload_user_file(
        file_bytes=b"hello-bytes",
        original_filename="report.pdf",
        user_id="alice",
        request_id="req-abc",
        max_bytes=1024,
        prefix="agent_data/uploads",
        bucket_name="phytomni",
        obsfs_mount_root=str(tmp_path / "no-mount"),
    )

    assert isinstance(record, UploadRecord)
    assert record.filename == "report.pdf"
    assert record.bytes == len(b"hello-bytes")
    assert record.obs_path.startswith(
        "/obs/phytomni/agent_data/uploads/alice/req-abc/"
    )
    assert record.obs_path.endswith("/report.pdf")
    assert record.file_id in record.obs_path

    put_kwargs = _FakeObsClient.captured["put_content"]
    assert put_kwargs["bucketName"] == "phytomni"
    assert put_kwargs["content"] == b"hello-bytes"
    assert put_kwargs["objectKey"].startswith(
        "agent_data/uploads/alice/req-abc/"
    )
    assert put_kwargs["objectKey"].endswith("/report.pdf")


def test_upload_user_file_writes_to_obsfs_when_mounted(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When obsfs is mounted the upload writes to the mount path directly."""
    mount = tmp_path / "obs"
    (mount / "phytomni").mkdir(parents=True)
    monkeypatch.setattr(uploads_module, "ObsClient", _FakeObsClient)

    record = upload_user_file(
        file_bytes=b"data",
        original_filename="notes.txt",
        user_id="bob",
        request_id="req-xyz",
        max_bytes=1024,
        prefix="agent_data/uploads",
        bucket_name="phytomni",
        obsfs_mount_root=str(mount),
    )

    assert "put_content" not in _FakeObsClient.captured
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


def test_upload_user_file_rejects_empty_body(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty payload raises InvalidUploadError (HTTP 400)."""
    monkeypatch.setattr(uploads_module, "ObsClient", _FakeObsClient)
    with pytest.raises(InvalidUploadError, match="empty"):
        upload_user_file(
            file_bytes=b"",
            original_filename="file.bin",
            user_id="u",
            request_id="r",
            max_bytes=1024,
            prefix="agent_data/uploads",
            obsfs_mount_root=str(tmp_path / "no-mount"),
        )


def test_upload_user_file_rejects_oversize(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exceeding max_bytes raises UploadTooLargeError (HTTP 413)."""
    monkeypatch.setattr(uploads_module, "ObsClient", _FakeObsClient)
    with pytest.raises(UploadTooLargeError, match="exceeds"):
        upload_user_file(
            file_bytes=b"x" * 11,
            original_filename="x.bin",
            user_id="u",
            request_id="r",
            max_bytes=10,
            prefix="agent_data/uploads",
            obsfs_mount_root=str(tmp_path / "no-mount"),
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


def test_upload_user_file_anonymizes_missing_user_id(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty user_id falls back to the shared anonymous bucket."""
    monkeypatch.setattr(uploads_module, "ObsClient", _FakeObsClient)
    record = upload_user_file(
        file_bytes=b"data",
        original_filename="x.bin",
        user_id="",
        request_id="r",
        max_bytes=1024,
        prefix="agent_data/uploads",
        bucket_name="phytomni",
        obsfs_mount_root=str(tmp_path / "no-mount"),
    )
    assert "/agent_data/uploads/anonymous/r/" in record.obs_path
