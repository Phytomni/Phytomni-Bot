# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shape-lock tests for the unified managed-attachment contract."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from mcp_server_phytomni.api.agent_capabilities import (
    AttachmentCapability,
    DatasetCapability,
    DocumentContextCapability,
    ExpertAttachmentRequirement,
    filter_tools_for_expert_attachments,
)
from mcp_server_phytomni.api.attachment_projection import (
    AttachmentProjectionError,
    project_managed_attachments,
)
from mcp_server_phytomni.runtime.attachment_assets import (
    ResolvedAsset,
    ResolvedAttachmentBundle,
)

pytestmark = pytest.mark.server

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_ROOT = _REPO_ROOT / "docs" / "contracts" / "unified-attachments"
_REQUEST_PATH = _FIXTURE_ROOT / "request.json"
_EXPECTED_PATH = _FIXTURE_ROOT / "expected-channel-projection.json"
_MANIFEST_PATH = _FIXTURE_ROOT / "manifest.json"
_PROTOCOL = "phytomni-unified-attachments-v1"
_VERSION = 1
_QUERY = "Process the attached synthetic research assets."
_SCENARIO_IDS = (
    "dual_pdf_fastq_archive",
    "document_only_mixed_classes",
    "dataset_only_mixed_classes",
    "zero_channel_rejected",
    "expert_authorized_capability_intersection",
)
_EXPERT_ALLOWED_TOOLS = (
    "DataAgent",
    "DigitalDesignAgent",
    "AnalystAgent",
    "BriefGeneAgent",
    "ChatAgent",
)
_MANIFEST_FILES = ("request.json", "expected-channel-projection.json")
_PROVING_TEST = (
    "tests/server/test_unified_attachment_contract_fixtures.py::"
    "test_unified_attachment_contract_matches_real_projector"
)
_FORBIDDEN_JSON_KEYS = frozenset(
    {
        "authorization",
        "capability",
        "token",
        "secret",
        "password",
        "owner_subject",
        "bucket",
        "object_key",
        "upload_id",
        "dataset_description",
        "purpose",
    }
)
_FORBIDDEN_MARKERS = (
    "bearer ",
    "obs://",
    "http://",
    "https://",
    "/home/",
    "agent_data/uploads",
)

CapabilityShape = Literal["dual", "document_only", "dataset_only", "zero"]
ServerClass = Literal["dataset", "document"]


class RequestAttachment(BaseModel):
    """One sanitized request attachment reference."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str


class RequestScenario(BaseModel):
    """One request-side unified attachment scenario."""

    model_config = ConfigDict(extra="forbid")

    id: str
    query: str
    attachments: list[RequestAttachment]


class RequestFixture(BaseModel):
    """Strict top-level request fixture schema."""

    model_config = ConfigDict(extra="forbid")

    protocol: str
    version: int
    scenarios: list[RequestScenario]


class ServerAsset(BaseModel):
    """One server-side synthetic asset classification."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str
    server_class: ServerClass


class ExpectedScenario(BaseModel):
    """One expected managed-channel projection scenario."""

    model_config = ConfigDict(extra="forbid")

    id: str
    server_assets: list[ServerAsset]
    capability_shape: CapabilityShape
    obs_asset_ids: list[str]
    data_asset_ids: list[str]
    error_code: str | None
    eligible_tools: list[str]


class ExpectedFixture(BaseModel):
    """Strict top-level expected projection fixture schema."""

    model_config = ConfigDict(extra="forbid")

    protocol: str
    version: int
    scenarios: list[ExpectedScenario]


class ManifestFile(BaseModel):
    """One digest-pinned payload entry in the fixture manifest."""

    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str


class FixtureManifest(BaseModel):
    """Strict manifest schema for the two payload fixtures."""

    model_config = ConfigDict(extra="forbid")

    protocol: str
    version: int
    scenario_ids: list[str]
    files: list[ManifestFile]
    proving_test: str


def _load_json(path: Path) -> Any:
    """Load one JSON fixture without changing its raw bytes."""
    return json.loads(path.read_text(encoding="utf-8"))


def _load_request() -> RequestFixture:
    """Load and validate the request fixture."""
    return RequestFixture.model_validate(_load_json(_REQUEST_PATH))


def _load_expected() -> ExpectedFixture:
    """Load and validate the expected projection fixture."""
    return ExpectedFixture.model_validate(_load_json(_EXPECTED_PATH))


def _load_manifest() -> FixtureManifest:
    """Load and validate the fixture manifest."""
    return FixtureManifest.model_validate(_load_json(_MANIFEST_PATH))


def _capability_for_shape(shape: CapabilityShape) -> AttachmentCapability:
    """Build the real capability descriptor named by one fixture row."""
    return {
        "dual": AttachmentCapability(
            document_context=DocumentContextCapability(),
            datasets=DatasetCapability(),
        ),
        "document_only": AttachmentCapability(
            document_context=DocumentContextCapability(),
        ),
        "dataset_only": AttachmentCapability(datasets=DatasetCapability()),
        "zero": AttachmentCapability(),
    }[shape]


