# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the bounded CSV dataset validation contract."""

from __future__ import annotations

from io import BytesIO

import pytest

from mcp_server_phytomni.api.upload_validation import (
    CsvUploadValidationError,
    validate_csv_upload,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\xff\xfeinvalid",
        b",value\n1,2\n",
        b"gene,gene\nA,B\n",
        b'gene,value\nA,"unterminated\n',
        b"gene,value\n",
    ],
)
def test_invalid_dataset_csv_is_rejected(payload: bytes) -> None:
    """Empty, malformed, duplicate, and header-only datasets fail closed."""
    with pytest.raises(CsvUploadValidationError):
        validate_csv_upload(BytesIO(payload), byte_size=len(payload))


def test_utf8_bom_and_quoted_comma_pass() -> None:
    """UTF-8 BOM and quoted commas retain the normalized header contract."""
    payload = b'\xef\xbb\xbfgene,note\nOs01g1,"a,b"\n'

    info = validate_csv_upload(BytesIO(payload), byte_size=len(payload))

    assert info.encoding == "utf-8-bom"
    assert info.headers == ("gene", "note")
    assert info.row_count == 1


class NoUnboundedRead(BytesIO):
    """BytesIO double that rejects reads without an explicit byte bound."""

    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.unbounded_read_calls = 0

    def read(self, size: int | None = -1) -> bytes:
        """Record and reject unbounded reads."""
        if size is None or size < 0:
            self.unbounded_read_calls += 1
            raise AssertionError("validator attempted an unbounded read")
        return super().read(size)


def test_validator_never_calls_unbounded_read() -> None:
    """CSV parsing consumes the stream through bounded buffered reads."""
    stream = NoUnboundedRead(b"gene,value\nOs01g1,1\nOs01g2,2\n")

    info = validate_csv_upload(stream, byte_size=stream.getbuffer().nbytes)

    assert info.row_count == 2
    assert stream.unbounded_read_calls == 0
    assert stream.tell() == 0


def test_validator_rewinds_after_an_invalid_csv() -> None:
    """The storage phase can still read an invalid stream for cleanup paths."""
    stream = BytesIO(b"gene,gene\nA,B\n")

    with pytest.raises(CsvUploadValidationError):
        validate_csv_upload(stream, byte_size=stream.getbuffer().nbytes)

    assert stream.tell() == 0
