# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Edge coverage for owner-scoped upload asset resolution."""

# pylint: disable=protected-access

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
from tests.support.resumable_asset_fakes import (
    ResumableAssetSpec,
    build_resumable_asset,
)

from mcp_server_phytomni.api import asset_resolver as resolver_mod
from mcp_server_phytomni.api.asset_resolver import (
    normalize_asset_attachments,
)
from mcp_server_phytomni.api.resumable_uploads import UploadContractError
from mcp_server_phytomni.api.schemas import AttachmentAsset
from mcp_server_phytomni.runtime.resumable_uploads import AssetRecord
from mcp_server_phytomni.storage.multipart import MultipartStorageError

pytestmark = pytest.mark.unit

_ASSET_ID = "file_" + "a" * 16


def _completed_record(
    *,
    completed_at: datetime | None,
    size_bytes: int = 4,
    status: str = "completed",
) -> AssetRecord:
    """Build one completed-looking asset record for isolated checks."""
    now = datetime(2026, 8, 1, tzinfo=UTC)
    return AssetRecord(
        asset_id=_ASSET_ID,
        owner_subject="owner-1",
        filename="notes.pdf",
        content_type="application/pdf",
        purpose="document",
        size_bytes=size_bytes,
        part_size_bytes=size_bytes,
        part_count=1,
        status=status,  # type: ignore[arg-type]
        object_key="agent_data/uploads/notes.pdf",
        obs_upload_id=None,
        idempotency_key="idem",
        state_version=1,
        reserved_bytes=0,
        created_at=now,
        updated_at=now,
        session_expires_at=now,
        completed_at=completed_at,
        activated_at=now,
    )


def test_materialize_rejects_oversize_and_unsafe_run_id(
    tmp_path: Path,
) -> None:
    """Materialization enforces the byte cap and run-id charset."""
    harness = build_resumable_asset(
        tmp_path, spec=ResumableAssetSpec(content=b"abcd")
    )
    harness.resolver.max_materialized_bytes = 1
    with pytest.raises(UploadContractError) as oversize:
        harness.resolver.materialize(harness.asset_id, harness.owner, "run-1")
    assert oversize.value.code == "upload_limit_exceeded"

    harness.resolver.max_materialized_bytes = 1024
    with pytest.raises(UploadContractError) as unsafe:
        harness.resolver.materialize(
            harness.asset_id, harness.owner, "../evil"
        )
    assert unsafe.value.code == "invalid_upload_metadata"


def test_materialize_replaces_stale_partial_and_size_mismatch(
    tmp_path: Path,
) -> None:
    """A leftover partial is removed; a size mismatch fails closed."""
    harness = build_resumable_asset(
        tmp_path, spec=ResumableAssetSpec(content=b"abcd")
    )
    run_id = "run-partial"
    digest = hashlib.sha256(harness.asset_id.encode("utf-8")).hexdigest()
    run_dir = harness.resolver.workspace_root / run_id
    run_dir.mkdir(parents=True)
    partial = run_dir / f".asset-{digest}.bin.partial"
    partial.write_bytes(b"stale")

    def _wrong_size(**kwargs: Any) -> int:
        kwargs["destination"].write_bytes(b"xx")
        return 2

    harness.resolver.storage = _wrong_size
    with pytest.raises(UploadContractError) as caught:
        harness.resolver.materialize(harness.asset_id, harness.owner, run_id)
    assert caught.value.code == "upload_state_conflict"


def test_materialize_maps_storage_and_os_errors(tmp_path: Path) -> None:
    """Provider and local I/O failures become contract errors."""
    harness = build_resumable_asset(
        tmp_path, spec=ResumableAssetSpec(content=b"abcd")
    )

    def _contract(**kwargs: Any) -> int:
        del kwargs
        raise UploadContractError("upload_state_conflict", 409)

    harness.resolver.storage = _contract
    with pytest.raises(UploadContractError) as contract:
        harness.resolver.materialize(
            harness.asset_id, harness.owner, "run-contract"
        )
    assert contract.value.code == "upload_state_conflict"

    def _missing(**kwargs: Any) -> int:
        del kwargs
        raise MultipartStorageError("upload_asset_not_found")

    harness.resolver.storage = _missing
    with pytest.raises(UploadContractError) as missing:
        harness.resolver.materialize(harness.asset_id, harness.owner, "run-io")
    assert missing.value.code == "upload_asset_not_found"
    assert missing.value.status_code == 404

    def _os_error(**kwargs: Any) -> int:
        kwargs["destination"].write_bytes(b"x")
        raise OSError("disk")

    original_unlink = Path.unlink

    def _unlink(self: Path, *args: Any, **kwargs: Any) -> None:
        if self.name.endswith(".partial"):
            raise OSError("busy")
        original_unlink(self, *args, **kwargs)

    harness.resolver.storage = _os_error
    with (
        patch.object(Path, "unlink", _unlink),
        pytest.raises(UploadContractError) as unavailable,
    ):
        harness.resolver.materialize(harness.asset_id, harness.owner, "run-os")
    assert unavailable.value.code == "upload_storage_unavailable"


