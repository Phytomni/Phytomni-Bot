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

from ..agents.research.input_inventory import (
    ManagedResearchAssetSnapshot,
    managed_research_assets_from_bundle,
)
from ..config.api_limits import ApiLimitsConfig
from ..config.defaults import ApiConfig, ServerConfig
from ..runtime.attachment_assets import ResolvedAsset, ResolvedAttachmentBundle
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
from .attachment_projection import ManagedAttachmentChannel
from .lifecycle_contract import SafeApiError

__all__ = [
    "AttachmentContractError",
    "AttachmentSelection",
    "ManagedAttachmentEvidence",
    "ManagedAttachmentEvidenceItem",
    "is_managed_upload_path",
    "normalize_asset_attachments",
    "legacy_dataset_path_allowed",
    "redact_managed_attachment_values",
    "redact_streaming_attachment_response",
    "validate_agent_attachments",
    "validate_native_attachments",
    "validate_research_attachment_bundle",
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
        "evidence",
        "attachment_evidence",
        "managed_evidence",
        "obs_file_list",
        "owner_subject",
    }
)
_REDACTED_ATTACHMENT = "<redacted-attachment>"
_REDACTED_VALUE = "<redacted>"


@dataclass(frozen=True, slots=True)
class AttachmentSelection:
    """Verified attachment references normalized for an agent invocation."""

    documents: tuple[UploadMetadata | ResolvedAsset, ...] = ()
    datasets: tuple[UploadMetadata | ResolvedAsset, ...] = ()
    legacy_dataset_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ManagedAttachmentEvidenceItem:
    """One ordered managed asset and its final native channel."""

    asset: ResolvedAsset
    projected_channel: ManagedAttachmentChannel


@dataclass(frozen=True, slots=True)
class ManagedAttachmentEvidence:
    """Private request-local provenance for owner-validated attachments."""

    attachment_owner: str
    items: tuple[ManagedAttachmentEvidenceItem, ...] = ()