def _resolved_bundle(scenario: ExpectedScenario) -> ResolvedAttachmentBundle:
    """Convert classified rows to typed assets with memory-only references."""
    return ResolvedAttachmentBundle(
        assets=tuple(
            ResolvedAsset(
                asset_id=asset.asset_id,
                reference=f"memory-sentinel-{asset.asset_id}",
                filename=f"fixture-{asset.asset_id}.bin",
                content_type="application/octet-stream",
                size_bytes=1,
                purpose=asset.server_class,
            )
            for asset in scenario.server_assets
        )
    )


def _iter_json_keys(value: object) -> Iterator[str]:
    """Yield every JSON object key, including nested fixture keys."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            assert isinstance(key, str)
            yield key
            yield from _iter_json_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_json_keys(child)


def test_unified_attachment_fixture_models_are_strict() -> None:
    """Request and expected models reject keys outside their contracts."""
    with pytest.raises(ValidationError):
        RequestFixture.model_validate(
            {
                "protocol": _PROTOCOL,
                "version": _VERSION,
                "scenarios": [],
                "extra": 1,
            }
        )
    with pytest.raises(ValidationError):
        ExpectedFixture.model_validate(
            {
                "protocol": _PROTOCOL,
                "version": _VERSION,
                "scenarios": [],
                "extra": 1,
            }
        )


def test_unified_attachment_contract_matches_real_projector() -> None:
    """Expected channels are produced by the real projector and filter."""
    request = _load_request()
    expected = _load_expected()

    assert request.protocol == expected.protocol == _PROTOCOL
    assert request.version == expected.version == _VERSION
    assert [scenario.id for scenario in request.scenarios] == list(
        _SCENARIO_IDS
    )
    assert [scenario.id for scenario in expected.scenarios] == list(
        _SCENARIO_IDS
    )

    for request_scenario, expected_scenario in zip(
        request.scenarios, expected.scenarios, strict=True
    ):
        assert request_scenario.id == expected_scenario.id
        assert request_scenario.query == _QUERY
        assert [item.asset_id for item in request_scenario.attachments] == [
            item.asset_id for item in expected_scenario.server_assets
        ]

        try:
            projected = project_managed_attachments(
                _resolved_bundle(expected_scenario),
                _capability_for_shape(expected_scenario.capability_shape),
            )
        except AttachmentProjectionError as error:
            assert expected_scenario.error_code == error.code
            assert expected_scenario.obs_asset_ids == []
            assert expected_scenario.data_asset_ids == []
        else:
            assert expected_scenario.error_code is None
            assert [asset.asset_id for asset in projected.obs_assets] == (
                expected_scenario.obs_asset_ids
            )
            assert [asset.asset_id for asset in projected.data_assets] == (
                expected_scenario.data_asset_ids
            )

        eligible_tools: tuple[str, ...] = ()
        if expected_scenario.id == "expert_authorized_capability_intersection":
            eligible_tools = filter_tools_for_expert_attachments(
                allowed_tools=_EXPERT_ALLOWED_TOOLS,
                requirement=ExpertAttachmentRequirement(managed_assets=True),
            )
        assert list(eligible_tools) == expected_scenario.eligible_tools


def test_unified_attachment_fixture_bytes_are_deterministic() -> None:
    """All published JSON uses sorted keys and exactly one trailing newline."""
    for path in (_REQUEST_PATH, _EXPECTED_PATH, _MANIFEST_PATH):
        raw = path.read_text(encoding="utf-8")
        canonical = (
            json.dumps(
                json.loads(raw), ensure_ascii=False, indent=2, sort_keys=True
            )
            + "\n"
        )
        assert raw == canonical


def test_unified_attachment_manifest_pins_only_payload_bytes() -> None:
    """Manifest shape and lowercase digests pin the two payload files."""
    manifest = _load_manifest()

    assert manifest.protocol == _PROTOCOL
    assert manifest.version == _VERSION
    assert manifest.scenario_ids == list(_SCENARIO_IDS)
    assert [entry.path for entry in manifest.files] == list(_MANIFEST_FILES)
    assert manifest.proving_test == _PROVING_TEST

    payload_paths = {
        "request.json": _REQUEST_PATH,
        "expected-channel-projection.json": _EXPECTED_PATH,
    }
    for entry in manifest.files:
        assert len(entry.sha256) == 64
        assert entry.sha256 == entry.sha256.lower()
        assert all(
            character in "0123456789abcdef" for character in entry.sha256
        )
        digest = hashlib.sha256(
            payload_paths[entry.path].read_bytes()
        ).hexdigest()
        assert digest == entry.sha256


def test_unified_attachment_fixtures_stay_sanitized() -> None:
    """Published fixtures contain no credential or deployment material."""
    for path in (_REQUEST_PATH, _EXPECTED_PATH, _MANIFEST_PATH):
        raw = path.read_text(encoding="utf-8")
        lowered = raw.casefold()
        for marker in _FORBIDDEN_MARKERS:
            assert marker.casefold() not in lowered
        for key in _iter_json_keys(json.loads(raw)):
            assert key.casefold() not in _FORBIDDEN_JSON_KEYS
