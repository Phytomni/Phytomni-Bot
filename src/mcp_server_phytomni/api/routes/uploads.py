# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Resumable upload route registration and browser data-plane helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from tempfile import SpooledTemporaryFile
from typing import Any, BinaryIO, cast

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from ...storage.multipart import PartInput
from ..asset_resolver import AssetResolver
from ..auth import ApiPrincipal
from ..resumable_uploads import ResumableUploadService, UploadContractError
from ..schemas import (
    AssetDescriptor,
    UploadCapabilityRenewRequest,
    UploadCapabilityResponse,
    UploadCompletionRequest,
    UploadCreateRequest,
    UploadCreateResponse,
    UploadPartResponse,
    UploadStatusResponse,
)

__all__ = ["AgentUploadDependencies", "register_upload_routes"]


@dataclass(frozen=True, slots=True)
class AgentUploadDependencies:
    """Resumable upload and lifecycle seams used by HTTP routes."""

    resumable_service: Callable[[], ResumableUploadService]
    asset_resolver: Callable[[], AssetResolver]
    require_upload_control: Callable[..., Any]
    schedule_cleanup: Callable[..., Any]
    serialize_file_upload_capability: Callable[[], Any]


def register_upload_routes(
    app: FastAPI,
    dependencies: AgentUploadDependencies,
) -> None:
    """Register the single v2 upload protocol on the shared API app."""

    @app.post(
        "/v1/files",
        status_code=201,
        response_model=UploadCreateResponse,
    )
    async def create_upload(
        payload: UploadCreateRequest,
        principal: ApiPrincipal = Depends(dependencies.require_upload_control),
    ) -> JSONResponse:
        """Create one owner-scoped upload through the Web service principal."""
        del principal
        return _upload_json(
            dependencies.resumable_service().create(payload), status_code=201
        )

    @app.post(
        "/v1/files/{asset_id}/capability",
        response_model=UploadCapabilityResponse,
    )
    async def renew_upload_capability(
        asset_id: str,
        payload: UploadCapabilityRenewRequest,
        principal: ApiPrincipal = Depends(dependencies.require_upload_control),
    ) -> JSONResponse:
        """Renew a browser capability for the trusted owner assertion."""
        del principal
        return _upload_json(
            dependencies.resumable_service().renew(
                asset_id, payload.owner_subject
            )
        )

    @app.head("/v1/files/{asset_id}")
    async def head_upload(asset_id: str, request: Request) -> Response:
        """Return resumable state through capability-only response headers."""
        status = dependencies.resumable_service().head(
            asset_id, _capability_from_request(request)
        )
        return Response(headers=_upload_status_headers(status))

    @app.put(
        "/v1/files/{asset_id}/parts/{part_number}",
        response_model=UploadPartResponse,
    )
    async def put_upload_part(
        asset_id: str,
        part_number: int,
        request: Request,
    ) -> JSONResponse:
        """Stream one exact-length part through a bounded temporary file."""
        capability = _capability_from_request(request)
        service = dependencies.resumable_service()
        service.authorize(asset_id, capability, operation="part")
        max_part_size = service.part_size_bytes
        content_length = _content_length_from_request(
            request, max_part_size=max_part_size
        )
        checksum = request.headers.get("x-phytomni-part-sha256")
        if checksum is None or not checksum:
            raise UploadContractError(
                "invalid_upload_metadata", status_code=400
            )
        async with _request_part_body(
            request,
            content_length,
            max_part_size=max_part_size,
        ) as source:
            response = service.put_part(
                asset_id,
                capability,
                _part_input(part_number, source, content_length, checksum),
            )
        return _upload_json(response)

    @app.post(
        "/v1/files/{asset_id}/complete",
        response_model=AssetDescriptor,
    )
    async def complete_upload(
        asset_id: str,
        request: Request,
        payload: UploadCompletionRequest | None = None,
    ) -> JSONResponse:
        """Complete one upload from the authoritative part registry."""
        response = dependencies.resumable_service().complete(
            asset_id,
            _capability_from_request(request),
            payload or UploadCompletionRequest(),
        )
        return _upload_json(response)

    @app.delete(
        "/v1/files/{asset_id}",
        response_model=UploadStatusResponse,
    )
    async def abort_upload(
        asset_id: str,
        request: Request,
    ) -> JSONResponse:
        """Abort one upload and release its provider session."""
        return _upload_json(
            dependencies.resumable_service().abort(
                asset_id, _capability_from_request(request)
            )
        )


