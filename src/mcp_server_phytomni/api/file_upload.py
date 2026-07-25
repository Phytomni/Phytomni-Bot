# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Handler for ``POST /v1/files`` multipart upload.

Public functions: handle_file_upload, read_with_byte_budget.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import Request, UploadFile
from fastapi.responses import JSONResponse

from ..config.defaults import ApiConfig
from ..runtime.request_context import current_request_id
from ..runtime.task_manager import resolve_tasks_db_path
from ..runtime.upload_registry import UploadMetadata, UploadRegistry
from ..storage.path_policy import IdFactory
from ..storage.uploads import (
    InvalidUploadError,
    UploadRecord,
    UploadRequest,
    UploadStorageOptions,
    UploadTooLargeError,
    upload_user_file,
    validated_format,
    validated_media_type,
)
from .app_support import _ErrorResponseOptions
from .schemas import FileUploadResponse, UploadPurpose
from .upload_validation import (
    CsvUploadValidationError,
    seek_upload_size,
    validate_csv_upload,
)

logger = logging.getLogger(__name__)

ErrorEnvelope = Callable[..., JSONResponse]

# 64 KiB chunks keep the per-call read modest and bound peak memory
# to ``max_bytes + DEFAULT_READ_CHUNK_SIZE - 1`` even when the
# Content-Length header is absent or falsified (chunked transfer
# encoding), closing the AF-001 OOM window in audit 2026-05-26.
DEFAULT_READ_CHUNK_SIZE = 64 * 1024


class UploadMetadataPersistenceError(RuntimeError):
    """Raised when an accepted OBS object cannot be registered locally."""


def _persist_upload_metadata(
    record: UploadRecord,
    *,
    user_id: str,
    purpose: UploadPurpose,
) -> int:
    """Persist trusted upload metadata and return its response timestamp."""
    created_at = datetime.now(UTC)
    metadata = UploadMetadata(
        file_id=record.file_id,
        user_id=user_id,
        obs_path=record.obs_path,
        filename=record.filename,
        purpose=purpose,
        byte_size=record.bytes,
        format=validated_format(record.filename, purpose),
        media_type=validated_media_type(record.filename, purpose),
        created_at=created_at.isoformat(),
    )
    try:
        UploadRegistry(resolve_tasks_db_path()).record(metadata)
    except (sqlite3.Error, OSError) as exc:
        logger.error(
            "upload metadata persistence failed (%s)",
            exc.__class__.__name__,
        )
        raise UploadMetadataPersistenceError from exc
    return int(created_at.timestamp())


def _dataset_error(
    error_response: ErrorEnvelope,
    message: str,
) -> JSONResponse:
    """Build the stable public error for dataset validation failures."""
    return error_response(
        422,
        message,
        options=_ErrorResponseOptions(
            code="invalid_dataset_format",
            stage="upload_validation",
            retryable=False,
        ),
    )


async def _validate_dataset_upload(
    file: UploadFile,
    *,
    max_bytes: int,
    error_response: ErrorEnvelope,
) -> JSONResponse | None:
    """Validate a dataset before the upload body is buffered or stored."""
    try:
        validated_format(file.filename or "", "dataset")
        byte_size = seek_upload_size(file.file)
        if byte_size > max_bytes:
            return error_response(
                413,
                f"upload exceeds maximum size of {max_bytes} bytes",
            )
        validate_csv_upload(file.file, byte_size=byte_size)
    except (CsvUploadValidationError, InvalidUploadError) as exc:
        return _dataset_error(error_response, str(exc))
    return None


async def read_with_byte_budget(
    file: UploadFile,
    max_bytes: int,
    chunk_size: int = DEFAULT_READ_CHUNK_SIZE,
) -> bytes | None:
    """Buffer ``file`` in chunks, returning None if the budget is breached.

    Unlike ``await file.read()``, this streams the body so an authenticated
    client cannot OOM the worker by sending a chunked-transfer body without
    a ``Content-Length`` header. The accumulator is dropped as soon as the
    next chunk would push past ``max_bytes``, so peak memory stays under
    ``max_bytes + chunk_size`` even on malicious inputs.

    Args:
        file: FastAPI upload whose body to drain into memory.
        max_bytes: Inclusive byte ceiling; the helper returns None on the
            first chunk that pushes the cumulative count past this value.
        chunk_size: Per-iteration read size; defaults to 64 KiB.

    Returns:
        The accumulated body as immutable ``bytes`` when within budget,
        or ``None`` when the cumulative read exceeds ``max_bytes``.
    """
    buffer = bytearray()
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            return bytes(buffer)
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            return None