class AttachmentContractError(ValueError):
    """A stable, sanitized attachment contract failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_research_attachment_bundle(
    bundle: ResolvedAttachmentBundle,
    config: ApiLimitsConfig,
) -> tuple[ManagedResearchAssetSnapshot, ...]:
    """Project trusted managed assets under Research-only count limits."""
    assets = bundle.all_assets
    if (
        len(assets) > config.API_MAX_ATTACHMENTS_PER_REQUEST
        or len(assets) > config.API_MAX_RESEARCH_INPUT_REFERENCES
    ):
        _raise(
            "attachment_limit_exceeded",
            "Uploaded attachments exceed the allowed limit.",
        )
    _validate_budget_sizes(
        tuple(asset.size_bytes for asset in bundle.documents)
    )
    return managed_research_assets_from_bundle(bundle)


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

    managed_items = _managed_evidence_by_reference(
        managed_evidence,
        owner=owner,
    )
    documents, document_sizes = _validate_document_paths(
        document_paths,
        owner=owner,
        registry=registry,
        managed_items=managed_items,
    )
    datasets, legacy_paths, dataset_sizes = _validate_dataset_items(
        dataset_items,
        owner=owner,
        registry=registry,
        managed_items=managed_items,
    )

    if managed_items:
        _raise(
            "attachment_not_found",
            "The attachment could not be verified.",
        )
    _validate_budget_sizes((*document_sizes, *dataset_sizes))
    return AttachmentSelection(
        documents=documents,
        datasets=datasets,
        legacy_dataset_paths=legacy_paths,
    )


def _validate_document_paths(
    paths: Sequence[str],
    *,
    owner: str,
    registry: UploadRegistry,
    managed_items: dict[str, ManagedAttachmentEvidenceItem],
) -> tuple[
    tuple[UploadMetadata | ResolvedAsset, ...],
    tuple[int, ...],
]:
    """Resolve document paths in order and collect verified sizes."""
    documents: list[UploadMetadata | ResolvedAsset] = []
    sizes: list[int] = []
    for path in paths:
        evidence_item = _consume_managed_evidence(
            managed_items,
            path=path,
            channel="obs_file_list",
        )
        if evidence_item is not None:
            _validate_managed_item(evidence_item)
            documents.append(evidence_item.asset)
            sizes.append(evidence_item.asset.size_bytes)
            continue
        metadata = _resolve_document_path(path, owner=owner, registry=registry)
        _validate_metadata(metadata, channel="documents")
        documents.append(metadata)
        sizes.append(metadata.byte_size)
    return tuple(documents), tuple(sizes)


def _validate_dataset_items(
    items: Sequence[tuple[str, Any]],
    *,
    owner: str,
    registry: UploadRegistry,
    managed_items: dict[str, ManagedAttachmentEvidenceItem],
) -> tuple[
    tuple[UploadMetadata | ResolvedAsset, ...],
    tuple[str, ...],
    tuple[int, ...],
]:
    """Resolve dataset items in order and retain approved legacy paths."""
    datasets: list[UploadMetadata | ResolvedAsset] = []
    legacy_paths: list[str] = []
    sizes: list[int] = []
    for path, description in items:
        evidence_item = _consume_managed_evidence(
            managed_items,
            path=path,
            channel="data_list",
        )
        if evidence_item is not None:
            _validate_managed_item(evidence_item)
            if description != "":
                _raise(
                    "attachment_description_required",
                    "Managed dataset values must be empty.",
                )
            datasets.append(evidence_item.asset)
            sizes.append(evidence_item.asset.size_bytes)
            continue
        dataset_metadata, is_legacy = _resolve_dataset_path(
            path,
            owner=owner,
            registry=registry,
        )
        _validate_dataset_description(description)
        if is_legacy:
            legacy_paths.append(path)
            continue
        if dataset_metadata is None:
            _raise(
                "attachment_not_found",
                "The uploaded dataset could not be verified.",
            )
        _validate_metadata(dataset_metadata, channel="datasets")
        datasets.append(dataset_metadata)
        sizes.append(dataset_metadata.byte_size)
    return tuple(datasets), tuple(legacy_paths), tuple(sizes)


def _managed_evidence_by_reference(
    evidence: ManagedAttachmentEvidence | None,
    *,
    owner: str,
) -> dict[str, ManagedAttachmentEvidenceItem]:
    """Return one exact, single-consumption map for private evidence."""
    if evidence is None:
        return {}
    if evidence.attachment_owner != owner:
        _raise(
            "attachment_not_found",
            "The attachment could not be verified.",
        )
    items: dict[str, ManagedAttachmentEvidenceItem] = {}
    asset_ids: set[str] = set()
    for item in evidence.items:
        reference = item.asset.reference
        if not isinstance(reference, str) or not reference:
            _raise(
                "attachment_not_found",
                "The attachment could not be verified.",
            )
        if reference in items or item.asset.asset_id in asset_ids:
            _raise(
                "attachment_duplicate",
                "The same attachment was provided more than once.",
            )
        items[reference] = item
        asset_ids.add(item.asset.asset_id)
    return items


def _consume_managed_evidence(
    items: dict[str, ManagedAttachmentEvidenceItem],
    *,
    path: str,
    channel: ManagedAttachmentChannel,
) -> ManagedAttachmentEvidenceItem | None:
    """Consume one exact managed reference in its declared native channel."""
    item = items.get(path)
    if item is None:
        return None
    if item.projected_channel != channel:
        _raise(
            "attachment_not_supported",
            "The selected agent does not support these attachments.",
        )
    del items[path]
    return item


def _validate_managed_item(item: ManagedAttachmentEvidenceItem) -> None:
    """Validate only trusted managed provenance and bounded size metadata."""
    size_bytes = item.asset.size_bytes
    if (
        not isinstance(size_bytes, int)
        or isinstance(size_bytes, bool)
        or size_bytes <= 0
    ):
        _raise(
            "attachment_not_found",
            "The attachment metadata could not be verified.",
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
                item.asset.reference
                for item in evidence.items
                if item.asset.reference
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


def _validate_budget_sizes(sizes: Sequence[int]) -> None:
    """Enforce inclusive limits across managed and registered uploads."""
    if len(sizes) > MAX_FILES:
        _raise(
            "attachment_limit_exceeded",
            "Uploaded attachments exceed the allowed limit.",
        )
    if any(size > MAX_FILE_BYTES for size in sizes):
        _raise(
            "attachment_limit_exceeded",
            "Uploaded attachments exceed the allowed limit.",
        )
    if sum(sizes) > MAX_TOTAL_BYTES:
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
