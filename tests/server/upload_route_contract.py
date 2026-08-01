# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Route-manifest entries for the resumable upload resource."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

__all__ = ["build_upload_route_contracts"]


def build_upload_route_contracts(
    route_factory: Callable[..., Any],
) -> tuple[Any, ...]:
    """Build the six ordered v2 upload route contracts."""
    return (
        route_factory(
            "/v1/files",
            ("POST",),
            201,
            "UploadCreateResponse",
            ("files:delegate",),
            "create_upload",
        ),
        route_factory(
            "/v1/files/{asset_id}/capability",
            ("POST",),
            200,
            "UploadCapabilityResponse",
            ("files:delegate",),
            "renew_upload_capability",
        ),
        route_factory(
            "/v1/files/{asset_id}",
            ("HEAD",),
            200,
            None,
            (),
            "head_upload",
        ),
        route_factory(
            "/v1/files/{asset_id}/parts/{part_number}",
            ("PUT",),
            200,
            "UploadPartResponse",
            (),
            "put_upload_part",
        ),
        route_factory(
            "/v1/files/{asset_id}/complete",
            ("POST",),
            200,
            "AssetDescriptor",
            (),
            "complete_upload",
        ),
        route_factory(
            "/v1/files/{asset_id}",
            ("DELETE",),
            200,
            "UploadStatusResponse",
            (),
            "abort_upload",
        ),
    )
