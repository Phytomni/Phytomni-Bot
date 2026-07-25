# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""OBS upload helper for the ``POST /v1/files`` HTTP endpoint.

Classes: UploadRequest, UploadStorageOptions, UploadRecord,
InvalidUploadError, UploadTooLargeError.
Functions: upload_user_file, safe_upload_filename.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..common.relay_client import current_relay_client
from ..config.defaults import ServerConfig
from ..config.relay_mode import relay_mode_enabled
from .obs_relay_ops import put_object_bytes
from .obs_storage import DEFAULT_OBSFS_MOUNT_ROOT, obs_path_from_key
from .path_policy import DEFAULT_USER_ID, IdFactory, safe_path_segment

_SERVER_DEFAULTS = ServerConfig()


class InvalidUploadError(ValueError):
    """Raised when an upload payload fails pre-write validation.

    Maps to HTTP 400 at the API boundary. Causes include an empty
    file body, an empty or path-traversing original filename, and an
    OBS object-key prefix that contains path separators.
    """


class UploadTooLargeError(ValueError):
    """Raised when an upload exceeds the configured size ceiling.

    Maps to HTTP 413 at the API boundary. The API layer also performs
    a pre-read ``Content-Length`` check so oversized requests are
    rejected before the body is buffered into memory.
    """


@dataclass(frozen=True)
class UploadStorageOptions:
    """Immutable storage settings for one upload operation."""

    max_bytes: int
    prefix: str
    bucket_name: str | None = None
    obs_server: str | None = None
    obsfs_mount_root: str = DEFAULT_OBSFS_MOUNT_ROOT


@dataclass(frozen=True)
class UploadRequest:
    """Immutable input for one validated upload operation."""

    file_bytes: bytes
    original_filename: str
    user_id: str
    request_id: str
    storage: UploadStorageOptions


@dataclass(frozen=True)
class UploadRecord:
    """Immutable record describing a single accepted upload.

    Attributes:
        file_id: Run-policy ID identifying this upload within the
            request scope (``upload_...`` token from ``IdFactory``).
        filename: Sanitized basename actually written to OBS.
        bytes: Byte length of the stored content.
        obs_path: Public ``/obs/<bucket>/<key>`` path that clients can
            replay into a later ``obs_file_list`` argument.
    """

    file_id: str
    filename: str
    bytes: int
    obs_path: str


def safe_upload_filename(original_filename: str) -> str:
    """Return a path-safe basename for a user-supplied filename.

    Args:
        original_filename: Raw ``UploadFile.filename`` as received from
            the multipart parser. May contain path components, Unicode,
            shell metacharacters, or ``..`` traversal segments.

    Returns:
        A single basename with the original suffix preserved and the
        stem run through ``safe_path_segment`` so unsafe characters are
        replaced with ``-`` before being joined into an OBS object key.

    Raises:
        InvalidUploadError: If the filename is empty, resolves to ``.``
            or ``..``, or carries no sanitizable stem after the suffix
            is stripped.
    """
    if not original_filename or not original_filename.strip():
        raise InvalidUploadError("upload filename is empty")
    basename = PurePosixPath(original_filename.replace("\\", "/")).name
    if not basename or basename in {".", ".."}:
        raise InvalidUploadError(
            f"upload filename is not a valid basename: {original_filename!r}"
        )
    suffix = Path(basename).suffix
    stem = basename[: -len(suffix)] if suffix else basename
    if not stem:
        raise InvalidUploadError(
            f"upload filename has no stem: {original_filename!r}"
        )
    safe_stem = safe_path_segment(stem, "file")
    return f"{safe_stem}{suffix}" if suffix else safe_stem


_MEDIA_TYPES = {
    "csv": "text/csv",
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml."
        "document"
    ),
    "msg": "application/vnd.ms-outlook",
    "pdf": "application/pdf",
    "pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml."
        "presentation"
    ),
    "txt": "text/plain",
    "xls": "application/vnd.ms-excel",
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml." "sheet"
    ),
}


def validated_format(filename: str, purpose: str) -> str:
    """Return a stable extension label, enforcing CSV dataset inputs."""
    extension = Path(filename).suffix.lower().lstrip(".")
    if purpose == "dataset" and extension != "csv":
        raise InvalidUploadError("dataset uploads require a CSV filename")
    return extension or "binary"


def validated_media_type(filename: str, purpose: str) -> str:
    """Return a fixed media type derived from the validated filename."""
    extension = validated_format(filename, purpose)
    return _MEDIA_TYPES.get(extension, "application/octet-stream")


async def upload_user_file(request: UploadRequest) -> UploadRecord:
    """Validate and store one user upload, returning its OBS coordinates.

    Args:
        request: Raw bytes plus caller identity and storage settings.

    Returns:
        An ``UploadRecord`` with the assigned ``file_id``, the sanitized
        ``filename``, the stored ``bytes`` count, and the public
        ``obs_path`` (``/obs/<bucket>/<key>``).

    Raises:
        InvalidUploadError: For empty payloads, unsafe filenames, or
            prefixes that escape the bucket root.
        UploadTooLargeError: If ``len(file_bytes) > max_bytes``.
        OSError: If both the obsfs write and the OBS SDK fallback fail.
    """
    if not request.file_bytes:
        raise InvalidUploadError("upload body is empty")
    if len(request.file_bytes) > request.storage.max_bytes:
        raise UploadTooLargeError(
            f"upload of {len(request.file_bytes)} bytes exceeds "
            f"max_bytes={request.storage.max_bytes}"
        )
    if "/" in request.storage.prefix.strip("/") and any(
        part in {".", ".."} for part in request.storage.prefix.split("/")
    ):
        raise InvalidUploadError(
            f"unsafe upload prefix: {request.storage.prefix!r}"
        )

    safe_filename = safe_upload_filename(request.original_filename)
    safe_user = safe_path_segment(
        request.user_id or DEFAULT_USER_ID, DEFAULT_USER_ID
    )
    safe_request = safe_path_segment(request.request_id, "request")
    file_id = IdFactory().new_id("upload")
    target_bucket = request.storage.bucket_name or _SERVER_DEFAULTS.BUCKET_NAME
    target_server = request.storage.obs_server or _SERVER_DEFAULTS.OBS_SERVER
    object_key = (
        f"{request.storage.prefix.strip('/')}/{safe_user}/{safe_request}/"
        f"{file_id}/{safe_filename}"
    )

    obs_path = obs_path_from_key(target_bucket, object_key)
    if relay_mode_enabled():
        await current_relay_client().put_obs_object(
            obs_path,
            request.file_bytes,
            message="Failed to upload file via relay",
        )
    else:
        put_object_bytes(
            target_bucket,
            object_key,
            request.file_bytes,
            obs_server=target_server,
            mount_root=request.storage.obsfs_mount_root,
        )

    return UploadRecord(
        file_id=file_id,
        filename=safe_filename,
        bytes=len(request.file_bytes),
        obs_path=obs_path,
    )
