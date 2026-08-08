# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Owner-scoped resolution and bounded materialization of upload assets."""

from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from ..agents.research.input_inventory import (
    ManagedResearchAssetResolver,
    ManagedResearchAssetSnapshot,
    managed_research_assets_from_bundle,
)
from ..runtime.attachment_assets import (
    EffectiveAssetPurpose,
    ResolvedAsset,
    ResolvedAttachmentBundle,
)
from ..runtime.resumable_uploads import AssetRecord, ResumableUploadRegistry
from ..runtime.upload_registry import UploadMetadata, UploadRegistry
from ..storage.multipart import (
    CompletedObjectReader,
    MultipartStorageError,
)
from ..storage.obs_storage import obs_path_from_key
from .asset_descriptors import build_asset_descriptor
from .resumable_uploads import UploadContractError
from .schemas import AssetDescriptor, AttachmentAsset

__all__ = [
    "AssetResolver",
    "bind_research_asset_resolver",
    "normalize_asset_attachments",
    "resolve_managed_research_assets",
]


_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_ASSET_ID = re.compile(r"^file_[A-Za-z0-9_-]{16,128}$")


class AssetResolver:
    """Resolve completed assets without widening the public storage surface."""

    def __init__(
        self,
        registry: ResumableUploadRegistry,
        storage: CompletedObjectReader,
        *,
        bucket_name: str,
        workspace_root: Path,
    ) -> None:
        self.registry = registry
        self.storage = storage
        self.bucket_name = bucket_name
        self.workspace_root = workspace_root
        self.max_materialized_bytes = registry.max_upload_bytes
        self.legacy_registry = UploadRegistry(registry.db_path)

    def resolve(self, asset_id: str, owner: str) -> AssetDescriptor:
        """Return a safe descriptor for one completed owner-owned asset."""
        return _descriptor(self._completed_asset(asset_id, owner))

    def resolve_bundle(
        self,
        attachments: Sequence[Any],
        owner: str,
    ) -> ResolvedAttachmentBundle:
        """Resolve owner-owned assets into ordered purpose-aware partitions."""
        asset_ids = tuple(_asset_id_from_item(item) for item in attachments)
        if len(set(asset_ids)) != len(asset_ids):
            raise _asset_error("upload_state_conflict", status_code=409)

        assets = tuple(
            self._completed_asset(asset_id, owner) for asset_id in asset_ids
        )
        purposes: tuple[EffectiveAssetPurpose, ...] = tuple(
            _effective_purpose(asset.purpose) for asset in assets
        )
        references = tuple(
            obs_path_from_key(self.bucket_name, asset.object_key)
            for asset in assets
        )
        if len(set(references)) != len(references):
            raise _asset_error("upload_state_conflict", status_code=409)

        resolved_assets = tuple(
            ResolvedAsset(
                asset_id=asset.asset_id,
                reference=reference,
                filename=asset.filename,
                content_type=asset.content_type,
                size_bytes=asset.size_bytes,
                purpose=purpose,
                state_version=asset.state_version,
                completed_at=_iso(asset.completed_at),
            )
            for asset, purpose, reference in zip(
                assets,
                purposes,
                references,
                strict=True,
            )
        )
        for asset, purpose, reference in zip(
            assets,
            purposes,
            references,
            strict=True,
        ):
            self._ensure_legacy_projection(asset, owner, reference, purpose)

        return ResolvedAttachmentBundle(assets=resolved_assets)

    def materialize(self, asset_id: str, owner: str, run_id: str) -> Path:
        """Stream one completed asset into a private generated run path."""
        asset = self._completed_asset(asset_id, owner)
        if asset.size_bytes > self.max_materialized_bytes:
            raise _asset_error("upload_limit_exceeded", status_code=413)
        if not _SAFE_RUN_ID.fullmatch(run_id):
            raise _asset_error("invalid_upload_metadata", status_code=422)

        run_dir = self.workspace_root / run_id
        target_name = (
            "asset-"
            + hashlib.sha256(asset.asset_id.encode("utf-8")).hexdigest()
            + ".bin"
        )
        target = run_dir / target_name
        partial = run_dir / f".{target_name}.partial"
        try:
            run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            run_dir.chmod(0o700)
            if partial.exists():
                partial.unlink()
            actual_size = self.storage(
                bucket=self.bucket_name,
                object_key=asset.object_key,
                destination=partial,
                expected_size=asset.size_bytes,
            )
            if actual_size != asset.size_bytes:
                raise MultipartStorageError("upload_state_conflict")
            partial.chmod(0o600)
            partial.replace(target)
            target.chmod(0o600)
            return target
        except UploadContractError:
            raise
        except MultipartStorageError as error:
            raise _asset_error(
                error.code, status_code=_status_for(error.code)
            ) from error
        except (OSError, ValueError) as error:
            raise _asset_error(
                "upload_storage_unavailable", status_code=503
            ) from error
        finally:
            try:
                if partial.exists():
                    partial.unlink()
            except OSError:
                pass

    def cleanup_run(self, run_id: str) -> None:
        """Remove one generated run workspace without following symlinks."""
        if not _SAFE_RUN_ID.fullmatch(run_id):
            return
        run_dir = self.workspace_root / run_id
        try:
            if run_dir.is_symlink():
                run_dir.unlink()
            else:
                shutil.rmtree(run_dir, ignore_errors=False)
        except FileNotFoundError:
            return

    def internal_reference(self, asset_id: str, owner: str) -> str:
        """Return an owner-checked OBS reference for Agent internals."""
        bundle = self.resolve_bundle(({"asset_id": asset_id},), owner)
        return bundle.all_assets[0].reference

    def _completed_asset(self, asset_id: str, owner: str) -> AssetRecord:
        """Load one completed asset while failing closed on ownership."""
        if (
            not isinstance(asset_id, str)
            or not isinstance(owner, str)
            or not owner
            or not _ASSET_ID.fullmatch(asset_id)
        ):
            raise _asset_error("upload_asset_not_found", status_code=404)
        asset = self.registry.get_asset(asset_id, owner=owner)
        if asset is None:
            raise _asset_error("upload_asset_not_found", status_code=404)
        if asset.status != "completed":
            raise _asset_error("upload_state_conflict", status_code=409)
        if asset.completed_at is None:
            raise _asset_error("upload_state_conflict", status_code=409)
        return asset

    def _ensure_legacy_projection(
        self,
        asset: AssetRecord,
        owner: str,
        reference: str,
        purpose: EffectiveAssetPurpose,
    ) -> None:
        """Project only safe completed metadata for legacy Agent validators."""
        if (
            self.legacy_registry.get_by_path(reference, owner=owner)
            is not None
        ):
            return
        metadata = UploadMetadata(
            file_id=asset.asset_id,
            user_id=owner,
            obs_path=reference,
            filename=asset.filename,
            purpose=purpose,
            byte_size=asset.size_bytes,
            format=_filename_format(asset.filename),
            media_type=asset.content_type or "application/octet-stream",
            created_at=_iso(asset.completed_at),
        )
        try:
            self.legacy_registry.record(metadata)
        except sqlite3.IntegrityError:
            existing = self.legacy_registry.get_by_path(reference, owner=owner)
            if existing is None:
                raise _asset_error(
                    "upload_state_conflict", status_code=409
                ) from None