async def _store_upload(
    upload_request: UploadRequest,
    *,
    purpose: UploadPurpose,
    error_response: ErrorEnvelope,
) -> UploadRecord | JSONResponse:
    """Validate and store an already-buffered upload body."""
    try:
        validated_format(upload_request.original_filename, purpose)
        return await upload_user_file(request=upload_request)
    except UploadTooLargeError as exc:
        return error_response(413, str(exc))
    except InvalidUploadError as exc:
        return error_response(400, str(exc))


async def handle_file_upload(
    request: Request,
    file: UploadFile,
    purpose: UploadPurpose,
    user_id: str,
    error_response: ErrorEnvelope,
) -> FileUploadResponse | JSONResponse:
    """Validate one multipart upload and write it to OBS.

    Pre-checks ``Content-Length`` so oversized requests are rejected
    before the body is read; falls back to ``read_with_byte_budget``
    for the chunked-transfer case (no Content-Length / falsified
    Content-Length) so an authenticated client cannot OOM the worker
    by streaming arbitrarily large bodies. ``upload_user_file`` then
    enforces the same byte ceiling as a defense-in-depth check, and
    maps ``UploadTooLargeError`` to 413 / ``InvalidUploadError`` to
    400 through the caller-supplied ``error_response`` factory so the
    unified API error envelope stays owned by ``api/app.py``.

    Args:
        request: ASGI request used to read ``Content-Length``.
        file: Parsed multipart upload from FastAPI.
        purpose: Caller-declared intent for the file.
        user_id: Authenticated principal that owns the upload.
        error_response: Builder that wraps ``(status, message)`` in the
            project's unified error envelope.

    Returns:
        ``FileUploadResponse`` model instance on success (FastAPI
        serializes it through the route's ``response_model`` with
        ``201``), or the appropriate error envelope on validation
        failure.
    """
    config = ApiConfig()
    max_bytes = config.API_UPLOAD_MAX_BYTES
    if purpose == "dataset":
        validation_error = await _validate_dataset_upload(
            file,
            max_bytes=max_bytes,
            error_response=error_response,
        )
        if validation_error is not None:
            return validation_error
    declared = request.headers.get("content-length")
    if declared is not None and purpose != "dataset":
        try:
            declared_int: int | None = int(declared)
        except ValueError:
            declared_int = None
        if declared_int is not None and declared_int > max_bytes:
            return error_response(
                413,
                f"upload exceeds maximum size of {max_bytes} bytes",
            )
    file_bytes = await read_with_byte_budget(file, max_bytes)
    if file_bytes is None:
        return error_response(
            413,
            f"upload exceeds maximum size of {max_bytes} bytes",
        )
    request_id = current_request_id() or IdFactory().new_id("request")
    stored = await _store_upload(
        UploadRequest(
            file_bytes=file_bytes,
            original_filename=file.filename or "",
            user_id=user_id,
            request_id=request_id,
            storage=UploadStorageOptions(
                max_bytes=max_bytes,
                prefix=config.API_UPLOAD_PREFIX,
            ),
        ),
        purpose=purpose,
        error_response=error_response,
    )
    if isinstance(stored, JSONResponse):
        return stored
    record = stored
    try:
        created_at = _persist_upload_metadata(
            record,
            user_id=user_id,
            purpose=purpose,
        )
    except UploadMetadataPersistenceError:
        return error_response(
            500,
            "The uploaded object could not be registered.",
            options=_ErrorResponseOptions(
                code="upload_metadata_failed",
                stage="upload_persist",
                retryable=False,
            ),
        )
    return FileUploadResponse(
        id=record.file_id,
        bytes=record.bytes,
        filename=record.filename,
        purpose=purpose,
        created_at=created_at,
        obs_path=record.obs_path,
    )
