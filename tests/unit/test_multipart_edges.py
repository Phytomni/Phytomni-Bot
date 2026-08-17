# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Edge coverage for bounded and fake multipart storage failures."""

from __future__ import annotations

import hashlib
from io import BytesIO
from types import SimpleNamespace
from typing import Any, cast

import pytest

from mcp_server_phytomni.storage.multipart import (
    BoundedMultipartStorage,
    CompletedObject,
    FakeMultipartStorage,
    MultipartSession,
    MultipartStorageError,
    PartInput,
    StoredPart,
    _looks_unknown,
)

pytestmark = pytest.mark.unit


def _storage() -> BoundedMultipartStorage:
    """Return an adapter whose SDK seam is replaced by each test."""
    return BoundedMultipartStorage(
        runtime=cast(Any, SimpleNamespace()),
        loop=cast(Any, SimpleNamespace()),
    )


def _session() -> MultipartSession:
    """Return opaque session coordinates for adapter tests."""
    return MultipartSession("bucket", "owner/file.bin", "upload-1")


def _part() -> StoredPart:
    """Return one completed part used by complete and reconcile."""
    return StoredPart(1, "etag-1", 3, "abc")


def _patch_obs_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid constructing a real OBS complete request in unit tests."""
    monkeypatch.setattr(
        "mcp_server_phytomni.storage.multipart.import_module",
        lambda _name: SimpleNamespace(
            CompleteMultipartUploadRequest=lambda **_kwargs: object(),
            CompletePart=lambda **_kwargs: object(),
        ),
    )


def _stub_run(
    monkeypatch: pytest.MonkeyPatch,
    storage: BoundedMultipartStorage,
    result: object,
) -> None:
    """Replace the SDK runner with a fixed result or exception."""

    def _run(_operation: Any) -> Any:
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(storage, "_run", _run)


def test_begin_rejects_blank_upload_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A success status without an upload id is still unavailable."""
    storage = _storage()
    _stub_run(
        monkeypatch,
        storage,
        SimpleNamespace(status=200, body=SimpleNamespace(uploadId="")),
    )
    with pytest.raises(
        MultipartStorageError, match="upload_storage_unavailable"
    ):
        storage.begin(bucket="bucket", object_key="owner/file.bin")


def test_put_part_rejects_blank_etag(monkeypatch: pytest.MonkeyPatch) -> None:
    """A success status without an ETag is still unavailable."""
    storage = _storage()
    _stub_run(
        monkeypatch,
        storage,
        SimpleNamespace(status=200, body=SimpleNamespace(etag="")),
    )
    with pytest.raises(
        MultipartStorageError, match="upload_storage_unavailable"
    ):
        storage.put_part(
            _session(),
            PartInput(1, BytesIO(b"abc"), 3, "unused"),
        )


def test_complete_timeout_reuses_reconciled_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown complete recovers when metadata confirms the object."""
    _patch_obs_model(monkeypatch)
    storage = _storage()
    _stub_run(monkeypatch, storage, TimeoutError())
    expected = CompletedObject("bucket", "owner/file.bin", 3)
    monkeypatch.setattr(
        storage, "reconcile_complete", lambda *_args, **_kwargs: expected
    )
    assert storage.complete(_session(), (_part(),)) == expected


def test_complete_timeout_stays_unknown_when_unreconciled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown complete stays unknown when metadata is missing."""
    _patch_obs_model(monkeypatch)
    storage = _storage()
    _stub_run(monkeypatch, storage, TimeoutError())
    monkeypatch.setattr(
        storage, "reconcile_complete", lambda *_args, **_kwargs: None
    )
    with pytest.raises(MultipartStorageError, match="obs_outcome_unknown"):
        storage.complete(_session(), (_part(),))


def test_abort_maps_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Abort transport errors become the stable unavailable code."""
    storage = _storage()
    _stub_run(monkeypatch, storage, OSError("abort"))
    with pytest.raises(
        MultipartStorageError, match="upload_storage_unavailable"
    ):
        storage.abort(_session())


@pytest.mark.parametrize(
    "response",
    [
        "raise",
        SimpleNamespace(status=404),
        SimpleNamespace(status=200, body=SimpleNamespace(contentLength="3")),
        SimpleNamespace(status=200, body=SimpleNamespace(contentLength=9)),
    ],
)
def test_reconcile_complete_returns_none_on_unconfirmed_object(
    response: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Metadata that cannot confirm the expected size is not reused."""
    storage = _storage()
    if response == "raise":
        _stub_run(monkeypatch, storage, ConnectionError())
    else:
        _stub_run(monkeypatch, storage, response)
    assert storage.reconcile_complete(_session(), (_part(),)) is None