def resolve_managed_research_assets(
    attachments: Sequence[Any],
    *,
    owner: str,
    resolver: AssetResolver,
) -> tuple[ManagedResearchAssetSnapshot, ...]:
    """Resolve owner-owned assets into immutable Research snapshots only."""
    return managed_research_assets_from_bundle(
        resolver.resolve_bundle(attachments, owner)
    )


def bind_research_asset_resolver(
    *,
    owner: str,
    resolver: AssetResolver,
) -> ManagedResearchAssetResolver:
    """Bind one owner to current managed Research asset resolution."""

    def resolve(
        asset_ids: tuple[str, ...],
    ) -> tuple[ManagedResearchAssetSnapshot, ...]:
        """Resolve only the original opaque IDs under the bound owner."""
        return resolve_managed_research_assets(
            tuple({"asset_id": asset_id} for asset_id in asset_ids),
            owner=owner,
            resolver=resolver,
        )

    return resolve


def normalize_asset_attachments(
    arguments: Mapping[str, Any],
    *,
    owner: str,
    resolver: AssetResolver,
) -> dict[str, Any]:
    """Convert Web asset references into controlled internal attachments."""
    normalized = dict(arguments)
    raw_attachments = normalized.pop("attachments", None)
    if raw_attachments in (None, []):
        return normalized
    if isinstance(raw_attachments, (str, bytes, bytearray)) or not isinstance(
        raw_attachments, Sequence
    ):
        raise _asset_error("invalid_upload_metadata", status_code=422)

    bundle = resolver.resolve_bundle(raw_attachments, owner)
    if bundle.datasets:
        raise _asset_error("upload_state_conflict", status_code=409)
    references = [asset.reference for asset in bundle.documents]
    existing = normalized.get("obs_file_list")
    if existing is None:
        normalized["obs_file_list"] = references
    elif isinstance(existing, Sequence) and not isinstance(
        existing, (str, bytes, bytearray)
    ):
        normalized["obs_file_list"] = [*existing, *references]
    else:
        raise _asset_error("invalid_upload_metadata", status_code=422)
    return normalized


def _asset_id_from_item(item: Any) -> str:
    """Extract one strict asset reference and nothing else from input JSON."""
    asset_id: Any = None
    if isinstance(item, AttachmentAsset):
        asset_id = item.asset_id
    elif isinstance(item, Mapping):
        asset_id = item.get("asset_id")
    else:
        asset_id = getattr(item, "asset_id", None)
    if not isinstance(asset_id, str) or not _ASSET_ID.fullmatch(asset_id):
        raise _asset_error("invalid_upload_metadata", status_code=422)
    return asset_id


def _effective_purpose(purpose: str) -> EffectiveAssetPurpose:
    """Map persisted upload purpose to one resolver partition."""
    if purpose in {"chat_attachment", "document"}:
        return "document"
    if purpose == "dataset":
        return "dataset"
    raise _asset_error("upload_state_conflict", status_code=409)


def _descriptor(asset: AssetRecord) -> AssetDescriptor:
    """Project only public-safe completed metadata."""
    completed_at = asset.completed_at
    if completed_at is None:
        raise _asset_error("upload_state_conflict", status_code=409)
    return build_asset_descriptor(asset)


def _filename_format(filename: str) -> str:
    """Derive a bounded format label from trusted display metadata."""
    suffix = Path(filename).suffix.lower().lstrip(".")
    return suffix or "binary"


def _iso(value: datetime | None) -> str:
    """Serialize trusted completion time for the legacy projection."""
    if value is None:
        raise _asset_error("upload_state_conflict", status_code=409)
    return value.isoformat()


def _asset_error(code: str, *, status_code: int) -> UploadContractError:
    """Create a stable error without embedding asset or storage details."""
    return UploadContractError(code=code, status_code=status_code)


def _status_for(code: str) -> int:
    """Map storage errors to the public upload status boundary."""
    return {
        "upload_asset_not_found": 404,
        "upload_state_conflict": 409,
        "upload_limit_exceeded": 413,
        "upload_storage_unavailable": 503,
    }.get(code, 503)
