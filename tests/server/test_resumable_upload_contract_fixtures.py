# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shape-lock tests for resumable-upload fixtures."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
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
from mcp_server_phytomni.runtime.resumable_uploads import (
    UPLOAD_PROTOCOL,
    UPLOAD_PROTOCOL_VERSION,
)

pytestmark = pytest.mark.server

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_ROOT = _REPO_ROOT / "docs" / "contracts" / "resumable-upload"
_WEB_COMPATIBILITY_PATH = _FIXTURE_ROOT / "resumable_upload_v2.json"

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
        "protocol": UPLOAD_PROTOCOL,
        "protocol_version": UPLOAD_PROTOCOL_VERSION,
        "renew": {
            "request": _project_shape("renew_request"),
            "response": _project_shape("renew_response"),
        },
    }


def _assert_strictly_sanitized(value: object) -> None:
    """Require recursive equality with the positive fixture projection."""
    assert value == _build_web_compatibility_fixture()


def _create_request_shape(fixture: dict[str, object]) -> dict[str, Any]:
    """Return the mutable create-request shape from a projected fixture."""
    create = fixture["create"]
    assert isinstance(create, dict)
    request = create["request"]
    assert isinstance(request, dict)
    return request


def test_manifest_pins_every_fixture_byte() -> None:
    """The vendor packet has an explicit set and digest for every golden."""
    manifest = _load("manifest.json")
    files = manifest["files"]

    assert manifest["protocol"] == UPLOAD_PROTOCOL
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


def test_web_compatibility_protocol_matches_runtime_authority() -> None:
    """Protocol identity and version come from the advertised runtime pair."""
    fixture = json.loads(_WEB_COMPATIBILITY_PATH.read_text(encoding="utf-8"))

    assert fixture["protocol"] == UPLOAD_PROTOCOL
    assert fixture["protocol_version"] == UPLOAD_PROTOCOL_VERSION


def test_web_compatibility_fixture_bytes_are_deterministic() -> None:
    """The vendorable fixture has canonical JSON and one trailing newline."""
    raw = _WEB_COMPATIBILITY_PATH.read_bytes()
    canonical = (
        json.dumps(
            _build_web_compatibility_fixture(),
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    expected_digest = _load("manifest.json")["files"][
        _WEB_COMPATIBILITY_PATH.name
    ]

    assert raw == canonical
    assert hashlib.sha256(canonical).hexdigest() == expected_digest


def test_web_compatibility_fixture_stays_strictly_sanitized() -> None:
    """No nested key or value exposes delegated or provider material."""
    fixture = json.loads(_WEB_COMPATIBILITY_PATH.read_text(encoding="utf-8"))

    _assert_strictly_sanitized(fixture)


@pytest.mark.parametrize(
    "unapproved_name",
    (
        "access_key_id",
        "account_id",
        "api_key",
        "authorization",
        "capability",
        "credential",
        "email",
        "filename",
        "owner_subject",
        "password",
        "payload",
        "private_key",
        "secret",
        "token",
        "upload_url",
    ),
)
def test_web_compatibility_sanitizer_rejects_nested_forbidden_material(
    unapproved_name: str,
) -> None:
    """The sanitizer rejects unapproved nested keys and field-name values."""
    key_projection = deepcopy(_build_web_compatibility_fixture())
    _create_request_shape(key_projection)[unapproved_name] = "string"
    with pytest.raises(AssertionError):
        _assert_strictly_sanitized(key_projection)

    value_projection = deepcopy(_build_web_compatibility_fixture())
    fields = _create_request_shape(value_projection)["fields"]
    assert isinstance(fields, list)
    first_field = fields[0]
    assert isinstance(first_field, dict)
    first_field["name"] = unapproved_name
    with pytest.raises(AssertionError):
        _assert_strictly_sanitized(value_projection)


def test_web_compatibility_rejects_unapproved_scalar_shapes() -> None:
    """Type/literal drift and misplaced allowed keys fail closed."""
    type_projection = deepcopy(_build_web_compatibility_fixture())
    fields = _create_request_shape(type_projection)["fields"]
    assert isinstance(fields, list)
    first_field = fields[0]
    assert isinstance(first_field, dict)
    first_field["type"] = "payload"

    protocol_projection = deepcopy(_build_web_compatibility_fixture())
    protocol_projection["protocol"] = "authorization"

    version_projection = deepcopy(_build_web_compatibility_fixture())
    version_projection["protocol_version"] = UPLOAD_PROTOCOL_VERSION + 1

    misplaced_projection = deepcopy(_build_web_compatibility_fixture())
    _create_request_shape(misplaced_projection)["protocol"] = UPLOAD_PROTOCOL

    for projection in (
        type_projection,
        protocol_projection,
        version_projection,
        misplaced_projection,
    ):
        with pytest.raises(AssertionError):
            _assert_strictly_sanitized(projection)


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
