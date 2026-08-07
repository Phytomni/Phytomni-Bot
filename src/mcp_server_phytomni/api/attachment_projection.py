# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Pure capability projection for managed attachment bundles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..runtime.attachment_assets import (
    ResolvedAsset,
    ResolvedAttachmentBundle,
)
from .agent_capabilities import AttachmentCapability

__all__ = [
    "AttachmentProjectionError",
    "ManagedAttachmentChannel",
    "ManagedAttachmentProjection",
    "project_managed_attachments",
]


type ManagedAttachmentChannel = Literal["obs_file_list", "data_list"]


@dataclass(frozen=True, slots=True)
class ManagedAttachmentProjection:
    """Managed assets assigned to the native attachment argument channels."""

    obs_assets: tuple[ResolvedAsset, ...] = ()
    data_assets: tuple[ResolvedAsset, ...] = ()


class AttachmentProjectionError(ValueError):
    """Raised when a capability cannot receive managed attachments."""

    code = "attachment_not_supported"


def project_managed_attachments(
    bundle: ResolvedAttachmentBundle,
    capability: AttachmentCapability,
) -> ManagedAttachmentProjection:
    """Project one ordered managed bundle through capability channel shape."""
    has_documents = capability.document_context is not None
    has_datasets = capability.datasets is not None
    if not bundle.assets:
        return ManagedAttachmentProjection()
    if has_documents and has_datasets:
        return ManagedAttachmentProjection(bundle.documents, bundle.datasets)
    if has_documents:
        return ManagedAttachmentProjection(obs_assets=bundle.assets)
    if has_datasets:
        return ManagedAttachmentProjection(data_assets=bundle.assets)
    raise AttachmentProjectionError()
