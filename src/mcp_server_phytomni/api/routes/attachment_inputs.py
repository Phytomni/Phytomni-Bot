# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""HTTP attachment input normalization before agent dispatch."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from ...runtime.attachment_assets import ResolvedAttachmentBundle
from ...runtime.locale import current_effective_locale
from ..agent_capabilities import (
    ExpertAttachmentRequirement,
    agent_uses_user_query,
    filter_tools_for_expert_attachments,
    get_agent_slug_for_tool,
    get_attachment_capability,
)
from ..asset_resolver import AssetResolver, normalize_asset_attachments
from ..attachment_projection import (
    AttachmentProjectionError,
    ManagedAttachmentChannel,
    ManagedAttachmentProjection,
    project_managed_attachments,
)
from ..attachments import (
    ManagedAttachmentEvidence,
    ManagedAttachmentEvidenceItem,
    validate_native_attachments,
)
from ..auth import ApiPrincipal
from ..lifecycle_contract import SafeApiError
from ..schemas import ChatCompletionRequest, ExpertQueryRequest

__all__ = [
    "PreparedAttachmentContext",
    "ResolvedAttachmentInput",
    "attachment_not_supported_error",
    "expert_attachment_channels",
    "expert_attachment_requirement",
    "filter_expert_attachment_candidates",
    "normalize_chat_payload_attachments",
    "normalize_expert_payload_attachments",
    "normalize_payload_attachments",
    "prepare_chat_attachments",
    "prepare_native_attachment_arguments",
    "prepare_selected_expert_arguments",
    "resolve_attachment_input",
    "resolve_attachment_owner",
]


@dataclass(frozen=True, slots=True)
class ResolvedAttachmentInput:
    """Owner-qualified opaque attachment bundle for one HTTP request."""

    attachment_owner: str
    bundle: ResolvedAttachmentBundle


@dataclass(frozen=True, slots=True)
class PreparedAttachmentContext:
    """Private request-local managed attachment evidence."""

    evidence: ManagedAttachmentEvidence | None


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
            bundle=ResolvedAttachmentBundle(assets=()),
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


def prepare_native_attachment_arguments(
    *,
    agent: str,
    arguments: Mapping[str, Any],
    resolved_input: ResolvedAttachmentInput,
    db_path: str,
) -> tuple[dict[str, Any], PreparedAttachmentContext]:
    """Project resolved native attachments and private evidence once."""
    capability = get_attachment_capability(agent)
    try:
        projection = project_managed_attachments(
            resolved_input.bundle,
            capability,
        )
    except AttachmentProjectionError as exc:
        raise attachment_not_supported_error() from exc
    prepared = _project_attachment_arguments(
        arguments,
        projection,
        capability_has_documents=capability.document_context is not None,
        capability_has_datasets=capability.datasets is not None,
    )
    evidence = _managed_attachment_evidence(resolved_input, projection)
    validate_native_attachments(
        agent,
        prepared,
        owner=resolved_input.attachment_owner,
        db_path=db_path,
        managed_evidence=evidence,
    )
    return prepared, PreparedAttachmentContext(
        evidence=evidence,
    )


def _project_attachment_arguments(
    arguments: Mapping[str, Any],
    projection: ManagedAttachmentProjection,
    *,
    capability_has_documents: bool,
    capability_has_datasets: bool,
) -> dict[str, Any]:
    """Append projected managed references to copied native arguments."""
    prepared = dict(arguments)
    obs_file_list = [
        *_document_argument_values(prepared.get("obs_file_list")),
        *(asset.reference for asset in projection.obs_assets),
    ]
    if obs_file_list or capability_has_documents:
        prepared["obs_file_list"] = obs_file_list
    data_list = _dataset_argument_values(prepared.get("data_list"))
    for asset in projection.data_assets:
        data_list[asset.reference] = ""
    if data_list or capability_has_datasets:
        prepared["data_list"] = data_list
    return prepared


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
    projection: ManagedAttachmentProjection,
) -> ManagedAttachmentEvidence | None:
    """Build private evidence for managed assets when any exist."""
    bundle = resolved_input.bundle
    if not bundle.assets:
        return None
    channel_by_asset_id: dict[str, ManagedAttachmentChannel] = {
        **{asset.asset_id: "obs_file_list" for asset in projection.obs_assets},
        **{asset.asset_id: "data_list" for asset in projection.data_assets},
    }
    return ManagedAttachmentEvidence(
        attachment_owner=resolved_input.attachment_owner,
        items=tuple(
            ManagedAttachmentEvidenceItem(
                asset=asset,
                projected_channel=channel_by_asset_id[asset.asset_id],
            )
            for asset in bundle.assets
        ),
    )


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


