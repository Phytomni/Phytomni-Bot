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
