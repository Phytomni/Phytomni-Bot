# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Handler for ``POST /v1/files`` multipart upload.

Public functions: handle_file_upload, read_with_byte_budget.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

from fastapi import Request, UploadFile
from fastapi.responses import JSONResponse

from ..config.defaults import ApiConfig
from ..runtime.request_context import current_request_id
from ..storage.path_policy import IdFactory
from ..storage.uploads import (
    InvalidUploadError,
    UploadTooLargeError,
    upload_user_file,
)
from .schemas import FileUploadResponse, UploadPurpose

ErrorEnvelope = Callable[[int, str], JSONResponse]

# 64 KiB chunks keep the per-call read modest and bound peak memory
# to ``max_bytes + DEFAULT_READ_CHUNK_SIZE - 1`` even when the
# Content-Length header is absent or falsified (chunked transfer
# encoding), closing the AF-001 OOM window in audit 2026-05-26.
DEFAULT_READ_CHUNK_SIZE = 64 * 1024


async def read_with_byte_budget(
    file: UploadFile,
    max_bytes: int,
    chunk_size: int = DEFAULT_READ_CHUNK_SIZE,
) -> Optional[bytes]:
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


async def handle_file_upload(
    request: Request,
    file: UploadFile,
    purpose: UploadPurpose,
    user_id: str,
    error_response: ErrorEnvelope,
) -> JSONResponse:
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
        ``201`` JSON response with the ``FileUploadResponse`` shape on
        success, or the appropriate error envelope on validation
        failure.
    """
    config = ApiConfig()
    max_bytes = config.API_UPLOAD_MAX_BYTES
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_int: Optional[int] = int(declared)
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
    try:
        record = await upload_user_file(
            file_bytes=file_bytes,
            original_filename=file.filename or "",
            user_id=user_id,
            request_id=request_id,
            max_bytes=max_bytes,
            prefix=config.API_UPLOAD_PREFIX,
        )
    except UploadTooLargeError as exc:
        return error_response(413, str(exc))
    except InvalidUploadError as exc:
        return error_response(400, str(exc))
    response = FileUploadResponse(
        id=record.file_id,
        bytes=record.bytes,
        filename=record.filename,
        purpose=purpose,
        created_at=int(datetime.now(timezone.utc).timestamp()),
        obs_path=record.obs_path,
        path=record.obs_path,
    )
    return JSONResponse(response.model_dump(), status_code=201)
