# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared public projection for persisted upload asset records."""

from __future__ import annotations

from ..runtime.resumable_uploads import AssetRecord
from .schemas import AssetDescriptor

__all__ = ["build_asset_descriptor"]


def build_asset_descriptor(asset: AssetRecord) -> AssetDescriptor:
    """Project one registry record without exposing provider coordinates."""
    return AssetDescriptor(
        asset_id=asset.asset_id,
        filename=asset.filename,
        content_type=asset.content_type,
        size_bytes=asset.size_bytes,
        status="completed",
        completed_at=asset.completed_at or asset.updated_at,
    )
