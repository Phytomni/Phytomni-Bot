# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Synthetic managed attachment evidence for API contract tests."""

from __future__ import annotations

from mcp_server_phytomni.api.attachment_projection import (
    ManagedAttachmentChannel,
)
from mcp_server_phytomni.api.attachments import ManagedAttachmentEvidenceItem
from mcp_server_phytomni.runtime.attachment_assets import (
    EffectiveAssetPurpose,
    ResolvedAsset,
)

__all__ = [
    "managed_dataset_evidence_item",
    "managed_document_evidence_item",
    "managed_attachment_evidence_item",
]

_STANDARD_EVIDENCE_FIELDS: dict[
    EffectiveAssetPurpose,
    tuple[str, str, int, ManagedAttachmentChannel],
] = {
    "document": (
        "document.pdf",
        "application/pdf",
        1,
        "obs_file_list",
    ),
    "dataset": (
        "dataset.h5ad",
        "application/octet-stream",
        1,
        "data_list",
    ),
}


def managed_attachment_evidence_item(
    asset: ResolvedAsset,
    projected_channel: ManagedAttachmentChannel,
) -> ManagedAttachmentEvidenceItem:
    """Build one ordered managed attachment-evidence item."""
    return ManagedAttachmentEvidenceItem(
        asset=asset,
        projected_channel=projected_channel,
    )


def _managed_standard_evidence_item(
    *,
    asset_id: str,
    reference: str,
    purpose: EffectiveAssetPurpose,
) -> ManagedAttachmentEvidenceItem:
    """Build one standard-purpose managed evidence item."""
    filename, content_type, size_bytes, projected_channel = (
        _STANDARD_EVIDENCE_FIELDS[purpose]
    )
    return managed_attachment_evidence_item(
        ResolvedAsset(
            asset_id=asset_id,
            reference=reference,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            purpose=purpose,
        ),
        projected_channel,
    )


def managed_document_evidence_item(
    *, asset_id: str, reference: str
) -> ManagedAttachmentEvidenceItem:
    """Build one standard document-channel managed evidence item."""
    return _managed_standard_evidence_item(
        asset_id=asset_id,
        reference=reference,
        purpose="document",
    )


def managed_dataset_evidence_item(
    *, asset_id: str, reference: str
) -> ManagedAttachmentEvidenceItem:
    """Build one standard dataset-channel managed evidence item."""
    return _managed_standard_evidence_item(
        asset_id=asset_id,
        reference=reference,
        purpose="dataset",
    )
