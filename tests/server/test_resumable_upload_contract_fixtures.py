# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shape-lock tests for resumable-upload and agent-attachment fixtures."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from tests.support.resumable_asset_fakes import (
    ResumableAssetSpec,
    build_resumable_asset,
    patch_dataset_description_completion,
)

from mcp_server_phytomni.agents.shared.dataset_description import (
    DatasetDescriptionResult,
)
from mcp_server_phytomni.api.agent_capabilities import (
    serialize_file_upload_capability,
)
from mcp_server_phytomni.api.attachments import (
    redact_managed_attachment_values,
)
from mcp_server_phytomni.api.routes.attachment_inputs import (
    prepare_native_attachment_arguments,
    resolve_attachment_input,
)
from mcp_server_phytomni.api.routes.uploads import _upload_status_headers
from mcp_server_phytomni.api.schemas import (
    AgentRunRequest,
    AssetDescriptor,
    UploadCapabilityRenewRequest,
    UploadCapabilityResponse,
    UploadCompletionRequest,
    UploadCreateRequest,
    UploadCreateResponse,
    UploadPartResponse,
    UploadStatusResponse,
)
from mcp_server_phytomni.runtime import (
    resumable_uploads as resumable_uploads_mod,
)

pytestmark = pytest.mark.server

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_ROOT = _REPO_ROOT / "docs" / "contracts" / "resumable-upload"
_AGENT_ATTACHMENT_FIXTURE_ROOT = (
    _REPO_ROOT / "docs" / "contracts" / "agent-attachments"
)

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
_AGENT_ATTACHMENT_FILES = frozenset({"native_mixed_request.json"})
_FIXTURE_DATASET_ID = "file_11111111111111111111111111111111"
_FIXTURE_DOCUMENT_ID = "file_22222222222222222222222222222222"
_FIXTURE_OWNER = "fixture-delegated-owner"
_FIXTURE_DESCRIPTION = "Synthetic CSV count matrix"
_FORBIDDEN_ATTACHMENT_MARKERS = (
    "object_key",
    "upload_id",
    "capability",
    "Bearer",
    "/home/",
    "/obs/",
    "http://",
    "https://",
    "ptm_",
    "AT1G",
    ">gene",
    "FASTA",
)


