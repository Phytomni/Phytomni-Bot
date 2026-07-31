# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared v2 upload constants and sanitized contract errors."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "MAX_UPLOAD_BYTES",
    "PART_SIZE_BYTES",
    "UPLOAD_PROTOCOL",
    "UploadContractError",
]

UPLOAD_PROTOCOL = "obs-multipart-v2"
PART_SIZE_BYTES = 128 * 1024**2
MAX_UPLOAD_BYTES = 10 * 1024**3


@dataclass(frozen=True, slots=True)
class UploadContractError(ValueError):
    """A stable upload error that cannot expose storage implementation data."""

    code: str
    status_code: int
    retryable: bool = False

    def __str__(self) -> str:
        """Return a fixed public message keyed only by the stable code."""
        messages = {
            "invalid_upload_metadata": "upload metadata is invalid",
            "upload_capability_invalid": "upload capability is invalid",
            "upload_asset_not_found": "upload asset was not found",
            "upload_state_conflict": "upload state conflict",
            "upload_session_expired": "upload session expired",
            "upload_limit_exceeded": "upload limit exceeded",
            "upload_checksum_mismatch": "upload checksum mismatch",
            "upload_rate_limited": "upload rate limited",
            "obs_outcome_unknown": "upload storage outcome is unknown",
            "upload_storage_unavailable": "upload storage unavailable",
            "unsupported_asset_format": "asset format is unsupported",
        }
        return messages.get(self.code, "upload request failed")
