# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Dependency-neutral resolved upload attachment types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "EffectiveAssetPurpose",
    "ResolvedAsset",
    "ResolvedAttachmentBundle",
]


EffectiveAssetPurpose = Literal["dataset", "document"]


# Trailing fields preserve legacy fake construction while exposing state.
# Keep this compatibility DTO flat: callers construct and project all fields.
# pylint: disable=too-many-instance-attributes
@dataclass(frozen=True, slots=True)
class ResolvedAsset:
    """One owner-validated internal reference with its effective purpose."""

    asset_id: str
    reference: str
    filename: str
    content_type: str
    size_bytes: int
    purpose: EffectiveAssetPurpose
    state_version: int = 0
    completed_at: str | None = None


# pylint: enable=too-many-instance-attributes


@dataclass(frozen=True, slots=True)
class ResolvedAttachmentBundle:
    """Request-ordered owner-validated managed assets."""

    assets: tuple[ResolvedAsset, ...] = ()

    @property
    def documents(self) -> tuple[ResolvedAsset, ...]:
        """Return document assets while retaining their request order."""
        return tuple(
            asset for asset in self.assets if asset.purpose == "document"
        )

    @property
    def datasets(self) -> tuple[ResolvedAsset, ...]:
        """Return dataset assets while retaining their request order."""
        return tuple(
            asset for asset in self.assets if asset.purpose == "dataset"
        )

    @property
    def all_assets(self) -> tuple[ResolvedAsset, ...]:
        """Return the authoritative request order."""
        return self.assets