def _upload_json(value: Any, *, status_code: int = 200) -> JSONResponse:
    """Serialize one upload model without allowing browser caching."""
    return JSONResponse(
        status_code=status_code,
        content=value.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )


def _capability_from_request(request: Request) -> str:
    """Extract the only credential accepted by browser data-plane routes."""
    authorization = request.headers.get("authorization")
    if authorization is None or not authorization.startswith("Bearer "):
        raise UploadContractError("upload_capability_invalid", status_code=401)
    capability = authorization.removeprefix("Bearer ")
    if not capability or capability != capability.strip():
        raise UploadContractError("upload_capability_invalid", status_code=401)
    return capability


def _content_length_from_request(
    request: Request, *, max_part_size: int
) -> int:
    """Require an exact bounded part length before reading the request body."""
    raw_length = request.headers.get("content-length")
    if raw_length is None:
        raise UploadContractError("invalid_upload_metadata", status_code=400)
    try:
        content_length = int(raw_length)
    except ValueError as error:
        raise UploadContractError(
            "invalid_upload_metadata", status_code=400
        ) from error
    if content_length < 0 or content_length > max_part_size:
        raise UploadContractError("upload_state_conflict", status_code=409)
    return content_length


@asynccontextmanager
async def _request_part_body(
    request: Request,
    content_length: int,
    *,
    max_part_size: int,
) -> AsyncIterator[BinaryIO]:
    """Spool at most one declared part while rejecting length overrun."""
    try:
        with SpooledTemporaryFile(
            max_size=max_part_size, mode="w+b"
        ) as staged:
            received = 0
            async for chunk in request.stream():
                if not chunk:
                    continue
                received += len(chunk)
                if received > content_length:
                    raise UploadContractError(
                        "upload_state_conflict", status_code=409
                    )
                staged.write(chunk)
            if received != content_length:
                raise UploadContractError(
                    "upload_state_conflict", status_code=409
                )
            staged.seek(0)
            yield cast(BinaryIO, staged)
    except UploadContractError:
        raise
    except (OSError, ValueError) as error:
        raise UploadContractError(
            "upload_storage_unavailable", status_code=503, retryable=True
        ) from error


def _part_input(
    part_number: int,
    source: BinaryIO,
    content_length: int,
    checksum: str,
) -> PartInput:
    """Build the storage-port input after request headers are bounded."""
    return PartInput(part_number, source, content_length, checksum)


def _upload_status_headers(status: UploadStatusResponse) -> dict[str, str]:
    """Project resumable state into the browser HEAD header contract."""
    return {
        "Upload-Protocol": status.protocol,
        "Upload-Status": status.status,
        "Upload-Length": str(status.size_bytes),
        "Upload-Part-Size": str(status.part_size_bytes),
        "Upload-Part-Count": str(status.part_count),
        "Upload-Received-Parts": _format_part_ranges(status.received_parts),
        "Cache-Control": "no-store",
    }


def _format_part_ranges(parts: Sequence[int]) -> str:
    """Compact sorted received part numbers for a bounded response header."""
    if not parts:
        return ""
    ranges: list[str] = []
    start = previous = parts[0]
    for part in parts[1:]:
        if part == previous + 1:
            previous = part
            continue
        ranges.append(_format_one_range(start, previous))
        start = previous = part
    ranges.append(_format_one_range(start, previous))
    return ",".join(ranges)


def _format_one_range(start: int, end: int) -> str:
    """Format one inclusive part range."""
    return str(start) if start == end else f"{start}-{end}"