def _load(name: str) -> Any:
    """Load one JSON fixture from the pinned contract directory."""
    return json.loads((_FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def _load_agent_attachment(name: str) -> Any:
    """Load one JSON fixture from the agent-attachment contract directory."""
    return json.loads(
        (_AGENT_ATTACHMENT_FIXTURE_ROOT / name).read_text(encoding="utf-8")
    )


def _pin_fixture_asset_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin create-time asset ids to 32 ones, then 32 twos.

    ``create`` emits ``token_hex(16)`` before the storage session's
    ``token_hex(8)``; part leases emit another ``token_hex(16)`` after that
    eight-byte draw. Consume the pinned values only on create-time draws.
    """
    pinned = iter(("1" * 32, "2" * 32))
    previous_nbytes: int | None = None
    original = resumable_uploads_mod.secrets.token_hex

    def _token_hex(nbytes: int) -> str:
        nonlocal previous_nbytes
        prior = previous_nbytes
        previous_nbytes = nbytes
        if nbytes == 16 and prior != 8:
            try:
                return next(pinned)
            except StopIteration:
                pass
        return original(nbytes)

    monkeypatch.setattr(resumable_uploads_mod.secrets, "token_hex", _token_hex)


def _build_fixture_mixed_assets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Any, str]:
    """Complete the pinned dataset and document under one shared registry."""
    _pin_fixture_asset_ids(monkeypatch)
    db_path = str(tmp_path / "fixture-attachments.sqlite")
    specs = (
        ResumableAssetSpec(
            owner=_FIXTURE_OWNER,
            filename="synthetic-counts.csv",
            content=b"column,count\nsynthetic,1\n",
            purpose="dataset",
        ),
        ResumableAssetSpec(
            owner=_FIXTURE_OWNER,
            filename="synthetic-protocol.pdf",
            content=b"%PDF-1.4\nsynthetic-protocol\n",
            purpose="document",
        ),
    )
    assets = [
        build_resumable_asset(tmp_path, db_path=db_path, spec=spec)
        for spec in specs
    ]
    assert [asset.asset_id for asset in assets] == [
        _FIXTURE_DATASET_ID,
        _FIXTURE_DOCUMENT_ID,
    ]
    return assets[0].resolver, db_path


async def _fail_dataset_provider(**_kwargs: Any) -> DatasetDescriptionResult:
    """Raise if user-supplied descriptions incorrectly call the provider."""
    raise AssertionError("supplied description must skip the provider")


def _assert_fixture_references_are_private(
    prepared_arguments: dict[str, Any],
    evidence: Any,
) -> None:
    """Require managed references stay out of fixture bytes and redaction."""
    dataset_reference = next(iter(prepared_arguments["data_list"]))
    document_reference = prepared_arguments["obs_file_list"][0]
    assert dataset_reference != document_reference
    fixture_bytes = (
        _AGENT_ATTACHMENT_FIXTURE_ROOT / "native_mixed_request.json"
    ).read_bytes()
    assert dataset_reference.encode("utf-8") not in fixture_bytes
    assert document_reference.encode("utf-8") not in fixture_bytes
    debug_projection = {
        "answer": f"used {dataset_reference} and {document_reference}",
        "arguments": prepared_arguments,
        "owner_subject": _FIXTURE_OWNER,
        "dataset_description": _FIXTURE_DESCRIPTION,
    }
    redacted = redact_managed_attachment_values(debug_projection, evidence)
    dumped = json.dumps(redacted, sort_keys=True)
    assert dataset_reference not in dumped
    assert document_reference not in dumped
    assert "attachments" not in redacted
    assert "data_list" not in redacted
    assert "obs_file_list" not in redacted
    assert "owner_subject" not in redacted
    assert "dataset_description" not in redacted


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


def test_agent_attachment_manifest_pins_mixed_request_bytes() -> None:
    """The paired agent-attachment packet pins one mixed native request."""
    manifest = _load_agent_attachment("manifest.json")
    files = manifest["files"]

    assert manifest["protocol"] == "phytomni-agent-attachments-v1"
    assert set(files) == _AGENT_ATTACHMENT_FILES
    for name, expected_digest in files.items():
        path = _AGENT_ATTACHMENT_FIXTURE_ROOT / name
        assert path.is_file(), f"missing agent-attachment fixture: {path}"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_digest
        assert path.read_bytes().endswith(b"\n")


def test_agent_attachment_request_matches_public_schema() -> None:
    """Mixed request parses as AgentRunRequest with exact fixture values."""
    payload = AgentRunRequest.model_validate(
        _load_agent_attachment("native_mixed_request.json")
    )

    assert payload.owner_subject == _FIXTURE_OWNER
    assert payload.dataset_description == _FIXTURE_DESCRIPTION
    assert payload.arguments["goal_description"] == (
        "Compare synthetic expression groups"
    )
    assert payload.arguments["data_list"] == {}
    assert payload.arguments["obs_file_list"] == []
    assert [item.asset_id for item in payload.attachments] == [
        _FIXTURE_DATASET_ID,
        _FIXTURE_DOCUMENT_ID,
    ]


def test_agent_attachment_fixtures_stay_provider_free() -> None:
    """Fixture and README text stay free of provider material."""
    paths = [
        _AGENT_ATTACHMENT_FIXTURE_ROOT / "native_mixed_request.json",
        _AGENT_ATTACHMENT_FIXTURE_ROOT / "manifest.json",
        _AGENT_ATTACHMENT_FIXTURE_ROOT / "README.md",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    for forbidden in _FORBIDDEN_ATTACHMENT_MARKERS:
        assert forbidden not in text, f"forbidden marker present: {forbidden}"


async def test_agent_attachment_fixture_resolves_to_analyst_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pinned asset ids project to one Analyst dataset and one document."""
    resolver, db_path = _build_fixture_mixed_assets(monkeypatch, tmp_path)
    payload = AgentRunRequest.model_validate(
        _load_agent_attachment("native_mixed_request.json")
    )
    resolved = resolve_attachment_input(
        payload.attachments,
        attachment_owner=_FIXTURE_OWNER,
        resolver=resolver,
    )
    patch_dataset_description_completion(monkeypatch, _fail_dataset_provider)
    prepared_arguments, context = await prepare_native_attachment_arguments(
        agent="analyst",
        arguments=payload.arguments,
        resolved_input=resolved,
        dataset_description=payload.dataset_description,
        db_path=db_path,
    )

    assert list(prepared_arguments["data_list"].values()) == [
        _FIXTURE_DESCRIPTION
    ]
    assert len(prepared_arguments["data_list"]) == 1
    assert len(prepared_arguments["obs_file_list"]) == 1
    assert context.description_source == "user"
    assert context.evidence is not None
    assert context.evidence.attachment_owner == _FIXTURE_OWNER
    _assert_fixture_references_are_private(
        prepared_arguments, context.evidence
    )
