# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Security regressions for resumable upload-session reclamation."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mcp_server_phytomni.common.logging_config import configure_logging
from mcp_server_phytomni.runtime.resumable_uploads import (
    AssetCreateSpec,
    CapabilityAuthorization,
    ResumableUploadRegistry,
    ResumableUploadRegistryConfig,
    UploadStateError,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 1, tzinfo=UTC)


def _spec() -> AssetCreateSpec:
    """Build one valid provisional upload allocation."""
    return AssetCreateSpec(
        owner_subject="owner-1",
        filename="sample.fa",
        content_type="application/octet-stream",
        size_bytes=3,
        purpose="document",
        idempotency_key="key-1",
    )


def test_expired_capability_cannot_terminalize_due_session(
    tmp_path: Path,
) -> None:
    """A stale matching capability cannot mutate an otherwise due row."""
    registry = ResumableUploadRegistry(
        str(tmp_path / "tasks.db"),
        ResumableUploadRegistryConfig(capability_ttl=timedelta(minutes=1)),
    )
    asset, secret = registry.create_or_replay(_spec(), now=NOW)

    with pytest.raises(UploadStateError) as error:
        registry.authorize_capability(
            secret.raw_token,
            asset_id=asset.asset_id,
            authorization=CapabilityAuthorization("head", True),
            now=NOW + timedelta(minutes=180),
        )

    assert error.value.code == "upload_capability_invalid"
    assert registry.get_asset(asset.asset_id, owner="owner-1") == asset


def test_cleanup_reports_aggregate_expiry_reason_without_row_identity(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup logs only the applicable deadline class and aggregate count."""
    registry = ResumableUploadRegistry(str(tmp_path / "tasks.db"))
    asset, _secret = registry.create_or_replay(_spec(), now=NOW)

    package_logger = logging.getLogger("mcp_server_phytomni")
    monkeypatch.setattr(package_logger, "handlers", [])
    monkeypatch.setattr(package_logger, "level", package_logger.level)
    monkeypatch.setattr(package_logger, "propagate", package_logger.propagate)
    configure_logging()
    registry.cleanup_expired(now=NOW + timedelta(minutes=180))

    captured = capfd.readouterr()
    assert "provisional_deadline=1" in captured.err
    assert asset.asset_id not in captured.err
