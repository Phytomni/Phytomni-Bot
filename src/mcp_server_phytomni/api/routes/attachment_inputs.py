# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""HTTP attachment input normalization before agent dispatch."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from ...agents.shared.dataset_description import (
    DatasetDescriptionResult,
    complete_dataset_descriptions,
)
from ...runtime.attachment_assets import ResolvedAttachmentBundle
from ..asset_resolver import AssetResolver, normalize_asset_attachments
from ..attachments import (
    ManagedAttachmentEvidence,
    validate_native_attachments,
)
from ..auth import ApiPrincipal
from ..schemas import ChatCompletionRequest, ExpertQueryRequest

__all__ = [
    "PreparedAttachmentContext",
    "ResolvedAttachmentInput",
    "normalize_chat_payload_attachments",
    "normalize_expert_payload_attachments",
    "normalize_payload_attachments",
    "prepare_native_attachment_arguments",
    "resolve_attachment_input",
    "resolve_attachment_owner",
    "validate_chat_attachment_capability",
]


@dataclass(frozen=True, slots=True)
class ResolvedAttachmentInput:
    """Owner-qualified opaque attachment bundle for one HTTP request."""

    attachment_owner: str
    bundle: ResolvedAttachmentBundle


@dataclass(frozen=True, slots=True)
class PreparedAttachmentContext:
    """Private request-local attachment evidence and description source."""

    evidence: ManagedAttachmentEvidence | None
    description_source: str | None


@dataclass(frozen=True, slots=True)
class _PreparedProjection:
    """Copied arguments plus ordered managed reference projections."""

    arguments: dict[str, Any]
    dataset_references: list[str]


def _attachment_payload_values(
    attachments: Sequence[Any],
) -> list[dict[str, Any]]:
    """Project typed attachment references into the resolver input shape."""
    return [
        item.model_dump() if hasattr(item, "model_dump") else dict(item)
        for item in attachments
    ]


def resolve_attachment_owner(
    principal: ApiPrincipal,
    owner_subject: str | None,
) -> str:
    """Resolve the asserted attachment owner for one request."""
    if owner_subject is None:
        return principal.user_id
    if "files:delegate" not in principal.scopes:
        raise HTTPException(status_code=403, detail="insufficient scope")
    return owner_subject


def resolve_attachment_input(
    attachments: Sequence[Any],
    *,
    attachment_owner: str,
    resolver: AssetResolver | Callable[[], AssetResolver],
) -> ResolvedAttachmentInput:
    """Resolve opaque assets into purpose partitions without projection."""
    if not attachments:
        return ResolvedAttachmentInput(
            attachment_owner=attachment_owner,
            bundle=ResolvedAttachmentBundle(),
        )
    if callable(resolver):
        resolver = resolver()
    bundle = resolver.resolve_bundle(
        _attachment_payload_values(attachments),
        attachment_owner,
    )
    return ResolvedAttachmentInput(
        attachment_owner=attachment_owner,
        bundle=bundle,
    )


async def prepare_native_attachment_arguments(
    *,
    agent: str,
    arguments: Mapping[str, Any],
    resolved_input: ResolvedAttachmentInput,
    dataset_description: str | None,
    db_path: str,
) -> tuple[dict[str, Any], PreparedAttachmentContext]:
    """Project resolved native attachments and private evidence once."""
    bundle = resolved_input.bundle
    projection = _project_attachment_arguments(arguments, bundle)
    evidence = _managed_attachment_evidence(resolved_input)
    validate_owner = (
        evidence.attachment_owner
        if evidence is not None
        else resolved_input.attachment_owner
    )
    validate_native_attachments(
        agent,
        projection.arguments,
        owner=validate_owner,
        db_path=db_path,
        managed_evidence=evidence,
    )
    description_source: str | None = None
    if bundle.datasets:
        if dataset_description and dataset_description.strip():
            _apply_dataset_descriptions(
                projection.arguments,
                projection.dataset_references,
                DatasetDescriptionResult(
                    (dataset_description,)
                    * len(projection.dataset_references),
                    "user",
                ),
            )
            description_source = "user"
        else:
            query = _canonical_dataset_query(agent, projection.arguments)
            completion = await complete_dataset_descriptions(
                query=query,
                datasets=bundle.datasets,
                supplied_description=dataset_description,
            )
            _apply_dataset_descriptions(
                projection.arguments,
                projection.dataset_references,
                completion,
            )
            description_source = completion.source
        validate_native_attachments(
            agent,
            projection.arguments,
            owner=validate_owner,
            db_path=db_path,
            managed_evidence=evidence,
        )
    return projection.arguments, PreparedAttachmentContext(
        evidence=evidence,
        description_source=description_source,
    )


