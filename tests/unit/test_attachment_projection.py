# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for pure managed-attachment capability projection."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.api.agent_capabilities import (
    AttachmentCapability,
    DatasetCapability,
    DocumentContextCapability,
)
from mcp_server_phytomni.api.attachment_projection import (
    AttachmentProjectionError,
    project_managed_attachments,
)
from mcp_server_phytomni.runtime.attachment_assets import (
    EffectiveAssetPurpose,
    ResolvedAsset,
    ResolvedAttachmentBundle,
)

pytestmark = pytest.mark.unit


def _asset(name: str, purpose: EffectiveAssetPurpose) -> ResolvedAsset:
    """Build one purpose-tagged managed attachment test asset."""
    return ResolvedAsset(
        asset_id=f"file_{name}",
        reference=f"obs://managed/{name}",
        filename=f"{name}.{'csv' if purpose == 'dataset' else 'pdf'}",
        content_type=(
            "text/csv" if purpose == "dataset" else "application/pdf"
        ),
        size_bytes=1,
        purpose=purpose,
    )


@pytest.fixture(name="ordered_bundle_fixture")
def _ordered_bundle_fixture() -> ResolvedAttachmentBundle:
    """Return the supplied order independent from effective purpose."""
    return ResolvedAttachmentBundle(
        assets=(
            _asset("dataset_a", "dataset"),
            _asset("document_b", "document"),
            _asset("dataset_c", "dataset"),
            _asset("document_d", "document"),
        )
    )


@pytest.mark.parametrize(
    "capability",
    (
        AttachmentCapability(DocumentContextCapability(), DatasetCapability()),
        AttachmentCapability(document_context=DocumentContextCapability()),
        AttachmentCapability(datasets=DatasetCapability()),
        AttachmentCapability(),
    ),
)
def test_empty_bundle_projects_to_empty_channels(
    capability: AttachmentCapability,
) -> None:
    """Every capability shape preserves an empty managed bundle."""
    assert not project_managed_attachments(
        ResolvedAttachmentBundle(), capability
    ).obs_assets
    assert not project_managed_attachments(
        ResolvedAttachmentBundle(), capability
    ).data_assets


def test_dual_channel_projection_preserves_purpose_lanes_and_identity(
    ordered_bundle_fixture: ResolvedAttachmentBundle,
) -> None:
    """Dual channels preserve source objects while separating purposes."""
    projected = project_managed_attachments(
        ordered_bundle_fixture,
        AttachmentCapability(DocumentContextCapability(), DatasetCapability()),
    )

    assert projected.obs_assets == (
        ordered_bundle_fixture.assets[1],
        ordered_bundle_fixture.assets[3],
    )
    assert projected.data_assets == (
        ordered_bundle_fixture.assets[0],
        ordered_bundle_fixture.assets[2],
    )
    assert [asset.purpose for asset in projected.obs_assets] == [
        "document",
        "document",
    ]
    assert [asset.purpose for asset in projected.data_assets] == [
        "dataset",
        "dataset",
    ]
    assert all(
        projected_asset is source_asset
        for projected_asset, source_asset in zip(
            (*projected.obs_assets, *projected.data_assets),
            (
                ordered_bundle_fixture.assets[1],
                ordered_bundle_fixture.assets[3],
                ordered_bundle_fixture.assets[0],
                ordered_bundle_fixture.assets[2],
            ),
            strict=True,
        )
    )


def test_document_only_projection_preserves_source_order_and_identity(
    ordered_bundle_fixture: ResolvedAttachmentBundle,
) -> None:
    """A document-only consumer receives every original object in order."""
    projected = project_managed_attachments(
        ordered_bundle_fixture,
        AttachmentCapability(document_context=DocumentContextCapability()),
    )

    assert projected.obs_assets == ordered_bundle_fixture.assets
    assert not projected.data_assets
    assert all(
        projected_asset is source_asset
        for projected_asset, source_asset in zip(
            projected.obs_assets, ordered_bundle_fixture.assets, strict=True
        )
    )


def test_dataset_only_projection_preserves_source_order_and_identity(
    ordered_bundle_fixture: ResolvedAttachmentBundle,
) -> None:
    """A dataset-only consumer receives every original object in order."""
    projected = project_managed_attachments(
        ordered_bundle_fixture,
        AttachmentCapability(datasets=DatasetCapability()),
    )

    assert not projected.obs_assets
    assert projected.data_assets == ordered_bundle_fixture.assets
    assert all(
        projected_asset is source_asset
        for projected_asset, source_asset in zip(
            projected.data_assets, ordered_bundle_fixture.assets, strict=True
        )
    )


def test_zero_channel_projection_rejects_nonempty_bundle(
    ordered_bundle_fixture: ResolvedAttachmentBundle,
) -> None:
    """Managed input cannot reach an attachmentless capability."""
    with pytest.raises(AttachmentProjectionError) as error:
        project_managed_attachments(
            ordered_bundle_fixture, AttachmentCapability()
        )

    assert error.value.code == "attachment_not_supported"
