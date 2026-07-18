# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``api/file_upload.read_with_byte_budget`` helper.

Covers the AF-001 chunked-accumulation guard: the helper returns the
joined body within ``max_bytes``, returns ``None`` at the first budget
breach, and stops reading immediately so a chunked upload without
``Content-Length`` cannot OOM the worker.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from mcp_server_phytomni.api.file_upload import read_with_byte_budget

pytestmark = pytest.mark.unit


class _ChunkedUpload:
    """Minimal ``UploadFile`` stand-in delivering a scripted chunk list.

    Records every ``read(n)`` call so tests can assert the helper
    stopped pulling more chunks once the budget broke. The mutable
    remaining queue models a one-shot upload stream and cannot be replaced
    by a value-only namespace without losing read offsets.
    """

    def __init__(self, chunks: list[bytes]) -> None:
        self.remaining: list[bytes] = list(chunks)
        self.read_calls: int = 0

    async def read(self, _size: int) -> bytes:
        """Return the next scripted chunk or b'' once drained."""
        self.read_calls += 1
        if not self.remaining:
            return b""
        return self.remaining.pop(0)


async def test_read_with_byte_budget_returns_bytes_within_budget() -> None:
    """A body that fits inside the budget is reassembled into bytes."""
    upload = _ChunkedUpload([b"hello", b" world"])

    result = await read_with_byte_budget(
        cast(Any, upload), max_bytes=100, chunk_size=8
    )

    assert result == b"hello world"
    assert isinstance(result, bytes)


async def test_byte_budget_none_when_first_chunk_overflows() -> None:
    """A single oversize chunk trips the budget on the first iteration."""
    upload = _ChunkedUpload([b"x" * 32])

    result = await read_with_byte_budget(
        cast(Any, upload), max_bytes=16, chunk_size=64
    )

    assert result is None
    assert upload.read_calls == 1


async def test_byte_budget_none_when_cumulative_overflows() -> None:
    """Several in-budget chunks plus one overflow chunk return None."""
    upload = _ChunkedUpload([b"aaaa", b"bbbb", b"cccc"])

    result = await read_with_byte_budget(
        cast(Any, upload), max_bytes=10, chunk_size=8
    )

    assert result is None
    # Helper stops reading the moment the budget breaks; the third
    # chunk was the trigger so no further reads occur.
    assert upload.read_calls == 3
    assert not upload.remaining


async def test_byte_budget_stops_draining_attacker_stream_early() -> None:
    """A long stream that exceeds budget early stops reading immediately."""
    upload = _ChunkedUpload([b"X" * 8] * 1000)

    result = await read_with_byte_budget(
        cast(Any, upload), max_bytes=16, chunk_size=8
    )

    assert result is None
    # Only 3 chunks needed: 8 + 8 = 16 (boundary), 8 more pushes to 24 > 16.
    assert upload.read_calls == 3
    # The remaining 997 chunks are NOT drained; an attacker streaming
    # gigabytes cannot OOM the worker because we abort early.
    assert len(upload.remaining) == 997


async def test_read_with_byte_budget_accepts_body_exactly_at_budget() -> None:
    """``max_bytes`` is inclusive: a body equal to the budget still passes."""
    upload = _ChunkedUpload([b"abcdefghij"])  # 10 bytes

    result = await read_with_byte_budget(
        cast(Any, upload), max_bytes=10, chunk_size=4
    )

    assert result == b"abcdefghij"


async def test_read_with_byte_budget_empty_body_returns_empty_bytes() -> None:
    """An exhausted source returns ``b''``, not None."""
    upload = _ChunkedUpload([])

    result = await read_with_byte_budget(
        cast(Any, upload), max_bytes=10, chunk_size=8
    )

    assert result == b""
