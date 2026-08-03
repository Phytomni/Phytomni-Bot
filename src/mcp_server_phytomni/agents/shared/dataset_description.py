# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Bounded, metadata-only dataset-description completion for agents."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ...common.prompts import get_prompt
from ...common.responses import message_content, parse_json_object_fragment
from ...config.defaults import ChatConfig
from ...config.settings import get_sensitive_config
from ...runtime.attachment_assets import ResolvedAsset
from ..chat.service import phyto_chat
from .options import build_resolver_chat_kwargs
from .query_resolution import ResolverFailure, invoke_resolver

__all__ = [
    "DatasetDescriptionResult",
    "DatasetDescriptionSource",
    "complete_dataset_descriptions",
]

_LOGGER = logging.getLogger(__name__)
_MAX_DATASETS = 10
_MAX_DESCRIPTION_SCALARS = 4_000
_RESOLVER_TIMEOUT_SECONDS = 15.0
_SYSTEM_PROMPT_PATH = "system/dataset_description_completion"
_USER_PROMPT_PATH = "user/dataset_description_completion"

DatasetDescriptionSource = Literal["user", "generated", "empty"]

_DATASET_DESCRIPTION_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "DatasetDescriptionCompletion",
        "schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "maxItems": _MAX_DATASETS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["label", "description"],
                    },
                },
            },
            "required": ["items"],
        },
    },
}


@dataclass(frozen=True, slots=True)
class DatasetDescriptionResult:
    """Ordered completion values for one resolved managed-dataset group."""

    descriptions: tuple[str, ...]
    source: DatasetDescriptionSource


async def complete_dataset_descriptions(
    *,
    query: str,
    datasets: Sequence[ResolvedAsset],
    supplied_description: str | None,
) -> DatasetDescriptionResult:
    """Complete missing managed-dataset descriptions with one safe call.

    User-supplied nonblank text takes precedence. Otherwise the provider sees
    only the query plus ten-or-fewer display-metadata rows; provider and
    parser failures are nonterminal, while task cancellation propagates.

    Args:
        query: Canonical user query associated with the managed datasets.
        datasets: Purpose-validated dataset assets in request order.
        supplied_description: Optional description supplied by the caller.

    Returns:
        One description per input dataset in its original order.
    """
    count = len(datasets)
    if not count:
        return _empty_result(count)
    if count > _MAX_DATASETS:
        _log_completion_event("invalid_count", "input", count)
        return _empty_result(count)
    if supplied_description and supplied_description.strip():
        return DatasetDescriptionResult(
            descriptions=(supplied_description,) * count,
            source="user",
        )

    return await _complete_generated_descriptions(query, datasets)


async def _complete_generated_descriptions(
    query: str,
    datasets: Sequence[ResolvedAsset],
) -> DatasetDescriptionResult:
    """Run and normalize the one bounded provider completion call."""
    count = len(datasets)

    labels = tuple(f"dataset_{index}" for index in range(1, count + 1))
    prompt = get_prompt(
        ChatConfig().PROMPT_FILE,
        _USER_PROMPT_PATH,
        {
            "query": query,
            "datasets_json": json.dumps(
                _prompt_dataset_rows(datasets),
                ensure_ascii=True,
                separators=(",", ":"),
            ),
        },
    )
    provider_result = await _capture_nonterminal(
        invoke_resolver(
            lambda: phyto_chat(user_query=prompt, **_provider_kwargs()),
            _RESOLVER_TIMEOUT_SECONDS,
        )
    )
    if isinstance(provider_result, ResolverFailure):
        code = (
            "timeout"
            if str(provider_result).startswith("resolver timeout after")
            else "provider_failure"
        )
        _log_completion_event(code, "resolver", count)
        return _empty_result(count)
    if isinstance(provider_result, Exception):
        _log_completion_event("provider_failure", "provider", count)
        return _empty_result(count)

    parsed_result = await _capture_nonterminal(
        _parse_provider_response(provider_result, labels)
    )
    if isinstance(parsed_result, Exception):
        _log_completion_event("malformed_output", "parser", count)
        return _empty_result(count)
    descriptions, outcome = parsed_result
    if outcome != "ok":
        _log_completion_event(outcome, "parser", count)
    source: DatasetDescriptionSource = (
        "generated" if any(descriptions) else "empty"
    )
    return DatasetDescriptionResult(descriptions=descriptions, source=source)


