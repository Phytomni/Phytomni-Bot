# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for bounded multipart storage ports."""

from __future__ import annotations

import hashlib
from io import BytesIO

import pytest

from mcp_server_phytomni.storage.multipart import (
    FakeMultipartStorage,
    MultipartStorageError,
    PartInput,
)

pytestmark = pytest.mark.unit


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
