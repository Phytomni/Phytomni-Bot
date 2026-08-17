# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for bounded multipart storage ports."""

# pylint: disable=protected-access, too-few-public-methods

from __future__ import annotations

import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import SimpleNamespace

import pytest

from mcp_server_phytomni.runtime.async_utils import wait_for_thread_future
from mcp_server_phytomni.runtime.outbound import (
    ObsClientRuntime,
    OutboundPoolName,
)
from mcp_server_phytomni.runtime.outbound.registry import OutboundPoolRegistry
from mcp_server_phytomni.storage.multipart import (
    BoundedMultipartStorage,
    FakeMultipartStorage,
    MultipartSession,
    MultipartStorageError,
    PartInput,
    StoredPart,
    _looks_unknown,
    _read_part,
    _require_ok,
    _rewind,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_bounded_begin_normalizes_raw_transport_failure() -> None:
    """A raw SDK timeout becomes the stable storage-unavailable error."""

    def fail_begin(**_kwargs: object) -> None:
        raise TimeoutError

    client = SimpleNamespace(initiateMultipartUpload=fail_begin)
    pools = OutboundPoolRegistry(
        {name: 0 for name in OutboundPoolName},
        wait_warn_seconds=1.0,
    )
    runtime = ObsClientRuntime(pools, client)
    storage = BoundedMultipartStorage(
        runtime=runtime,
        loop=asyncio.get_running_loop(),
    )
    executor = ThreadPoolExecutor(max_workers=1)

    try:
        future = executor.submit(
            storage.begin,
            bucket="bucket",
            object_key="owner/file",
        )
        with pytest.raises(MultipartStorageError) as captured:
            await wait_for_thread_future(future)
    finally:
        executor.shutdown(wait=True)
        await runtime.aclose()

    assert captured.value.code == "upload_storage_unavailable"
    assert str(captured.value) == "upload_storage_unavailable"


def test_fake_storage_reads_one_part_in_bounded_chunks() -> None:
    """Part reads are chunked and do not depend on cumulative asset size."""
    storage = FakeMultipartStorage()
    session = storage.begin(bucket="bucket", object_key="owner/file")
    content = b"abc" * 700_000
    part = storage.put_part(
        session,
        PartInput(
            1,
            BytesIO(content),
            len(content),
            hashlib.sha256(content).hexdigest(),
        ),
    )
    assert part.byte_size == len(content)
    assert max(storage.read_sizes) <= 1024 * 1024
    assert len(storage.read_sizes) > 1


def test_digest_length_and_retry_conflict_are_provider_boundary_errors() -> (
    None
):
    """Malformed body length and digest mismatch fail before completion."""
    storage = FakeMultipartStorage()
    session = storage.begin(bucket="bucket", object_key="owner/file")
    with pytest.raises(
        MultipartStorageError, match="upload_checksum_mismatch"
    ):
        storage.put_part(
            session,
            PartInput(1, BytesIO(b"abc"), 3, "0" * 64),
        )
    with pytest.raises(MultipartStorageError, match="upload_state_conflict"):
        storage.put_part(
            session,
            PartInput(
                1,
                BytesIO(b"abc"),
                2,
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f200"
                "15ad",
            ),
        )


def test_complete_and_abort_are_idempotent() -> None:
    """Provider operations preserve terminal behavior without raw details."""
    storage = FakeMultipartStorage()
    session = storage.begin(bucket="bucket", object_key="owner/file")
    content = b"abc"
    part = storage.put_part(
        session,
        PartInput(1, BytesIO(content), 3, hashlib.sha256(content).hexdigest()),
    )
    assert storage.complete(session, [part]).byte_size == 3
    reconciled = storage.reconcile_complete(session, [part])
    assert reconciled is not None
    assert reconciled.byte_size == 3
    storage.abort(session)


def test_unknown_completion_reconciles_to_success() -> None:
    """A committed provider result is recovered before surfacing unknown."""
    storage = FakeMultipartStorage()
    storage.fail_complete_unknown = True
    session = storage.begin(bucket="bucket", object_key="owner/file")
    content = b"abc"
    part = storage.put_part(
        session,
        PartInput(1, BytesIO(content), 3, hashlib.sha256(content).hexdigest()),
    )
    with pytest.raises(MultipartStorageError, match="obs_outcome_unknown"):
        storage.complete(session, [part])
    reconciled = storage.reconcile_complete(session, [part])
    assert reconciled is not None
    assert reconciled.byte_size == 3


def _bounded_storage() -> BoundedMultipartStorage:
    """Build an adapter whose SDK seam will be replaced per test."""
    return BoundedMultipartStorage(
        runtime=SimpleNamespace(), loop=SimpleNamespace()
    )


def test_begin_rejects_empty_upload_id() -> None:
    """A success-status response without an upload id is unavailable."""
    storage = _bounded_storage()
    storage._run = lambda _operation: SimpleNamespace(
        status=200, body=SimpleNamespace(uploadId="")
    )
    with pytest.raises(
        MultipartStorageError, match="upload_storage_unavailable"
    ):
        storage.begin(bucket="bucket", object_key="owner/file")


def test_put_part_rejects_empty_etag() -> None:
    """A part response without an ETag is mapped to unavailable."""
    storage = _bounded_storage()
    storage._run = lambda _operation: SimpleNamespace(
        status=200, body=SimpleNamespace(etag="")
    )
    with pytest.raises(
        MultipartStorageError, match="upload_storage_unavailable"
    ):
        storage.put_part(
            MultipartSession("bucket", "owner/file", "upload-1"),
            PartInput(1, BytesIO(b"abc"), 3, "0" * 64),
        )


def test_complete_reconciles_unknown_transport_success() -> None:
    """A timeout after complete recovers a matching published object."""
    storage = _bounded_storage()

    def fail(_operation: object) -> None:
        raise TimeoutError

    storage._run = fail
    storage.reconcile_complete = lambda _session, _parts: SimpleNamespace(
        bucket="bucket", object_key="owner/file", byte_size=3
    )
    completed = storage.complete(
        MultipartSession("bucket", "owner/file", "upload-1"),
        [StoredPart(1, "etag-1", 3, "0" * 64)],
    )
    assert completed.byte_size == 3


def test_complete_unknown_outcome_when_reconcile_misses() -> None:
    """An unknown complete stays unknown when metadata cannot confirm it."""
    storage = _bounded_storage()

    def fail(_operation: object) -> None:
        raise ConnectionError

    storage._run = fail
    storage.reconcile_complete = lambda _session, _parts: None
    with pytest.raises(MultipartStorageError, match="obs_outcome_unknown"):
        storage.complete(
            MultipartSession("bucket", "owner/file", "upload-1"), []
        )


def test_complete_maps_known_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A classified transport failure does not attempt reconciliation."""
    storage = _bounded_storage()

    def fail(_operation: object) -> None:
        raise OSError("refused")

    storage._run = fail
    monkeypatch.setattr(
        "mcp_server_phytomni.storage.multipart._looks_unknown",
        lambda _exc: False,
    )
    with pytest.raises(
        MultipartStorageError, match="upload_storage_unavailable"
    ):
        storage.complete(
            MultipartSession("bucket", "owner/file", "upload-1"), []
        )


def test_abort_maps_transport_failure() -> None:
    """An abort transport error becomes the stable unavailable code."""
    storage = _bounded_storage()

    def fail(_operation: object) -> None:
        raise OSError("down")

    storage._run = fail
    with pytest.raises(
        MultipartStorageError, match="upload_storage_unavailable"
    ):
        storage.abort(MultipartSession("bucket", "owner/file", "upload-1"))


def test_reconcile_complete_returns_none_for_unusable_metadata() -> None:
    """Missing, failed, or mismatched metadata cannot confirm completion."""
    storage = _bounded_storage()
    session = MultipartSession("bucket", "owner/file", "upload-1")
    parts = [StoredPart(1, "etag-1", 3, "0" * 64)]

    def fail(_operation: object) -> None:
        raise TimeoutError

    storage._run = fail
    assert storage.reconcile_complete(session, parts) is None
    storage._run = lambda _operation: SimpleNamespace(status=404)
    assert storage.reconcile_complete(session, parts) is None
    storage._run = lambda _operation: SimpleNamespace(
        status=200, body=SimpleNamespace(contentLength="3")
    )
    assert storage.reconcile_complete(session, parts) is None
    storage._run = lambda _operation: SimpleNamespace(
        status=200, body=SimpleNamespace(contentLength=9)
    )
    assert storage.reconcile_complete(session, parts) is None


def test_download_to_path_verifies_and_rejects(tmp_path) -> None:
    """Downloads verify size and map missing or mismatched files."""
    storage = _bounded_storage()
    destination = tmp_path / "object.bin"

    def write_and_ok(_operation: object) -> SimpleNamespace:
        destination.write_bytes(b"abc")
        return SimpleNamespace(status=200)

    storage._run = write_and_ok
    assert (
        storage.download_to_path(
            bucket="bucket",
            object_key="owner/file",
            destination=destination,
            expected_size=3,
        )
        == 3
    )
    storage._run = lambda _operation: SimpleNamespace(status=200)
    with pytest.raises(
        MultipartStorageError, match="upload_storage_unavailable"
    ):
        storage.download_to_path(
            bucket="bucket",
            object_key="owner/file",
            destination=tmp_path / "missing.bin",
            expected_size=3,
        )
    destination.write_bytes(b"ab")
    with pytest.raises(MultipartStorageError, match="upload_state_conflict"):
        storage.download_to_path(
            bucket="bucket",
            object_key="owner/file",
            destination=destination,
            expected_size=3,
        )


def test_require_ok_rewind_fake_and_read_part_errors(tmp_path) -> None:
    """Helpers and the fake provider cover remaining error branches."""
    with pytest.raises(
        MultipartStorageError, match="upload_storage_unavailable"
    ):
        _require_ok(SimpleNamespace(status=500))

    class _Unseekable:
        def seek(self, *_args: object, **_kwargs: object) -> int:
            """Reject rewind on a closed or unseekable stream."""
            raise ValueError("closed")

    with pytest.raises(
        MultipartStorageError, match="upload_storage_unavailable"
    ):
        _rewind(_Unseekable())
    assert _looks_unknown(TimeoutError())
    assert not _looks_unknown(RuntimeError("no"))
    storage = FakeMultipartStorage()
    session = storage.begin(bucket="bucket", object_key="owner/file")
    content = b"abc"
    part = storage.put_part(
        session,
        PartInput(1, BytesIO(content), 3, hashlib.sha256(content).hexdigest()),
    )
    with pytest.raises(MultipartStorageError, match="upload_state_conflict"):
        storage.complete(session, [])
    storage.abort(session)
    with pytest.raises(MultipartStorageError, match="upload_state_conflict"):
        storage.complete(session, [])
    assert storage.reconcile_complete(session, []) is None
    with pytest.raises(MultipartStorageError, match="upload_asset_not_found"):
        storage._state(MultipartSession("bucket", "owner/file", "missing"))
    storage = FakeMultipartStorage()
    with pytest.raises(MultipartStorageError, match="upload_asset_not_found"):
        storage.download_to_path(
            bucket="bucket",
            object_key="owner/file",
            destination=tmp_path / "out.bin",
            expected_size=3,
        )
    session = storage.begin(bucket="bucket", object_key="owner/file")
    part = storage.put_part(
        session,
        PartInput(1, BytesIO(content), 3, hashlib.sha256(content).hexdigest()),
    )
    storage.complete(session, [part])
    with pytest.raises(
        MultipartStorageError, match="upload_storage_unavailable"
    ):
        storage.download_to_path(
            bucket="bucket",
            object_key="owner/file",
            destination=tmp_path / "out.bin",
            expected_size=1,
        )
    with pytest.raises(MultipartStorageError, match="upload_state_conflict"):
        storage.download_to_path(
            bucket="bucket",
            object_key="owner/file",
            destination=tmp_path / "out.bin",
            expected_size=9,
        )
    with pytest.raises(
        MultipartStorageError, match="upload_storage_unavailable"
    ):
        storage.download_to_path(
            bucket="bucket",
            object_key="owner/file",
            destination=tmp_path,
            expected_size=3,
        )
    digest = hashlib.sha256(b"abc").hexdigest()
    with pytest.raises(MultipartStorageError, match="invalid_upload_metadata"):
        _read_part(BytesIO(b""), content_length=-1, expected_sha256=digest)
    with pytest.raises(MultipartStorageError, match="upload_state_conflict"):
        _read_part(BytesIO(b""), content_length=3, expected_sha256=digest)

    class _Greedy:
        def read(self, _size: int = -1) -> bytes:
            """Return more bytes than the declared content length."""
            return b"abcd"

    with pytest.raises(MultipartStorageError, match="upload_state_conflict"):
        _read_part(_Greedy(), content_length=3, expected_sha256=digest)
    actual, payload = _read_part(
        BytesIO(b"abc"), content_length=3, expected_sha256=digest
    )
    assert actual == digest
    assert payload == b"abc"
