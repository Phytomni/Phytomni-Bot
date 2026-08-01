# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Validate attachment references at the authenticated API boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, NoReturn

from ..config.defaults import ApiConfig, ServerConfig
from ..runtime.locale import current_effective_locale
from ..runtime.upload_registry import UploadMetadata, UploadRegistry
from ..storage.obs_storage import ObsPathError, normalize_obs_object_key
from .agent_capabilities import (
    DOCUMENT_EXTENSIONS,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    get_agent_capability,
    get_attachment_capability,
)
from .asset_resolver import normalize_asset_attachments
from .lifecycle_contract import SafeApiError

__all__ = [
    "AttachmentContractError",
    "AttachmentSelection",
    "is_managed_upload_path",
    "normalize_asset_attachments",
    "legacy_dataset_path_allowed",
    "prepare_expert_arguments",
    "validate_agent_attachments",
]


_DOCUMENT_PURPOSES = frozenset(
    {
        "agent_context",
        "assistants",
        "batch",
        "document",
        "fine-tune",
        "user_data",
        "vision",
    }
)
_DATASET_PURPOSE = "dataset"


@dataclass(frozen=True, slots=True)
class AttachmentSelection:
    """Verified attachment references normalized for an agent invocation."""

    documents: tuple[UploadMetadata, ...] = ()
    datasets: tuple[UploadMetadata, ...] = ()
    legacy_dataset_paths: tuple[str, ...] = ()


