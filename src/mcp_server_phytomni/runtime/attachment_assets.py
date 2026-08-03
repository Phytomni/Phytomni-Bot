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


@dataclass(frozen=True, slots=True)
class ResolvedAsset:
    """One owner-validated internal reference with its effective purpose."""

    asset_id: str
    reference: str
    filename: str
    content_type: str
    size_bytes: int
    purpose: EffectiveAssetPurpose


@dataclass(frozen=True, slots=True)
class ResolvedAttachmentBundle:
    """Purpose-partitioned resolved attachment references."""

    documents: tuple[ResolvedAsset, ...] = ()
    datasets: tuple[ResolvedAsset, ...] = ()

    @property
    def all_assets(self) -> tuple[ResolvedAsset, ...]:
        """Return partition order for validation-only consumers."""
        return (*self.documents, *self.datasets)