def _project_attachment_arguments(
    arguments: Mapping[str, Any],
    bundle: ResolvedAttachmentBundle,
) -> _PreparedProjection:
    """Append managed attachment references to a copied argument map."""
    prepared = dict(arguments)
    existing_documents = _document_argument_values(
        prepared.get("obs_file_list")
    )
    existing_datasets = _dataset_argument_values(prepared.get("data_list"))
    document_references = [asset.reference for asset in bundle.documents]
    dataset_references = [asset.reference for asset in bundle.datasets]
    if existing_documents or document_references:
        prepared["obs_file_list"] = [*existing_documents, *document_references]
    if existing_datasets or dataset_references:
        data_list = dict(existing_datasets)
        for reference in dataset_references:
            data_list[reference] = ""
        prepared["data_list"] = data_list
    return _PreparedProjection(prepared, dataset_references)


def _document_argument_values(value: Any) -> list[Any]:
    """Return caller document entries while preserving validation behavior."""
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)):
        return [value]
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def _dataset_argument_values(value: Any) -> dict[Any, Any]:
    """Return caller dataset entries while preserving validation behavior."""
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    return {value: ""}


def _managed_attachment_evidence(
    resolved_input: ResolvedAttachmentInput,
) -> ManagedAttachmentEvidence | None:
    """Build private evidence for managed assets when any exist."""
    bundle = resolved_input.bundle
    if not bundle.documents and not bundle.datasets:
        return None
    return ManagedAttachmentEvidence(
        attachment_owner=resolved_input.attachment_owner,
        document_references=frozenset(
            asset.reference for asset in bundle.documents
        ),
        dataset_references=frozenset(
            asset.reference for asset in bundle.datasets
        ),
    )


def _canonical_dataset_query(agent: str, arguments: Mapping[str, Any]) -> str:
    """Return the native query used for managed dataset completion."""
    key = "goal_description" if agent == "analyst" else "user_query"
    value = arguments.get(key)
    if isinstance(value, str) and value.strip():
        return value
    raise HTTPException(status_code=422, detail="dataset query is required")


def _apply_dataset_descriptions(
    arguments: dict[str, Any],
    references: Sequence[str],
    completion: DatasetDescriptionResult,
) -> None:
    """Replace only managed dataset placeholder descriptions."""
    data_list = dict(arguments.get("data_list") or {})
    for reference, description in zip(
        references, completion.descriptions, strict=True
    ):
        data_list[reference] = description
    arguments["data_list"] = data_list


def normalize_payload_attachments(
    arguments: Mapping[str, Any],
    attachments: Sequence[Any],
    *,
    owner: str,
    resolver: AssetResolver | Callable[[], AssetResolver],
) -> dict[str, Any]:
    """Resolve Web asset IDs before any Agent or routing boundary."""
    if not attachments:
        return dict(arguments)
    if callable(resolver):
        resolver = resolver()
    return normalize_asset_attachments(
        {**arguments, "attachments": _attachment_payload_values(attachments)},
        owner=owner,
        resolver=resolver,
    )


def validate_chat_attachment_capability(
    payload: ChatCompletionRequest,
    tool_name: str,
    tool_accepts_obs: Callable[[str], bool],
) -> None:
    """Reject asset attachments before resolving unsupported tools."""
    if payload.attachments and not tool_accepts_obs(tool_name):
        raise HTTPException(
            status_code=400,
            detail=f"model {payload.model} does not accept attachments",
        )


def normalize_chat_payload_attachments(
    payload: ChatCompletionRequest,
    *,
    owner: str,
    resolver: AssetResolver | Callable[[], AssetResolver],
) -> ChatCompletionRequest:
    """Keep chat's public body while replacing asset IDs internally."""
    if not payload.attachments:
        return payload
    arguments = normalize_payload_attachments(
        {"obs_file_list": payload.obs_file_list or []},
        payload.attachments,
        owner=owner,
        resolver=resolver,
    )
    return payload.model_copy(
        update={
            "obs_file_list": arguments.get("obs_file_list", []),
            "attachments": [],
        }
    )


def normalize_expert_payload_attachments(
    payload: ExpertQueryRequest,
    *,
    owner: str,
    resolver: AssetResolver | Callable[[], AssetResolver],
) -> ExpertQueryRequest:
    """Normalize Expert asset IDs without changing its tool allowlist."""
    if not payload.attachments:
        return payload
    arguments = normalize_payload_attachments(
        {"obs_file_list": payload.obs_file_list},
        payload.attachments,
        owner=owner,
        resolver=resolver,
    )
    return payload.model_copy(
        update={
            "obs_file_list": arguments.get("obs_file_list", []),
            "attachments": [],
        }
    )
