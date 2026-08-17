# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""CSV upload stream validation error branches."""

from __future__ import annotations

from io import BytesIO

import pytest

from mcp_server_phytomni.api.upload_validation import (
    CsvUploadValidationError,
    seek_upload_size,
    validate_csv_upload,
)

pytestmark = pytest.mark.unit


class _Unseekable(BytesIO):
    """Binary buffer that rejects seek like a closed upload."""

    def seek(self, *_args: object, **_kwargs: object) -> int:
        raise OSError("closed")


def test_seek_upload_size_rejects_unseekable_and_negative_sizes() -> None:
    """Size probing fails closed when the stream cannot rewind."""
    with pytest.raises(CsvUploadValidationError, match="not seekable"):
        seek_upload_size(_Unseekable(b"a,b\n1,2\n"))


def test_validate_csv_upload_rejects_header_only_rows() -> None:
    """A header without data rows is a public validation error."""
    stream = BytesIO(b"gene,tissue\n")
    with pytest.raises(CsvUploadValidationError, match="no data rows"):
        validate_csv_upload(stream, byte_size=len(stream.getvalue()))


def test_validate_csv_upload_rejects_empty_header() -> None:
    """An empty header row is rejected before row counting."""
    stream = BytesIO(b"\n1,2\n")
    with pytest.raises(CsvUploadValidationError, match="blank headers"):
        validate_csv_upload(stream, byte_size=len(stream.getvalue()))


class _RewindFails(BytesIO):
    """Allows a size probe but rejects the later rewind."""

    def seek(self, offset: int, whence: int = 0) -> int:
        if offset == 0 and whence == 0:
            raise OSError("closed")
        return super().seek(offset, whence)


class _NegativeSize(BytesIO):
    """Reports a negative byte size after a successful end seek."""

    def tell(self) -> int:
        return -1


def test_seek_upload_size_rejects_rewind_failure() -> None:
    """A stream that cannot return to zero is not a valid upload."""
    with pytest.raises(CsvUploadValidationError, match="not seekable"):
        seek_upload_size(_RewindFails(b"a,b\n1,2\n"))


def test_seek_upload_size_rejects_negative_byte_size() -> None:
    """A negative tell() after SEEK_END is an invalid dataset size."""
    with pytest.raises(CsvUploadValidationError, match="size is invalid"):
        seek_upload_size(_NegativeSize(b"a,b\n1,2\n"))


def test_seek_upload_size_returns_byte_length() -> None:
    """A seekable CSV reports its size and is left at position zero."""
    stream = BytesIO(b"a,b\n1,2\n")
    assert seek_upload_size(stream) == 8
    assert stream.tell() == 0


def test_validate_csv_upload_rejects_empty_stream() -> None:
    """An empty upload has no header row to read."""
    stream = BytesIO(b"")
    with pytest.raises(CsvUploadValidationError, match="dataset is empty"):
        validate_csv_upload(stream, byte_size=0)


def test_validate_csv_upload_rejects_headerless_payload() -> None:
    """A non-empty stream with no CSV rows has no header."""
    stream = BytesIO(b"")
    with pytest.raises(CsvUploadValidationError, match="no header"):
        validate_csv_upload(stream, byte_size=1)
