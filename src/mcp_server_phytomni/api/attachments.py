# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Validate attachment references at the authenticated API boundary."""

from __future__ import annotations

from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    Sequence,
)
from dataclasses import dataclass, fields, is_dataclass
from pathlib import PurePosixPath
from typing import Any, NoReturn, cast

from fastapi.responses import Response, StreamingResponse

from ..config.defaults import ApiConfig, ServerConfig
from ..runtime.upload_registry import UploadMetadata, UploadRegistry
from ..storage.obs_storage import ObsPathError, normalize_obs_object_key
from .agent_capabilities import (
    DOCUMENT_EXTENSIONS,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    get_attachment_capability,
)
from .asset_resolver import normalize_asset_attachments
from .lifecycle_contract import SafeApiError

__all__ = [
    "AttachmentContractError",
    "AttachmentSelection",
    "ManagedAttachmentEvidence",
    "is_managed_upload_path",
    "normalize_asset_attachments",
    "legacy_dataset_path_allowed",
    "redact_managed_attachment_values",
    "redact_streaming_attachment_response",
    "validate_agent_attachments",
    "validate_native_attachments",
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
_PRIVATE_ATTACHMENT_KEYS = frozenset(
    {
        "attachments",
        "data_list",
        "obs_file_list",
        "owner_subject",
    }
)
_REDACTED_ATTACHMENT = "<redacted-attachment>"
_REDACTED_VALUE = "<redacted>"


@dataclass(frozen=True, slots=True)
class AttachmentSelection:
    """Verified attachment references normalized for an agent invocation."""

    documents: tuple[UploadMetadata, ...] = ()
    datasets: tuple[UploadMetadata, ...] = ()
    legacy_dataset_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ManagedAttachmentEvidence:
    """Private request-local provenance for owner-validated attachments."""

    attachment_owner: str
    document_references: frozenset[str] = frozenset()
    dataset_references: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class _ResolvedDataset:
    """One resolved dataset entry retained through description validation."""

    path: str
    metadata: UploadMetadata | None
    is_legacy: bool
    description: Any


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
    managed_evidence: ManagedAttachmentEvidence | None = None,
) -> None:
    """Validate one API invocation and project failures into HTTP state."""
    try:
        validate_agent_attachments(
            agent,
            arguments,
            owner=owner,
            registry=UploadRegistry(db_path),
            managed_evidence=managed_evidence,
        )
    except AttachmentContractError as exc:
        raise SafeApiError(
            status_code=422,
            code=exc.code,
            message=str(exc),
            stage="attachment_validation",
            retryable=False,
        ) from exc


def validate_agent_attachments(
    agent: str,
    arguments: Mapping[str, Any],
    *,
    owner: str,
    registry: UploadRegistry,
    managed_evidence: ManagedAttachmentEvidence | None = None,
) -> AttachmentSelection:
    """Validate all declared attachment references for one native agent."""
    document_paths = _document_paths(arguments)
    dataset_items = _dataset_items(arguments)
    _reject_duplicate_paths(
        (*document_paths, *(path for path, _ in dataset_items))
    )

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
    resolved_datasets = _resolve_datasets(
        dataset_items,
        owner=owner,
        registry=registry,
    )
    datasets = tuple(
        item.metadata
        for item in resolved_datasets
        if item.metadata is not None and not item.is_legacy
    )
    legacy_paths = tuple(
        item.path for item in resolved_datasets if item.is_legacy
    )
    for metadata in documents:
        _validate_metadata(metadata, channel="documents")
    for metadata in datasets:
        _validate_metadata(metadata, channel="datasets")
    evidence = (
        managed_evidence
        if managed_evidence is not None
        and managed_evidence.attachment_owner == owner
        else None
    )
    for item in resolved_datasets:
        _validate_dataset_description(
            item.description,
            blank_allowed=_managed_dataset_description_may_be_empty(
                item,
                owner=owner,
                evidence=evidence,
            ),
        )
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
) -> tuple[_ResolvedDataset, ...]:
    """Resolve user and legacy datasets while preserving descriptions."""
    resolved: list[_ResolvedDataset] = []
    for path, description in dataset_items:
        metadata, is_legacy = _resolve_dataset_path(
            path,
            owner=owner,
            registry=registry,
        )
        resolved.append(
            _ResolvedDataset(
                path=path,
                metadata=metadata,
                is_legacy=is_legacy,
                description=description,
            )
        )
    return tuple(resolved)


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


