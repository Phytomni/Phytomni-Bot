# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Boundary tests for direct HTTP A2UI action parsing."""

from __future__ import annotations

import json

import pytest
from starlette.requests import Request

from mcp_server_phytomni.api.a2ui_limits import (
    A2uiPayloadError,
    A2uiPayloadTooLargeError,
    ensure_a2ui_response_size,
    read_a2ui_action_request,
)

pytestmark = pytest.mark.server


def _streaming_request(
    body: bytes,
    *,
    content_length: str | None = None,
) -> Request:
    """Build a request whose body is delivered in one receive event."""
    delivered = False
    headers: list[tuple[bytes, bytes]] = []
    if content_length is not None:
        headers.append((b"content-length", content_length.encode()))

    async def receive() -> dict[str, object]:
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/runs/run/a2ui-actions",
        "headers": headers,
        "query_string": b"",
    }
    return Request(scope, receive=receive)


def _confirm_body(**overrides: object) -> bytes:
    """Serialize one valid confirm action with optional root overrides."""
    payload: dict[str, object] = {
        "surface_id": "surface-1",
        "widget": "confirm",
        "action_id": "action-1",
        "run_id": "run-1",
        "payload": {"accepted": True},
    }
    payload.update(overrides)
    return json.dumps(payload, separators=(",", ":")).encode()


async def test_read_a2ui_action_request_accepts_valid_confirm() -> None:
    """A valid action reaches the existing Pydantic contract unchanged."""
    request = _streaming_request(_confirm_body())

    result = await read_a2ui_action_request(request)

    assert result.widget == "confirm"
    assert result.payload == {"accepted": True}


@pytest.mark.parametrize(
    "body",
    [
        _confirm_body(surface_id=""),
        _confirm_body(surface_id=" surface-1"),
        _confirm_body(action_id="action-1 "),
        _confirm_body(run_id="\trun-1"),
    ],
)
async def test_read_a2ui_action_request_rejects_invalid_identifiers(
    body: bytes,
) -> None:
    """Identifiers must be nonblank, trimmed, and bounded strings."""
    with pytest.raises(A2uiPayloadError):
        await read_a2ui_action_request(_streaming_request(body))


async def test_read_a2ui_action_request_rejects_advertised_overflow() -> None:
    """An advertised body over the direct-A2UI cap is rejected early."""
    with pytest.raises(A2uiPayloadTooLargeError):
        await read_a2ui_action_request(
            _streaming_request(_confirm_body(), content_length=str(65_537))
        )


async def test_read_a2ui_action_request_rejects_actual_overflow() -> None:
    """A chunked body over the cap is rejected before Pydantic validation."""
    body = _confirm_body(
        widget="form",
        payload={
            "fields": {f"field-{index}": "x" * 4_096 for index in range(16)}
        },
    )

    with pytest.raises(A2uiPayloadTooLargeError):
        await read_a2ui_action_request(_streaming_request(body))


async def test_read_a2ui_action_request_rejects_duplicate_keys() -> None:
    """Duplicate JSON keys cannot bypass action validation."""
    body = (
        b'{"surface_id":"surface-1","surface_id":"surface-2",'
        b'"widget":"confirm","action_id":"action-1",'
        b'"run_id":"run-1","payload":{"accepted":true}}'
    )

    with pytest.raises(A2uiPayloadError, match="duplicate"):
        await read_a2ui_action_request(_streaming_request(body))


async def test_read_a2ui_action_request_rejects_trailing_json() -> None:
    """A second JSON value after the envelope is rejected."""
    body = _confirm_body() + b"{}"

    with pytest.raises(A2uiPayloadError, match="json"):
        await read_a2ui_action_request(_streaming_request(body))


async def test_read_a2ui_action_request_rejects_form_field_overflow() -> None:
    """A form action cannot carry more than twenty fields."""
    body = _confirm_body(
        widget="form",
        payload={"fields": {f"field-{index}": index for index in range(21)}},
    )

    with pytest.raises(A2uiPayloadError, match="form fields"):
        await read_a2ui_action_request(_streaming_request(body))


async def test_read_a2ui_action_request_rejects_scalar_overflow() -> None:
    """Nested scalar strings are bounded before graph entry."""
    body = _confirm_body(payload={"accepted": "x" * 4_097})

    with pytest.raises(A2uiPayloadError, match="string"):
        await read_a2ui_action_request(_streaming_request(body))


async def test_read_a2ui_action_request_rejects_choice_overflow() -> None:
    """A choice action cannot select more than one hundred options."""
    body = _confirm_body(
        widget="choice",
        payload={"selected": [f"choice-{index}" for index in range(101)]},
    )

    with pytest.raises(A2uiPayloadError, match="choices"):
        await read_a2ui_action_request(_streaming_request(body))


def test_ensure_a2ui_response_size_rejects_large_payload() -> None:
    """A2UI resume responses have a one-megabyte serialized cap."""
    with pytest.raises(A2uiPayloadTooLargeError):
        ensure_a2ui_response_size(
            {"answer": "x" * (1_048_576 + 1)},
            max_bytes=1_048_576,
        )