def _prompt_dataset_rows(
    datasets: Sequence[ResolvedAsset],
) -> list[dict[str, str | int]]:
    """Project only display-safe bounded metadata into ordered prompt rows."""
    return [
        {
            "label": f"dataset_{index}",
            "name": asset.filename,
            "size_bytes": asset.size_bytes,
            "format": Path(asset.filename).suffix.lower().lstrip("."),
            "mime_type": asset.content_type,
        }
        for index, asset in enumerate(datasets, start=1)
    ]


def _provider_kwargs() -> dict[str, Any]:
    """Build one fixed resolver request from the shared option seam."""
    config = ChatConfig()
    kwargs = build_resolver_chat_kwargs(
        _SYSTEM_PROMPT_PATH,
        _DATASET_DESCRIPTION_SCHEMA,
        config,
        get_sensitive_config(),
    )
    kwargs.update(
        stream=False,
        n=1,
        max_retries=0,
        timeout=_RESOLVER_TIMEOUT_SECONDS,
    )
    return kwargs


async def _capture_nonterminal[CompletionValue](
    awaitable: Awaitable[CompletionValue],
) -> CompletionValue | Exception:
    """Capture ordinary completion failures while preserving cancellation."""
    result = (await asyncio.gather(awaitable, return_exceptions=True))[0]
    if isinstance(result, asyncio.CancelledError):
        raise result
    if isinstance(result, Exception):
        return result
    if isinstance(result, BaseException):
        raise result
    return result


async def _parse_provider_response(
    response: Mapping[str, Any],
    labels: tuple[str, ...],
) -> tuple[tuple[str, ...], Literal["ok", "empty_output", "malformed_output"]]:
    """Adapt synchronous parsing to the shared nonterminal boundary."""
    return _parse_items(response, labels)


def _parse_items(
    response: Mapping[str, Any],
    labels: tuple[str, ...],
) -> tuple[tuple[str, ...], Literal["ok", "empty_output", "malformed_output"]]:
    """Normalize one structured response without trusting its schema claim."""
    content = message_content(response)
    if not content:
        return ("",) * len(labels), "empty_output"
    payload = parse_json_object_fragment(content)
    if not payload:
        return ("",) * len(labels), "malformed_output"
    items = payload.get("items")
    if not isinstance(items, list):
        return ("",) * len(labels), "malformed_output"
    if len(items) > _MAX_DATASETS:
        return ("",) * len(labels), "malformed_output"

    label_counts = {label: 0 for label in labels}
    known_items: list[Mapping[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        label = item.get("label")
        if isinstance(label, str) and label in label_counts:
            label_counts[label] += 1
            known_items.append(item)

    values = {label: "" for label in labels}
    for item in known_items:
        item_label = item.get("label")
        if not isinstance(item_label, str) or label_counts[item_label] != 1:
            continue
        description = item.get("description")
        if isinstance(description, str) and _valid_description(description):
            values[item_label] = description
    descriptions = tuple(values[label] for label in labels)
    return descriptions, "ok"


def _valid_description(value: Any) -> bool:
    """Check the exact semantic limits retained after provider validation."""
    return (
        isinstance(value, str)
        and bool(value.strip())
        and "\x00" not in value
        and len(value) <= _MAX_DESCRIPTION_SCALARS
    )


def _empty_result(count: int) -> DatasetDescriptionResult:
    """Return the exact empty outcome aligned to the caller's input count."""
    return DatasetDescriptionResult(descriptions=("",) * count, source="empty")


def _log_completion_event(code: str, source: str, count: int) -> None:
    """Record only stable diagnostics that contain no caller/provider data."""
    _LOGGER.warning(
        "dataset_description_completion code=%s source=%s count=%d",
        code,
        source,
        count,
    )
