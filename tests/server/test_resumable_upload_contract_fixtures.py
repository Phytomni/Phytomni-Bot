# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shape-lock tests for resumable-upload fixtures."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, get_origin

import pytest
from pydantic import BaseModel

from mcp_server_phytomni.api.agent_capabilities import (
    serialize_file_upload_capability,
)
from mcp_server_phytomni.api.routes.uploads import _upload_status_headers
from mcp_server_phytomni.api.schemas import (
    AssetDescriptor,
    AttachmentAsset,
    UploadCapabilityRenewRequest,
    UploadCapabilityResponse,
    UploadCompletionRequest,
    UploadCreateRequest,
    UploadCreateResponse,
    UploadPartResponse,
    UploadStatusResponse,
)

pytestmark = pytest.mark.server

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_ROOT = _REPO_ROOT / "docs" / "contracts" / "resumable-upload"
_WEB_COMPATIBILITY_PATH = _FIXTURE_ROOT / "resumable_upload_v2.json"
_PROTOCOL = "obs-multipart-v2"
_PROTOCOL_VERSION = 2

_EXPECTED_FILES = frozenset(
    {
        "capability.json",
        "create_request.json",
        "create_response.json",
        "renew_request.json",
        "renew_response.json",
        "head_response.json",
        "head_headers.json",
        "part_response.json",
        "complete_request.json",
        "complete_response.json",
        "abort_response.json",
        "resumable_upload_v2.json",
    }
)

_SHAPE_MODELS: dict[str, type[BaseModel]] = {
    "attachment": AttachmentAsset,
    "create_request": UploadCreateRequest,
    "create_response": UploadCreateResponse,
    "renew_request": UploadCapabilityRenewRequest,
    "renew_response": UploadCapabilityResponse,
}
_PROJECTED_FIELDS: dict[str, tuple[str, ...]] = {
    "attachment": ("asset_id",),
    "create_request": (
        "content_type",
        "idempotency_key",
        "last_modified_ms",
        "purpose",
        "size_bytes",
    ),
    "create_response": (
        "asset_id",
        "max_parallel_parts",
        "part_count",
        "part_size_bytes",
        "protocol",
        "session_expires_at",
        "status",
    ),
    "renew_request": (),
    "renew_response": (
        "asset_id",
        "protocol",
        "session_expires_at",
        "status",
    ),
}
_OMITTED_FIELDS: dict[str, frozenset[str]] = {
    "attachment": frozenset(),
    "create_request": frozenset({"filename", "owner_subject"}),
    "create_response": frozenset(
        {"capability", "capability_expires_at", "upload_url"}
    ),
    "renew_request": frozenset({"owner_subject"}),
    "renew_response": frozenset(
        {"capability", "capability_expires_at", "upload_url"}
    ),
}
_FORBIDDEN_KEY_FRAGMENTS = (
    "bucket",
    "capability",
    "credential",
    "filename",
    "huawei",
    "object_key",
    "owner",
    "path",
    "provider",
    "route",
    "secret",
    "token",
    "upload_id",
    "upload_url",
    "user",
)
_FORBIDDEN_VALUE_FRAGMENTS = _FORBIDDEN_KEY_FRAGMENTS + (
    "://",
    "bearer ",
)


