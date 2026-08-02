# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shape-lock tests for resumable-upload contract fixtures."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from mcp_server_phytomni.api.agent_capabilities import (
    serialize_file_upload_capability,
)
from mcp_server_phytomni.api.routes.uploads import _upload_status_headers
from mcp_server_phytomni.api.schemas import (
    AssetDescriptor,
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
    }
)


def _load(name: str) -> Any:
    """Load one JSON fixture from the pinned contract directory."""
    return json.loads((_FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def test_manifest_pins_every_fixture_byte() -> None:
    """The vendor packet has an explicit set and digest for every golden."""
    manifest = _load("manifest.json")
    files = manifest["files"]

    assert manifest["protocol"] == "obs-multipart-v2"
    assert set(files) == _EXPECTED_FILES
    for name, expected_digest in files.items():
        path = _FIXTURE_ROOT / name
        assert path.is_file(), f"missing resumable-upload fixture: {path}"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_digest
        assert path.read_bytes().endswith(b"\n")


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
