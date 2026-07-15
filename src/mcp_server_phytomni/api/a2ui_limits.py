# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Bounded parsing and response projection for direct HTTP A2UI actions."""

from __future__ import annotations

import json
from collections.abc import Mapping
from json import JSONDecodeError
from typing import Any

from pydantic import ValidationError
from starlette.requests import Request

from ..config.api_limits import ApiLimitsConfig
from .schemas import A2uiActionRequest

__all__ = [
    "A2uiPayloadError",
    "A2uiPayloadTooLargeError",
    "ensure_a2ui_response_size",
    "read_a2ui_action_request",
]


class A2uiPayloadError(ValueError):
    """Raised when an A2UI action cannot satisfy the public input contract."""


class A2uiPayloadTooLargeError(A2uiPayloadError):
    """Raised when an A2UI request or response exceeds its byte budget."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate object keys before Pydantic sees the payload."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise A2uiPayloadError("duplicate JSON key")
        result[key] = value
    return result


async def _read_bounded_body(request: Request, max_bytes: int) -> bytes:
    """Read an inbound body without buffering beyond the configured cap."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            advertised = int(content_length)
        except ValueError as exc:
            raise A2uiPayloadError("invalid content-length") from exc
        if advertised < 0:
            raise A2uiPayloadError("invalid content-length")
        if advertised > max_bytes:
            raise A2uiPayloadTooLargeError("a2ui request body too large")

    buffer = bytearray()
    async for chunk in request.stream():
        if len(buffer) + len(chunk) > max_bytes:
            raise A2uiPayloadTooLargeError("a2ui request body too large")
        buffer.extend(chunk)
    return bytes(buffer)


def _validate_scalar_strings(value: Any, *, max_chars: int) -> None:
    """Reject oversized JSON string values and object keys recursively."""
    if isinstance(value, str):
        if len(value) > max_chars:
            raise A2uiPayloadError("a2ui scalar string exceeds limit")
        return
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if isinstance(key, str) and len(key) > max_chars:
                raise A2uiPayloadError("a2ui object key exceeds limit")
            _validate_scalar_strings(nested, max_chars=max_chars)
        return
    if isinstance(value, list):
        for nested in value:
            _validate_scalar_strings(nested, max_chars=max_chars)


def _validate_identifier(
    payload: Mapping[str, Any],
    name: str,
    *,
    max_chars: int,
) -> None:
    """Require one public identifier to be nonblank, trimmed, and bounded."""
    value = payload.get(name)
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > max_chars
    ):
        raise A2uiPayloadError(f"invalid a2ui identifier: {name}")


def _validate_action_shape(
    payload: Mapping[str, Any],
    *,
    limits: ApiLimitsConfig,
) -> None:
    """Apply structural limits before invoking the shared Pydantic model."""
    for identifier in ("surface_id", "action_id", "run_id"):
        _validate_identifier(
            payload,
            identifier,
            max_chars=limits.A2UI_MAX_IDENTIFIER_RUNES,
        )
    _validate_scalar_strings(
        payload,
        max_chars=limits.A2UI_MAX_SCALAR_CHARS,
    )

    action_payload = payload.get("payload")
    if not isinstance(action_payload, Mapping):
        return
    fields = action_payload.get("fields")
    if (
        isinstance(fields, Mapping)
        and len(fields) > limits.A2UI_MAX_FORM_FIELDS
    ):
        raise A2uiPayloadError("a2ui form fields exceed limit")
    selected = action_payload.get("selected")
    if isinstance(selected, list) and len(selected) > limits.A2UI_MAX_CHOICES:
        raise A2uiPayloadError("a2ui choices exceed limit")


async def read_a2ui_action_request(request: Request) -> A2uiActionRequest:
    """Read and validate one bounded Web A2UI action envelope.

    The raw body is bounded and parsed with duplicate-key rejection before
    Pydantic validation. Errors deliberately expose only stable contract
    messages; the action body is never logged.
    """
    limits = ApiLimitsConfig()
    body = await _read_bounded_body(request, limits.A2UI_MAX_BODY_BYTES)
    try:
        decoded = json.loads(
            body.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (UnicodeDecodeError, JSONDecodeError) as exc:
        raise A2uiPayloadError("invalid a2ui json body") from exc
    if not isinstance(decoded, Mapping):
        raise A2uiPayloadError("a2ui action body must be an object")
    _validate_action_shape(decoded, limits=limits)
    try:
        return A2uiActionRequest.model_validate(decoded)
    except ValidationError as exc:
        raise A2uiPayloadError("invalid a2ui action envelope") from exc


def ensure_a2ui_response_size(
    payload: Mapping[str, Any], *, max_bytes: int
) -> None:
    """Reject an A2UI resume response whose compact JSON exceeds its cap."""
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise A2uiPayloadError("invalid a2ui response") from exc
    if len(encoded) > max_bytes:
        raise A2uiPayloadTooLargeError("a2ui response body too large")