def _managed_dataset_description_may_be_empty(
    item: _ResolvedDataset,
    *,
    owner: str,
    evidence: ManagedAttachmentEvidence | None,
) -> bool:
    """Allow a blank description only for exact current managed evidence."""
    metadata = item.metadata
    return (
        evidence is not None
        and not item.is_legacy
        and metadata is not None
        and metadata.user_id == owner
        and metadata.purpose == _DATASET_PURPOSE
        and item.path in evidence.dataset_references
    )


def _validate_dataset_description(
    description: Any,
    *,
    blank_allowed: bool = False,
) -> None:
    """Require a nonblank description for every dataset mapping entry."""
    if isinstance(description, str) and (description.strip() or blank_allowed):
        return
    _raise(
        "attachment_description_required",
        "Dataset descriptions must be nonblank.",
    )


def redact_managed_attachment_values(
    value: Any,
    evidence: ManagedAttachmentEvidence,
) -> Any:
    """Project managed attachment evidence into a safe recursive value."""
    return _redact_managed_attachment_value(
        value,
        _managed_reference_tuple(evidence),
    )


def redact_streaming_attachment_response(
    response: Response,
    evidence: ManagedAttachmentEvidence,
) -> Response:
    """Redact exact managed references across streamed response chunks."""
    if not isinstance(response, StreamingResponse):
        return response
    references = _managed_reference_tuple(evidence)
    if not references:
        return response
    source = cast(AsyncIterator[Any], response.body_iterator)
    return StreamingResponse(
        _redact_attachment_stream_chunks(source, references),
        status_code=response.status_code,
        headers=dict(response.headers),
        media_type=response.media_type,
        background=response.background,
    )


def _managed_reference_tuple(
    evidence: ManagedAttachmentEvidence,
) -> tuple[str, ...]:
    """Return managed references longest-first for exact replacement."""
    return tuple(
        sorted(
            (
                reference
                for reference in (
                    *evidence.document_references,
                    *evidence.dataset_references,
                )
                if reference
            ),
            key=len,
            reverse=True,
        )
    )


async def _redact_attachment_stream_chunks(
    source: AsyncIterator[Any],
    references: tuple[str, ...],
) -> AsyncIterator[Any]:
    """Replace exact references while preserving SSE chunk boundaries."""
    max_hold = max(len(reference) for reference in references) - 1
    pending = ""
    emit_bytes: bool | None = None
    try:
        async for chunk in source:
            if emit_bytes is None:
                emit_bytes = isinstance(chunk, (bytes, bytearray))
            text = (
                bytes(chunk).decode("utf-8", errors="surrogateescape")
                if isinstance(chunk, (bytes, bytearray))
                else str(chunk)
            )
            pending = _replace_managed_references(pending + text, references)
            if max_hold <= 0:
                emit, pending = pending, ""
            elif len(pending) > max_hold:
                emit, pending = pending[:-max_hold], pending[-max_hold:]
            else:
                continue
            yield emit.encode("utf-8") if emit_bytes else emit
        if pending:
            pending = _replace_managed_references(pending, references)
            yield pending.encode("utf-8") if emit_bytes else pending
    finally:
        aclose = getattr(source, "aclose", None)
        if callable(aclose):
            await cast(Callable[[], Awaitable[Any]], aclose)()


def _replace_managed_references(
    value: str,
    references: tuple[str, ...],
) -> str:
    """Replace every exact managed reference with the fixed marker."""
    for reference in references:
        value = value.replace(reference, _REDACTED_ATTACHMENT)
    return value


def _redact_managed_attachment_value(
    value: Any,
    references: tuple[str, ...],
) -> Any:
    """Recursively project one value without calling untrusted ``repr``."""
    if isinstance(value, str):
        for reference in references:
            value = value.replace(reference, _REDACTED_ATTACHMENT)
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return {
            _redact_managed_attachment_value(
                key, references
            ): _redact_managed_attachment_value(
                item,
                references,
            )
            for key, item in value.items()
            if not isinstance(key, str)
            or key.lower() not in _PRIVATE_ATTACHMENT_KEYS
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _redact_managed_attachment_value(
                getattr(value, field.name),
                references,
            )
            for field in fields(value)
            if field.name.lower() not in _PRIVATE_ATTACHMENT_KEYS
        }
    if isinstance(value, (tuple, list)):
        items = [
            _redact_managed_attachment_value(item, references)
            for item in value
        ]
        return tuple(items) if isinstance(value, tuple) else items
    return _REDACTED_VALUE


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