def _load(name: str) -> Any:
    """Load one JSON fixture from the pinned contract directory."""
    return json.loads((_FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def _field_type(annotation: object) -> str:
    """Return the deliberately small public type vocabulary."""
    if get_origin(annotation) is Literal:
        return "literal"
    if annotation is str:
        return "string"
    if annotation is int:
        return "integer"
    if annotation is datetime:
        return "date-time"
    raise AssertionError(f"unsupported compatibility type: {annotation!r}")


def _project_shape(name: str) -> dict[str, object]:
    """Project one model to safe field-name/type/required metadata."""
    fields = _SHAPE_MODELS[name].model_fields
    return {
        "fields": [
            {
                "name": field_name,
                "required": fields[field_name].is_required(),
                "type": _field_type(fields[field_name].annotation),
            }
            for field_name in _PROJECTED_FIELDS[name]
        ]
    }


def _build_web_compatibility_fixture() -> dict[str, object]:
    """Build the strict Web compatibility projection from public models."""
    return {
        "attachment": _project_shape("attachment"),
        "create": {
            "request": _project_shape("create_request"),
            "response": _project_shape("create_response"),
        },
        "protocol": _PROTOCOL,
        "protocol_version": _PROTOCOL_VERSION,
        "renew": {
            "request": _project_shape("renew_request"),
            "response": _project_shape("renew_response"),
        },
    }


def _iter_json_entries(value: object) -> Iterator[tuple[str, str]]:
    """Yield nested JSON keys and string values for sanitization checks."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            assert isinstance(key, str)
            yield "key", key
            yield from _iter_json_entries(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_json_entries(child)
    elif isinstance(value, str):
        yield "value", value


def _assert_strictly_sanitized(value: object) -> None:
    """Reject forbidden structural keys and string material recursively."""
    for entry_kind, entry in _iter_json_entries(value):
        lowered = entry.casefold()
        forbidden = (
            _FORBIDDEN_KEY_FRAGMENTS
            if entry_kind == "key"
            else _FORBIDDEN_VALUE_FRAGMENTS
        )
        assert not any(fragment in lowered for fragment in forbidden)


def test_manifest_pins_every_fixture_byte() -> None:
    """The vendor packet has an explicit set and digest for every golden."""
    manifest = _load("manifest.json")
    files = manifest["files"]

    assert manifest["protocol"] == _PROTOCOL
    assert set(files) == _EXPECTED_FILES
    for name, expected_digest in files.items():
        path = _FIXTURE_ROOT / name
        assert path.is_file(), f"missing resumable-upload fixture: {path}"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_digest
        assert path.read_bytes().endswith(b"\n")


def test_web_compatibility_projection_accounts_for_every_model_field() -> None:
    """Account for every model field as published or omitted."""
    for shape, model in _SHAPE_MODELS.items():
        projected = frozenset(_PROJECTED_FIELDS[shape])
        omitted = _OMITTED_FIELDS[shape]

        assert projected.isdisjoint(omitted)
        assert projected | omitted == frozenset(model.model_fields)


def test_web_compatibility_fixture_matches_sanitized_projection() -> None:
    """The published fixture is derived from current public Pydantic models."""
    fixture = json.loads(_WEB_COMPATIBILITY_PATH.read_text(encoding="utf-8"))

    assert fixture == _build_web_compatibility_fixture()
    assert fixture["protocol"] == _PROTOCOL
    assert fixture["protocol_version"] == _PROTOCOL_VERSION


def test_web_compatibility_fixture_bytes_are_deterministic() -> None:
    """The vendorable fixture has canonical JSON and one trailing newline."""
    raw = _WEB_COMPATIBILITY_PATH.read_bytes()
    fixture = json.loads(raw)
    canonical = (
        json.dumps(fixture, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")

    assert raw == canonical
    assert len(hashlib.sha256(raw).hexdigest()) == 64


def test_web_compatibility_fixture_stays_strictly_sanitized() -> None:
    """No nested key or value exposes delegated or provider material."""
    fixture = json.loads(_WEB_COMPATIBILITY_PATH.read_text(encoding="utf-8"))

    _assert_strictly_sanitized(fixture)


@pytest.mark.parametrize(
    "forbidden_projection",
    (
        {"nested": {"capability": "string"}},
        {"nested": [{"name": "filename"}]},
        {"nested": [{"name": "owner_subject"}]},
        {"nested": [{"name": "upload_url"}]},
        {"nested": ["https://"]},
    ),
)
def test_web_compatibility_sanitizer_rejects_nested_forbidden_material(
    forbidden_projection: object,
) -> None:
    """The recursive sanitizer rejects both forbidden keys and values."""
    with pytest.raises(AssertionError):
        _assert_strictly_sanitized(forbidden_projection)


def test_capability_fixture_matches_bot_descriptor() -> None:
    """The vendored capability is the sanitized Bot descriptor."""
    assert _load("capability.json") == serialize_file_upload_capability()


def test_control_plane_fixtures_match_public_models() -> None:
    """Create and renew request/response shapes stay provider-free."""
    create_request = UploadCreateRequest.model_validate(
        _load("create_request.json")
    )
    create_response = UploadCreateResponse.model_validate(
        _load("create_response.json")
    )
    renew_request = UploadCapabilityRenewRequest.model_validate(
        _load("renew_request.json")
    )
    renew_response = UploadCapabilityResponse.model_validate(
        _load("renew_response.json")
    )

    assert create_request.owner_subject == renew_request.owner_subject
    assert create_response.asset_id == renew_response.asset_id
    assert create_response.protocol == renew_response.protocol
    assert create_response.capability == "fixture-capability"
    assert renew_response.capability == "fixture-capability-renewed"


def test_data_plane_fixtures_match_public_models_and_headers() -> None:
    """HEAD, part, completion, and abort goldens match their wire shapes."""
    status = UploadStatusResponse.model_validate(_load("head_response.json"))
    part = UploadPartResponse.model_validate(_load("part_response.json"))
    completion = UploadCompletionRequest.model_validate(
        _load("complete_request.json")
    )
    descriptor = AssetDescriptor.model_validate(
        _load("complete_response.json")
    )
    aborted = UploadStatusResponse.model_validate(_load("abort_response.json"))

    assert _load("head_headers.json") == _upload_status_headers(status)
    assert part.asset_id == status.asset_id
    assert part.received_parts == status.received_parts
    assert (
        completion.sha256
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    assert descriptor.asset_id == status.asset_id
    assert descriptor.size_bytes == status.size_bytes
    assert aborted.status == "aborted"


def test_fixtures_do_not_expose_provider_coordinates() -> None:
    """Synthetic goldens contain no OBS coordinates or local paths."""
    text = "\n".join(
        (_FIXTURE_ROOT / name).read_text(encoding="utf-8")
        for name in _EXPECTED_FILES
    )
    for forbidden in (
        "object_key",
        "obs_upload_id",
        "upload_id",
        "bucket",
        "/home/",
        "Bearer ",
    ):
        assert forbidden not in text