def expert_attachment_channels(
    resolved_input: ResolvedAttachmentInput,
    obs_file_list: Sequence[str],
) -> frozenset[str]:
    """Return only legacy channels needed by the context boundary."""
    del resolved_input
    channels: set[str] = set()
    if any(str(item).strip() for item in obs_file_list):
        channels.add("documents")
    return frozenset(channels)


def expert_attachment_requirement(
    resolved_input: ResolvedAttachmentInput,
    obs_file_list: Sequence[str],
) -> ExpertAttachmentRequirement:
    """Summarize managed and legacy Expert attachment constraints."""
    return ExpertAttachmentRequirement(
        managed_assets=bool(resolved_input.bundle.assets),
        legacy_documents=any(str(item).strip() for item in obs_file_list),
    )


def filter_expert_attachment_candidates(
    payload: ExpertQueryRequest,
    requirement: ExpertAttachmentRequirement,
) -> ExpertQueryRequest:
    """Filter ordered Expert tools by attachment authorization facts."""
    if not (requirement.managed_assets or requirement.legacy_documents):
        return payload
    filtered = filter_tools_for_expert_attachments(
        allowed_tools=payload.allowed_tools,
        requirement=requirement,
    )
    if not filtered or (
        payload.forced_tool is not None and payload.forced_tool not in filtered
    ):
        raise attachment_not_supported_error()
    return payload.model_copy(update={"allowed_tools": list(filtered)})


def prepare_selected_expert_arguments(
    *,
    agent: str,
    selected_arguments: Mapping[str, Any],
    payload: ExpertQueryRequest,
    resolved_input: ResolvedAttachmentInput,
    db_path: str,
) -> tuple[dict[str, Any], PreparedAttachmentContext]:
    """Discard selector path maps and prepare managed Expert arguments."""
    requirement = expert_attachment_requirement(
        resolved_input,
        payload.obs_file_list,
    )
    allowed_tools = filter_tools_for_expert_attachments(
        allowed_tools=payload.allowed_tools,
        requirement=requirement,
    )
    if not any(
        get_agent_slug_for_tool(tool) == agent for tool in allowed_tools
    ):
        raise attachment_not_supported_error()
    arguments = dict(selected_arguments)
    arguments.pop("obs_file_list", None)
    arguments.pop("data_list", None)
    arguments.pop("attachments", None)
    if agent == "analyst":
        arguments["goal_description"] = payload.user_query
        arguments.pop("user_query", None)
    elif agent_uses_user_query(agent):
        arguments["user_query"] = payload.user_query
        arguments.pop("goal_description", None)
    arguments["locale"] = current_effective_locale()
    if payload.obs_file_list:
        arguments["obs_file_list"] = list(payload.obs_file_list)
    capability = get_attachment_capability(agent)
    if capability.document_context is not None:
        arguments.setdefault("obs_file_list", [])
    if capability.datasets is not None:
        arguments.setdefault("data_list", {})
    return prepare_native_attachment_arguments(
        agent=agent,
        arguments=arguments,
        resolved_input=resolved_input,
        db_path=db_path,
    )


def prepare_chat_attachments(
    *,
    tool_name: str,
    arguments: Mapping[str, Any],
    resolved_input: ResolvedAttachmentInput,
    db_path: str,
) -> tuple[dict[str, Any], PreparedAttachmentContext]:
    """Project managed Chat-route assets through the selected capability."""
    slug = get_agent_slug_for_tool(tool_name) or "chat"
    return prepare_native_attachment_arguments(
        agent=slug,
        arguments=arguments,
        resolved_input=resolved_input,
        db_path=db_path,
    )


def attachment_not_supported_error() -> SafeApiError:
    """Return the stable public attachment-authorization failure."""
    return SafeApiError(
        status_code=422,
        code="attachment_not_supported",
        message="The selected agent does not accept these attachments.",
        stage="attachment_validation",
        retryable=False,
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
