# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Handler for ``POST /v1/files`` multipart upload.

Public functions: handle_file_upload.
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
from .schemas import FileUploadResponse

ErrorEnvelope = Callable[[int, str], JSONResponse]


async def handle_file_upload(
    request: Request,
    file: UploadFile,
    purpose: str,
    user_id: str,
    error_response: ErrorEnvelope,
) -> JSONResponse:
    """Validate one multipart upload and write it to OBS.

    Pre-checks ``Content-Length`` so oversized requests are rejected
    before the body is buffered; delegates to ``upload_user_file`` for
    the storage write and post-read size guard. Maps
    ``UploadTooLargeError`` to 413 and ``InvalidUploadError`` to 400
    through the caller-supplied ``error_response`` factory so the
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
    file_bytes = await file.read()
    request_id = current_request_id() or IdFactory().new_id("request")
    try:
        record = upload_user_file(
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
