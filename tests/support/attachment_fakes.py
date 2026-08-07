# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Synthetic managed attachment evidence for API contract tests."""

from __future__ import annotations

from dataclasses import dataclass

from mcp_server_phytomni.api.attachment_projection import (
    ManagedAttachmentChannel,
)
from mcp_server_phytomni.api.attachments import ManagedAttachmentEvidenceItem
from mcp_server_phytomni.runtime.attachment_assets import (
    EffectiveAssetPurpose,
    ResolvedAsset,
)

__all__ = [
    "ManagedAttachmentEvidenceSpec",
    "managed_attachment_evidence_item",
]


@dataclass(frozen=True, slots=True)
class ManagedAttachmentEvidenceSpec:
    """Explicit values for one synthetic managed attachment."""

    asset_id: str
    reference: str
    filename: str
    content_type: str
    size_bytes: int
    purpose: EffectiveAssetPurpose
    projected_channel: ManagedAttachmentChannel


def managed_attachment_evidence_item(
    spec: ManagedAttachmentEvidenceSpec,
) -> ManagedAttachmentEvidenceItem:
    """Build one ordered managed attachment-evidence item."""
    return ManagedAttachmentEvidenceItem(
        asset=ResolvedAsset(
            asset_id=spec.asset_id,
            reference=spec.reference,
            filename=spec.filename,
            content_type=spec.content_type,
            size_bytes=spec.size_bytes,
            purpose=spec.purpose,
        ),
        projected_channel=spec.projected_channel,
    )
