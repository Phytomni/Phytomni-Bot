# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""OBS upload helper for the ``POST /v1/files`` HTTP endpoint.

Classes: UploadRecord, InvalidUploadError, UploadTooLargeError.
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


# pylint: disable=too-many-arguments,too-many-locals
# Conceptually-atomic OBS upload (validate -> resolve target ->
# write). Splitting the args into dataclasses adds caller boilerplate
# without splitting the responsibility. See docs/lint-exemptions.md.
async def upload_user_file(
    file_bytes: bytes,
    original_filename: str,
    user_id: str,
    request_id: str,
    *,
    max_bytes: int,
    prefix: str,
    bucket_name: str | None = None,
    obs_server: str | None = None,
    obsfs_mount_root: str = DEFAULT_OBSFS_MOUNT_ROOT,
) -> UploadRecord:
    """Validate and store one user upload, returning its OBS coordinates.

    Args:
        file_bytes: Raw upload body already read into memory.
        original_filename: Client-supplied filename, sanitized internally.
        user_id: Authenticated principal whose ``ptm_`` key owns this
            upload; embedded into the OBS object key for per-user
            isolation. Empty/missing falls back to ``anonymous``.
        request_id: Per-request correlation id (the same value carried
            on the ``X-Request-Id`` response header) so an upload can be
            traced back to one HTTP call.
        max_bytes: Inclusive size ceiling; payloads larger than this
            raise ``UploadTooLargeError``.
        prefix: OBS object-key prefix below the bucket root (e.g.
            ``agent_data/uploads``).
        bucket_name: OBS bucket override; defaults to ``ServerConfig``.
        obs_server: OBS endpoint override; defaults to ``ServerConfig``.
        obsfs_mount_root: Filesystem root for the obsfs mount.

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
    if not file_bytes:
        raise InvalidUploadError("upload body is empty")
    if len(file_bytes) > max_bytes:
        raise UploadTooLargeError(
            f"upload of {len(file_bytes)} bytes exceeds "
            f"max_bytes={max_bytes}"
        )
    if "/" in prefix.strip("/") and any(
        part in {".", ".."} for part in prefix.split("/")
    ):
        raise InvalidUploadError(f"unsafe upload prefix: {prefix!r}")

    safe_filename = safe_upload_filename(original_filename)
    safe_user = safe_path_segment(user_id or DEFAULT_USER_ID, DEFAULT_USER_ID)
    safe_request = safe_path_segment(request_id, "request")
    file_id = IdFactory().new_id("upload")
    target_bucket = bucket_name or _SERVER_DEFAULTS.BUCKET_NAME
    target_server = obs_server or _SERVER_DEFAULTS.OBS_SERVER
    object_key = (
        f"{prefix.strip('/')}/{safe_user}/{safe_request}/"
        f"{file_id}/{safe_filename}"
    )

    obs_path = obs_path_from_key(target_bucket, object_key)
    if relay_mode_enabled():
        await current_relay_client().put_obs_object(
            obs_path, file_bytes, message="Failed to upload file via relay"
        )
    else:
        put_object_bytes(
            target_bucket,
            object_key,
            file_bytes,
            obs_server=target_server,
            mount_root=obsfs_mount_root,
        )

    return UploadRecord(
        file_id=file_id,
        filename=safe_filename,
        bytes=len(file_bytes),
        obs_path=obs_path,
    )
