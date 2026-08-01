# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""HTTP attachment input normalization before agent dispatch."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from fastapi import HTTPException

from ..asset_resolver import AssetResolver, normalize_asset_attachments
from ..schemas import ChatCompletionRequest, ExpertQueryRequest

__all__ = [
    "normalize_chat_payload_attachments",
    "normalize_expert_payload_attachments",
    "normalize_payload_attachments",
    "validate_chat_attachment_capability",
]


def _attachment_payload_values(
    attachments: Sequence[Any],
) -> list[dict[str, Any]]:
    """Project typed attachment references into the resolver input shape."""
    return [
        item.model_dump() if hasattr(item, "model_dump") else dict(item)
        for item in attachments
    ]


def normalize_payload_attachments(
    arguments: Mapping[str, Any],
    attachments: Sequence[Any],
    *,
    owner: str,
    resolver: AssetResolver,
) -> dict[str, Any]:
    """Resolve Web asset IDs before any Agent or routing boundary."""
    if not attachments:
        return dict(arguments)
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
    resolver: AssetResolver,
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
    resolver: AssetResolver,
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