def test_cleanup_run_ignores_unsafe_ids_and_unlinks_symlinks(
    tmp_path: Path,
) -> None:
    """Cleanup refuses unsafe run ids and unlinks workspace symlinks."""
    harness = build_resumable_asset(tmp_path)
    harness.resolver.cleanup_run("../outside")
    target = tmp_path / "real-dir"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    harness.resolver.workspace_root.mkdir(parents=True, exist_ok=True)
    link = harness.resolver.workspace_root / "run-link"
    link.symlink_to(target, target_is_directory=True)
    harness.resolver.cleanup_run("run-link")
    assert not link.exists()
    assert marker.exists()


def test_completed_asset_requires_completion_timestamp(
    tmp_path: Path,
) -> None:
    """Completed status without completed_at is a state conflict."""
    harness = build_resumable_asset(tmp_path)
    harness.resolver.registry.get_asset = Mock(
        return_value=_completed_record(completed_at=None)
    )
    with pytest.raises(UploadContractError) as caught:
        harness.resolver.resolve(_ASSET_ID, "owner-1")
    assert caught.value.code == "upload_state_conflict"


def test_legacy_projection_integrity_without_existing_row(
    tmp_path: Path,
) -> None:
    """A colliding legacy insert without a readable row is a conflict."""
    harness = build_resumable_asset(tmp_path)
    harness.resolver.legacy_registry.get_by_path = Mock(return_value=None)
    harness.resolver.legacy_registry.record = Mock(
        side_effect=sqlite3.IntegrityError()
    )
    with pytest.raises(UploadContractError) as caught:
        harness.resolver.resolve_bundle(
            [{"asset_id": harness.asset_id}], harness.owner
        )
    assert caught.value.code == "upload_state_conflict"


def test_normalize_attachments_edges(tmp_path: Path) -> None:
    """Normalization accepts empty input and rejects malformed lists."""
    harness = build_resumable_asset(
        tmp_path, spec=ResumableAssetSpec(filename="context.pdf")
    )
    empty = normalize_asset_attachments(
        {"user_query": "q", "attachments": []},
        owner=harness.owner,
        resolver=harness.resolver,
    )
    assert empty == {"user_query": "q"}

    with pytest.raises(UploadContractError) as invalid:
        normalize_asset_attachments(
            {"attachments": "file_xxx"},
            owner=harness.owner,
            resolver=harness.resolver,
        )
    assert invalid.value.code == "invalid_upload_metadata"

    merged = normalize_asset_attachments(
        {
            "attachments": [{"asset_id": harness.asset_id}],
            "obs_file_list": ["obs://existing/a.pdf"],
        },
        owner=harness.owner,
        resolver=harness.resolver,
    )
    assert merged["obs_file_list"][0] == "obs://existing/a.pdf"
    assert len(merged["obs_file_list"]) == 2

    with pytest.raises(UploadContractError) as bad_existing:
        normalize_asset_attachments(
            {
                "attachments": [{"asset_id": harness.asset_id}],
                "obs_file_list": "obs://existing/a.pdf",
            },
            owner=harness.owner,
            resolver=harness.resolver,
        )
    assert bad_existing.value.code == "invalid_upload_metadata"


def test_asset_id_extraction_and_helpers() -> None:
    """Typed, attribute, and helper edges stay on the public error codes."""
    assert (
        resolver_mod._asset_id_from_item(AttachmentAsset(asset_id=_ASSET_ID))
        == _ASSET_ID
    )

    class _Item:
        asset_id = _ASSET_ID

    assert resolver_mod._asset_id_from_item(_Item()) == _ASSET_ID
    with pytest.raises(UploadContractError):
        resolver_mod._descriptor(_completed_record(completed_at=None))
    with pytest.raises(UploadContractError):
        resolver_mod._iso(None)
    assert resolver_mod._status_for("upload_state_conflict") == 409
    assert resolver_mod._status_for("unknown-code") == 503
