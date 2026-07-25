# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Streaming validation for user-provided CSV dataset uploads."""

from __future__ import annotations

import codecs
import csv
from dataclasses import dataclass
from io import TextIOWrapper
from typing import BinaryIO, Literal

__all__ = [
    "CsvUploadInfo",
    "CsvUploadValidationError",
    "seek_upload_size",
    "validate_csv_upload",
]


@dataclass(frozen=True, slots=True)
class CsvUploadInfo:
    """Validated facts about one supported CSV dataset."""

    encoding: Literal["utf-8", "utf-8-bom"]
    headers: tuple[str, ...]
    row_count: int


class CsvUploadValidationError(ValueError):
    """Raised when an uploaded dataset is outside the supported CSV subset."""


def _rewind(stream: BinaryIO) -> None:
    """Rewind a binary upload stream or raise a public validation error."""
    try:
        stream.seek(0)
    except (OSError, ValueError) as exc:
        raise CsvUploadValidationError(
            "dataset stream is not seekable"
        ) from exc


def seek_upload_size(stream: BinaryIO) -> int:
    """Return a seekable upload's byte size and leave it at position zero."""
    try:
        stream.seek(0, 2)
        byte_size = stream.tell()
    except (OSError, ValueError) as exc:
        raise CsvUploadValidationError(
            "dataset stream is not seekable"
        ) from exc
    _rewind(stream)
    if byte_size < 0:
        raise CsvUploadValidationError("dataset size is invalid")
    return byte_size


def validate_csv_upload(
    stream: BinaryIO,
    *,
    byte_size: int,
) -> CsvUploadInfo:
    """Validate one UTF-8 comma-delimited CSV without retaining its rows.

    The caller supplies the already bounded byte size so this validator can
    reject an empty upload without reading the stream to discover its size.
    The stream is rewound before and after parsing for the subsequent OBS
    storage read.
    """
    if byte_size <= 0:
        raise CsvUploadValidationError("dataset is empty")
    _rewind(stream)
    prefix = stream.read(3)
    _rewind(stream)
    encoding: Literal["utf-8", "utf-8-bom"] = (
        "utf-8-bom" if prefix == codecs.BOM_UTF8 else "utf-8"
    )
    text = TextIOWrapper(
        stream,
        encoding="utf-8-sig",
        newline="",
    )
    try:
        reader = csv.reader(text, dialect="excel", strict=True)
        try:
            raw_headers = next(reader)
        except StopIteration as exc:
            raise CsvUploadValidationError("dataset has no header") from exc
        headers = tuple(value.strip() for value in raw_headers)
        if not headers or any(not value for value in headers):
            raise CsvUploadValidationError("dataset has blank headers")
        if len(set(headers)) != len(headers):
            raise CsvUploadValidationError("dataset has duplicate headers")
        row_count = sum(1 for _row in reader)
        if row_count == 0:
            raise CsvUploadValidationError("dataset has no data rows")
    except (UnicodeDecodeError, csv.Error) as exc:
        raise CsvUploadValidationError("invalid utf-8 csv") from exc
    finally:
        text.detach()
        _rewind(stream)
    return CsvUploadInfo(encoding, headers, row_count)