class AttachmentContractError(ValueError):
    """A stable, sanitized attachment contract failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_native_attachments(
    agent: str,
    arguments: Mapping[str, Any],
    *,
    owner: str,
    db_path: str,
) -> None:
    """Validate one API invocation and project failures into HTTP state."""
    try:
        validate_agent_attachments(
            agent,
            arguments,
            owner=owner,
            registry=UploadRegistry(db_path),
        )
    except AttachmentContractError as exc:
        raise SafeApiError(
            status_code=422,
            code=exc.code,
            message=str(exc),
            stage="attachment_validation",
            retryable=False,
        ) from exc


def prepare_expert_arguments(
    agent: str,
    selected_arguments: Mapping[str, Any],
    *,
    obs_file_list: Sequence[str],
    owner: str,
    db_path: str,
) -> dict[str, Any]:
    """Prepare Expert arguments under the selected capability contract."""
    capability = get_agent_capability(agent)
    arguments = dict(selected_arguments)
    selected_obs_file_list = arguments.get("obs_file_list")
    arguments.pop("obs_file_list", None)
    arguments["locale"] = current_effective_locale()
    if obs_file_list:
        if not capability.attachments.expert_forwarding:
            raise SafeApiError(
                status_code=422,
                code="attachment_not_supported",
                message=(
                    "The selected agent does not accept Expert attachments."
                ),
                stage="attachment_validation",
                retryable=False,
            )
        arguments["obs_file_list"] = list(obs_file_list)
    elif (
        selected_obs_file_list == []
        or capability.attachments.document_context is not None
    ):
        # Preserve the empty schema value; selector-generated paths are not
        # trusted or forwarded.
        arguments["obs_file_list"] = []
    validate_native_attachments(
        agent,
        arguments,
        owner=owner,
        db_path=db_path,
    )
    return arguments


def validate_agent_attachments(
    agent: str,
    arguments: Mapping[str, Any],
    *,
    owner: str,
    registry: UploadRegistry,
) -> AttachmentSelection:
    """Validate all declared attachment references for one native agent."""
    document_paths = _document_paths(arguments)
    dataset_items = _dataset_items(arguments)
    all_paths = (*document_paths, *(path for path, _ in dataset_items))
    _reject_duplicate_paths(all_paths)

    capability = _attachment_capability(agent)
    if document_paths and capability.document_context is None:
        _raise(
            "attachment_not_supported",
            "This agent does not support document attachments.",
        )
    if dataset_items and capability.datasets is None:
        _raise(
            "attachment_not_supported",
            "This agent does not support dataset attachments.",
        )

    documents = tuple(
        _resolve_document_path(path, owner=owner, registry=registry)
        for path in document_paths
    )
    datasets, legacy_paths, descriptions = _resolve_datasets(
        dataset_items,
        owner=owner,
        registry=registry,
    )
    for metadata in documents:
        _validate_metadata(metadata, channel="documents")
    for metadata in datasets:
        _validate_metadata(metadata, channel="datasets")
    for description in descriptions:
        _validate_dataset_description(description)
    _validate_budget((*documents, *datasets))
    return AttachmentSelection(
        documents=documents,
        datasets=datasets,
        legacy_dataset_paths=legacy_paths,
    )


def _resolve_datasets(
    dataset_items: Sequence[tuple[str, Any]],
    *,
    owner: str,
    registry: UploadRegistry,
) -> tuple[
    tuple[UploadMetadata, ...],
    tuple[str, ...],
    tuple[Any, ...],
]:
    """Resolve user and legacy datasets while preserving descriptions."""
    datasets: list[UploadMetadata] = []
    legacy_paths: list[str] = []
    descriptions: list[Any] = []
    for path, description in dataset_items:
        metadata, is_legacy = _resolve_dataset_path(
            path,
            owner=owner,
            registry=registry,
        )
        if is_legacy:
            legacy_paths.append(path)
        elif metadata is not None:
            datasets.append(metadata)
        descriptions.append(description)
    return (
        tuple(datasets),
        tuple(legacy_paths),
        tuple(descriptions),
    )


def is_managed_upload_path(path: str) -> bool:
    """Return whether ``path`` is inside the configured user-upload prefix."""
    key = _normalized_key(path)
    if key is None:
        return False
    prefix = ApiConfig().API_UPLOAD_PREFIX.strip("/")
    return key == prefix or key.startswith(f"{prefix}/")


def legacy_dataset_path_allowed(path: str) -> bool:
    """Reuse the existing OBS path normalizer for preconfigured datasets."""
    if not isinstance(path, str) or not path or is_managed_upload_path(path):
        return False
    return _normalized_key(path) is not None


def _attachment_capability(agent: str):
    """Return a known capability, failing closed for unknown slugs."""
    try:
        return get_attachment_capability(agent)
    except KeyError as exc:
        raise AttachmentContractError(
            "attachment_not_supported",
            "This agent does not support attachments.",
        ) from exc


def _document_paths(arguments: Mapping[str, Any]) -> tuple[str, ...]:
    """Read document paths without changing their caller-provided values."""
    raw_paths = arguments.get("obs_file_list")
    if raw_paths is None:
        return ()
    if isinstance(raw_paths, (str, bytes, bytearray)) or not isinstance(
        raw_paths, Sequence
    ):
        _raise(
            "attachment_format_unsupported",
            "Document attachments have an invalid shape.",
        )
    paths = tuple(raw_paths)
    if any(not isinstance(path, str) for path in paths):
        _raise(
            "attachment_not_found",
            "The attachment could not be verified.",
        )
    return tuple(path for path in paths if isinstance(path, str))


def _dataset_items(
    arguments: Mapping[str, Any],
) -> tuple[tuple[str, Any], ...]:
    """Read dataset paths and preserve each description for validation."""
    raw_data = arguments.get("data_list")
    if raw_data is None:
        return ()
    if not isinstance(raw_data, Mapping):
        _raise(
            "attachment_format_unsupported",
            "Dataset attachments have an invalid shape.",
        )
    items = tuple(raw_data.items())
    if any(not isinstance(path, str) for path, _ in items):
        _raise(
            "attachment_not_found",
            "The attachment could not be verified.",
        )
    return items


def _reject_duplicate_paths(paths: Sequence[str]) -> None:
    """Reject repeated references before any owner or capability lookup."""
    if len(paths) != len(set(paths)):
        _raise(
            "attachment_duplicate",
            "The same attachment was provided more than once.",
        )


def _resolve_document_path(
    path: str,
    *,
    owner: str,
    registry: UploadRegistry,
) -> UploadMetadata:
    """Resolve a document only through trusted owner-scoped metadata."""
    metadata = registry.get_by_path(path, owner=owner)
    if metadata is None:
        _raise(
            "attachment_not_found",
            "The attachment could not be verified.",
        )
    return metadata


def _resolve_dataset_path(
    path: str,
    *,
    owner: str,
    registry: UploadRegistry,
) -> tuple[UploadMetadata | None, bool]:
    """Resolve a user upload or retain an existing legacy dataset path."""
    metadata = registry.get_by_path(path, owner=owner)
    if metadata is not None:
        return metadata, False
    if is_managed_upload_path(path):
        _raise(
            "attachment_not_found",
            "The uploaded dataset could not be verified.",
        )
    if legacy_dataset_path_allowed(path):
        return None, True
    _raise(
        "attachment_not_found",
        "The dataset path is not available.",
    )


def _validate_dataset_description(description: Any) -> None:
    """Require a nonblank description for every dataset mapping entry."""
    if not isinstance(description, str) or not description.strip():
        _raise(
            "attachment_description_required",
            "Dataset descriptions must be nonblank.",
        )


def _validate_metadata(metadata: UploadMetadata, *, channel: str) -> None:
    """Validate trusted metadata against the selected attachment channel."""
    if (
        not isinstance(metadata.byte_size, int)
        or isinstance(metadata.byte_size, bool)
        or metadata.byte_size <= 0
    ):
        _raise(
            "attachment_not_found",
            "The attachment metadata could not be verified.",
        )
    if channel == "documents":
        if metadata.purpose not in _DOCUMENT_PURPOSES:
            _raise(
                "attachment_purpose_mismatch",
                "The attachment purpose does not match the document channel.",
            )
        _validate_extension(metadata, allowed=DOCUMENT_EXTENSIONS)
        return
    if metadata.purpose != _DATASET_PURPOSE:
        _raise(
            "attachment_purpose_mismatch",
            "The attachment purpose does not match the dataset channel.",
        )
    _validate_extension(metadata, allowed=("csv",))


def _validate_extension(
    metadata: UploadMetadata,
    *,
    allowed: Sequence[str],
) -> None:
    """Require the trusted format and filename suffix to agree."""
    format_name = metadata.format.lower().lstrip(".")
    suffix = PurePosixPath(metadata.filename).suffix.lower().lstrip(".")
    if format_name not in allowed or suffix != format_name:
        code = (
            "unsupported_asset_format"
            if metadata.file_id.startswith("file_")
            else "attachment_format_unsupported"
        )
        _raise(
            code,
            "The attachment format is not supported for this channel.",
        )


def _validate_budget(metadata: Sequence[UploadMetadata]) -> None:
    """Enforce inclusive per-file, count, and aggregate upload limits."""
    if len(metadata) > MAX_FILES:
        _raise(
            "attachment_limit_exceeded",
            "Uploaded attachments exceed the allowed limit.",
        )
    if any(item.byte_size > MAX_FILE_BYTES for item in metadata):
        _raise(
            "attachment_limit_exceeded",
            "Uploaded attachments exceed the allowed limit.",
        )
    if sum(item.byte_size for item in metadata) > MAX_TOTAL_BYTES:
        _raise(
            "attachment_limit_exceeded",
            "Uploaded attachments exceed the allowed limit.",
        )


def _normalized_key(path: str) -> str | None:
    """Normalize one path through the existing configured OBS boundary."""
    if not isinstance(path, str):
        return None
    try:
        key = normalize_obs_object_key(path, ServerConfig().BUCKET_NAME)
    except (ObsPathError, ValueError):
        return None
    return key or None


def _raise(code: str, message: str) -> NoReturn:
    """Raise one sanitized contract error without including caller paths."""
    raise AttachmentContractError(code, message)