def test_download_to_path_verifies_expected_size(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful download returns the on-disk size when it matches."""
    storage = _storage()
    destination = tmp_path / "object.bin"
    destination.write_bytes(b"abc")
    _stub_run(monkeypatch, storage, SimpleNamespace(status=200))
    assert (
        storage.download_to_path(
            bucket="bucket",
            object_key="owner/file.bin",
            destination=destination,
            expected_size=3,
        )
        == 3
    )


def test_download_to_path_maps_missing_file(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A download that never materializes the file is unavailable."""
    storage = _storage()
    _stub_run(monkeypatch, storage, SimpleNamespace(status=200))
    with pytest.raises(
        MultipartStorageError, match="upload_storage_unavailable"
    ):
        storage.download_to_path(
            bucket="bucket",
            object_key="owner/file.bin",
            destination=tmp_path / "missing.bin",
            expected_size=3,
        )


def test_download_to_path_rejects_size_mismatch(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A downloaded object whose size disagrees is a state conflict."""
    storage = _storage()
    destination = tmp_path / "object.bin"
    destination.write_bytes(b"ab")
    _stub_run(monkeypatch, storage, SimpleNamespace(status=200))
    with pytest.raises(MultipartStorageError, match="upload_state_conflict"):
        storage.download_to_path(
            bucket="bucket",
            object_key="owner/file.bin",
            destination=destination,
            expected_size=3,
        )


def test_download_to_path_maps_error_status(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-success download status is storage-unavailable."""
    storage = _storage()
    _stub_run(monkeypatch, storage, SimpleNamespace(status=500))
    with pytest.raises(
        MultipartStorageError, match="upload_storage_unavailable"
    ):
        storage.download_to_path(
            bucket="bucket",
            object_key="owner/file.bin",
            destination=tmp_path / "object.bin",
            expected_size=3,
        )


def test_fake_complete_rejects_aborted_or_incomplete_parts() -> None:
    """The fake provider preserves abort and part-list conflicts."""
    storage = FakeMultipartStorage()
    session = storage.begin(bucket="bucket", object_key="owner/file.bin")
    storage.abort(session)
    with pytest.raises(MultipartStorageError, match="upload_state_conflict"):
        storage.complete(session, [])
    live = storage.begin(bucket="bucket", object_key="owner/other.bin")
    content = b"abc"
    part = storage.put_part(
        live,
        PartInput(
            1,
            BytesIO(content),
            3,
            hashlib.sha256(content).hexdigest(),
        ),
    )
    with pytest.raises(MultipartStorageError, match="upload_state_conflict"):
        storage.complete(live, [])
    assert storage.complete(live, [part]).byte_size == 3


def test_fake_reconcile_and_download_require_completion(tmp_path) -> None:
    """Unfinished fake sessions cannot be reconciled or downloaded."""
    storage = FakeMultipartStorage()
    session = storage.begin(bucket="bucket", object_key="owner/file.bin")
    assert storage.reconcile_complete(session, []) is None
    with pytest.raises(MultipartStorageError, match="upload_asset_not_found"):
        storage.download_to_path(
            bucket="bucket",
            object_key="owner/file.bin",
            destination=tmp_path / "out.bin",
            expected_size=3,
        )
    with pytest.raises(MultipartStorageError, match="upload_asset_not_found"):
        storage.complete(
            MultipartSession("bucket", "missing", "no-such"),
            [],
        )


def test_fake_download_enforces_expected_size(tmp_path) -> None:
    """Fake downloads reject both overflow and short writes."""
    storage = FakeMultipartStorage()
    session = storage.begin(bucket="bucket", object_key="owner/file.bin")
    content = b"abc"
    part = storage.put_part(
        session,
        PartInput(
            1,
            BytesIO(content),
            3,
            hashlib.sha256(content).hexdigest(),
        ),
    )
    storage.complete(session, [part])
    with pytest.raises(
        MultipartStorageError, match="upload_storage_unavailable"
    ):
        storage.download_to_path(
            bucket="bucket",
            object_key="owner/file.bin",
            destination=tmp_path / "over.bin",
            expected_size=1,
        )
    assert (
        storage.download_to_path(
            bucket="bucket",
            object_key="owner/file.bin",
            destination=tmp_path / "ok.bin",
            expected_size=3,
        )
        == 3
    )
    with pytest.raises(MultipartStorageError, match="upload_state_conflict"):
        storage.download_to_path(
            bucket="bucket",
            object_key="owner/file.bin",
            destination=tmp_path / "short.bin",
            expected_size=10,
        )


def test_fake_download_maps_destination_oserror(tmp_path) -> None:
    """A directory destination becomes storage-unavailable."""
    storage = FakeMultipartStorage()
    session = storage.begin(bucket="bucket", object_key="owner/file.bin")
    content = b"abc"
    part = storage.put_part(
        session,
        PartInput(
            1,
            BytesIO(content),
            3,
            hashlib.sha256(content).hexdigest(),
        ),
    )
    storage.complete(session, [part])
    with pytest.raises(
        MultipartStorageError, match="upload_storage_unavailable"
    ):
        storage.download_to_path(
            bucket="bucket",
            object_key="owner/file.bin",
            destination=tmp_path,
            expected_size=3,
        )


def test_read_part_rejects_negative_empty_and_overread() -> None:
    """Part reads fail closed on length and digest contract breaks."""
    storage = FakeMultipartStorage()
    session = storage.begin(bucket="bucket", object_key="owner/file.bin")
    with pytest.raises(MultipartStorageError, match="invalid_upload_metadata"):
        storage.put_part(
            session,
            PartInput(1, BytesIO(b""), -1, "0" * 64),
        )
    with pytest.raises(MultipartStorageError, match="upload_state_conflict"):
        storage.put_part(
            session,
            PartInput(1, BytesIO(b""), 3, "0" * 64),
        )

    class _Overread:
        """Return more bytes than the caller asked to read."""

        def read(self, _size: int = -1) -> bytes:
            return b"abcd"

    with pytest.raises(MultipartStorageError, match="upload_state_conflict"):
        storage.put_part(
            session,
            PartInput(1, cast(Any, _Overread()), 3, "0" * 64),
        )


def test_looks_unknown_classifies_transport_failures() -> None:
    """Only transport-shaped errors are treated as unknown outcomes."""
    assert _looks_unknown(TimeoutError())
    assert _looks_unknown(ConnectionError())
    assert _looks_unknown(OSError("io"))
    assert not _looks_unknown(ValueError("other"))
