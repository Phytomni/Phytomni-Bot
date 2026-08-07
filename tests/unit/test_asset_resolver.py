# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Owner isolation and byte-faithful delivery tests for upload assets."""

from __future__ import annotations

import gzip
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest
from tests.support.attachment_fakes import (
    ManagedAttachmentEvidenceSpec,
    managed_attachment_evidence_item,
)
from tests.support.resumable_asset_fakes import (
    ResumableAssetHarness,
    ResumableAssetSpec,
    build_resumable_asset,
)

from mcp_server_phytomni.api import asset_resolver as asset_resolver_module
from mcp_server_phytomni.api.asset_resolver import (
    AssetResolver,
    normalize_asset_attachments,
)
from mcp_server_phytomni.api.attachments import (
    AttachmentContractError,
    ManagedAttachmentEvidence,
    redact_managed_attachment_values,
    validate_agent_attachments,
)
from mcp_server_phytomni.api.resumable_uploads import UploadContractError
from mcp_server_phytomni.api.routes.attachment_inputs import (
    ResolvedAttachmentInput,
    prepare_native_attachment_arguments,
)
from mcp_server_phytomni.runtime.upload_registry import (
    UploadMetadata,
    UploadRegistry,
)

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class AttachmentProjectionFixture:
    """Nested test fixture for private attachment projection coverage."""

    reference: str
    attachments: str
    nested: tuple[object, ...]


def _build_completed_asset(
    tmp_path: Path,
    *,
    filename: str,
    content: bytes,
) -> tuple[AssetResolver, str, str, bytes]:
    """Create one completed fake asset and its owner-scoped resolver."""
    harness = build_resumable_asset(
        tmp_path,
        spec=ResumableAssetSpec(filename=filename, content=content),
    )
    return (
        harness.resolver,
        harness.asset_id,
        harness.owner,
        harness.content,
    )


def _build_purpose_assets(
    tmp_path: Path,
) -> tuple[dict[str, ResumableAssetHarness], str]:
    """Create completed fixture assets sharing one upload registry."""
    db_path = str(tmp_path / "uploads.sqlite")
    specs = {
        "document": ResumableAssetSpec(
            filename="document-a.pdf",
            content=b"document-a",
            purpose="document",
        ),
        "legacy": ResumableAssetSpec(
            filename="legacy-b.pdf",
            content=b"legacy-b",
            purpose="chat_attachment",
        ),
        "dataset_a": ResumableAssetSpec(
            filename="dataset-a.csv",
            content=b"dataset-a",
            purpose="dataset",
        ),
        "dataset_b": ResumableAssetSpec(
            filename="dataset-b.csv",
            content=b"dataset-b",
            purpose="dataset",
        ),
    }
    return {
        name: build_resumable_asset(tmp_path, db_path=db_path, spec=spec)
        for name, spec in specs.items()
    }, db_path


def test_resolve_requires_owner_and_completion(tmp_path: Path) -> None:
    """Missing, foreign, and unfinished assets fail without storage details."""
    harness = build_resumable_asset(
        tmp_path,
        spec=ResumableAssetSpec(
            filename="input.fa",
            content=b"abc",
            complete=False,
        ),
    )

    with pytest.raises(UploadContractError) as unfinished:
        harness.resolver.resolve(harness.asset_id, harness.owner)
    assert unfinished.value.code == "upload_state_conflict"

    with pytest.raises(UploadContractError) as foreign:
        harness.resolver.resolve(harness.asset_id, "owner-2")
    assert foreign.value.code == "upload_asset_not_found"
    with pytest.raises(UploadContractError) as missing:
        harness.resolver.resolve("file_does_not_exist", harness.owner)
    assert missing.value.code == "upload_asset_not_found"
    assert "resolver-bucket" not in str(foreign.value)
    assert harness.capability not in str(foreign.value)


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("sample.fasta", b">gene1\nATGCATGC\n"),
        ("sample.fastq.gz", gzip.compress(b"@r1\nATGC\n+\n!!!!\n", mtime=0)),
        ("sample.vcf.gz", gzip.compress(b"##fileformat=VCFv4.3\n", mtime=0)),
        ("sample.gff", b"chr1\tsrc\tgene\t1\t4\t.\t+\t.\tID=g1\n"),
        ("sample.gtf", b'chr1\tsrc\texon\t1\t4\t.\t+\t.\tgene_id "g1";\n'),
        ("sample.bed", b"chr1\t0\t4\tg1\n"),
        ("sample.bam", b"BAM\x01synthetic-binary\x00"),
        ("sample.h5ad", b"\x89HDF\r\n\x1a\nsynthetic"),
        ("sample.bin", bytes(range(256))),
    ],
)
def test_materialize_is_byte_faithful_and_non_executable(
    tmp_path: Path,
    filename: str,
    content: bytes,
) -> None:
    """Delivery preserves bytes and creates private generated paths."""
    resolver, asset_id, owner, expected = _build_completed_asset(
        tmp_path,
        filename=filename,
        content=content,
    )

    descriptor = resolver.resolve(asset_id, owner)
    assert descriptor.filename == filename
    assert descriptor.size_bytes == len(expected)
    assert "object_key" not in descriptor.model_dump()
    assert "capability" not in descriptor.model_dump()

    materialized = resolver.materialize(asset_id, owner, "run-asset-1")
    assert materialized.read_bytes() == expected
    assert materialized.name != filename
    assert stat.S_IMODE(materialized.stat().st_mode) == 0o600
    assert stat.S_IMODE(materialized.parent.stat().st_mode) == 0o700

    resolver.cleanup_run("run-asset-1")
    resolver.cleanup_run("run-asset-1")
    assert not materialized.parent.exists()


def test_attachment_normalization_projects_only_owner_checked_references(
    tmp_path: Path,
) -> None:
    """Asset IDs become paths without widening the caller allowlist."""
    resolver, asset_id, owner, _content = _build_completed_asset(
        tmp_path,
        filename="context.pdf",
        content=b"pdf-fixture",
    )
    normalized = normalize_asset_attachments(
        {
            "user_query": "summarize",
            "attachments": [{"asset_id": asset_id}],
        },
        owner=owner,
        resolver=resolver,
    )
    assert normalized["user_query"] == "summarize"
    assert len(normalized["obs_file_list"]) == 1
    assert "attachments" not in normalized
    assert "allowed_tools" not in normalized
    assert "resolver-bucket" in normalized["obs_file_list"][0]

    with pytest.raises(UploadContractError) as duplicate:
        normalize_asset_attachments(
            {"attachments": [{"asset_id": asset_id}, {"asset_id": asset_id}]},
            owner=owner,
            resolver=resolver,
        )
    assert duplicate.value.code == "upload_state_conflict"


def test_redact_managed_attachment_values_is_recursive_and_nonmutating() -> (
    None
):
    """Private evidence is removed from nested HTTP/persistence projections."""
    document_reference = "obs://private/document"
    dataset_reference = "obs://private/dataset"
    projection_fixture = AttachmentProjectionFixture(
        reference=dataset_reference,
        attachments="drop this field",
        nested=(
            {"owner_subject": "u1"},
            {"attachments": "drop this too"},
        ),
    )
    value = {
        "keep": "prefix obs://private/document suffix",
        "attachment_evidence": "drop this private evidence",
        "evidence": "drop this private evidence too",
        "obs_file_list": [document_reference],
        "data_list": {dataset_reference: "description"},
        "nested": [
            "obs://private/dataset",
            ("unrelated", document_reference),
            projection_fixture,
        ],
    }
    evidence = ManagedAttachmentEvidence(
        attachment_owner="u1",
        items=(
            managed_attachment_evidence_item(
                ManagedAttachmentEvidenceSpec(
                    asset_id="file_document",
                    reference=document_reference,
                    filename="document.pdf",
                    content_type="application/pdf",
                    size_bytes=1,
                    purpose="document",
                    projected_channel="obs_file_list",
                )
            ),
            managed_attachment_evidence_item(
                ManagedAttachmentEvidenceSpec(
                    asset_id="file_dataset",
                    reference=dataset_reference,
                    filename="dataset.h5ad",
                    content_type="application/octet-stream",
                    size_bytes=1,
                    purpose="dataset",
                    projected_channel="data_list",
                )
            ),
        ),
    )

    redacted = redact_managed_attachment_values(value, evidence)

    assert redacted == {
        "keep": "prefix <redacted-attachment> suffix",
        "nested": [
            "<redacted-attachment>",
            ("unrelated", "<redacted-attachment>"),
            {
                "reference": "<redacted-attachment>",
                "nested": ({}, {}),
            },
        ],
    }
    assert value["obs_file_list"] == [document_reference]
    assert value["data_list"] == {dataset_reference: "description"}
    assert projection_fixture.reference == dataset_reference
    assert document_reference not in repr(redacted)
    assert dataset_reference not in repr(redacted)
    assert "evidence" not in redacted


def test_resolve_bundle_preserves_canonical_and_derived_request_order(
    tmp_path: Path,
) -> None:
    """Bundle resolution preserves canonical and derived request order."""
    assets, db_path = _build_purpose_assets(tmp_path)
    resolver = assets["document"].resolver
    owner = assets["document"].owner
    document_bundle = resolver.resolve_bundle(
        [
            {"asset_id": assets["document"].asset_id},
            {"asset_id": assets["legacy"].asset_id},
        ],
        owner,
    )
    dataset_bundle = resolver.resolve_bundle(
        [
            {"asset_id": assets["dataset_a"].asset_id},
            {"asset_id": assets["dataset_b"].asset_id},
        ],
        owner,
    )
    mixed_bundle = resolver.resolve_bundle(
        [
            {"asset_id": assets["dataset_a"].asset_id},
            {"asset_id": assets["document"].asset_id},
            {"asset_id": assets["dataset_b"].asset_id},
            {"asset_id": assets["legacy"].asset_id},
        ],
        owner,
    )

    assert [asset.asset_id for asset in document_bundle.documents] == [
        assets["document"].asset_id,
        assets["legacy"].asset_id,
    ]
    assert not document_bundle.datasets
    assert [asset.asset_id for asset in dataset_bundle.datasets] == [
        assets["dataset_a"].asset_id,
        assets["dataset_b"].asset_id,
    ]
    assert not dataset_bundle.documents
    assert [asset.asset_id for asset in mixed_bundle.datasets] == [
        assets["dataset_a"].asset_id,
        assets["dataset_b"].asset_id,
    ]
    assert [asset.asset_id for asset in mixed_bundle.documents] == [
        assets["document"].asset_id,
        assets["legacy"].asset_id,
    ]
    assert [asset.asset_id for asset in mixed_bundle.assets] == [
        assets["dataset_a"].asset_id,
        assets["document"].asset_id,
        assets["dataset_b"].asset_id,
        assets["legacy"].asset_id,
    ]
    assert [asset.asset_id for asset in mixed_bundle.all_assets] == [
        assets["dataset_a"].asset_id,
        assets["document"].asset_id,
        assets["dataset_b"].asset_id,
        assets["legacy"].asset_id,
    ]

    expected = {
        assets["document"].asset_id: (
            "document-a.pdf",
            len(b"document-a"),
            "document",
        ),
        assets["legacy"].asset_id: (
            "legacy-b.pdf",
            len(b"legacy-b"),
            "document",
        ),
        assets["dataset_a"].asset_id: (
            "dataset-a.csv",
            len(b"dataset-a"),
            "dataset",
        ),
        assets["dataset_b"].asset_id: (
            "dataset-b.csv",
            len(b"dataset-b"),
            "dataset",
        ),
    }
    legacy_registry = UploadRegistry(db_path)
    for asset in mixed_bundle.all_assets:
        filename, size_bytes, purpose = expected[asset.asset_id]
        assert (asset.filename, asset.size_bytes, asset.purpose) == (
            filename,
            size_bytes,
            purpose,
        )
        assert asset.content_type == "application/octet-stream"
        projection = legacy_registry.get_by_path(
            asset.reference,
            owner=owner,
        )
        assert projection is not None
        assert projection.purpose == purpose


def test_native_preparation_keeps_source_order_in_private_evidence(
    tmp_path: Path,
) -> None:
    """Final capability projection keeps legacy values before managed ones."""
    assets, db_path = _build_purpose_assets(tmp_path)
    resolver = assets["document"].resolver
    owner = assets["document"].owner
    bundle = resolver.resolve_bundle(
        [
            {"asset_id": assets["dataset_a"].asset_id},
            {"asset_id": assets["document"].asset_id},
            {"asset_id": assets["dataset_b"].asset_id},
            {"asset_id": assets["legacy"].asset_id},
        ],
        owner,
    )

    prepared, context = prepare_native_attachment_arguments(
        agent="analyst",
        arguments={
            "obs_file_list": [],
            "data_list": {"/obs/phytomni/prepared/input.fasta": "legacy"},
        },
        resolved_input=ResolvedAttachmentInput(
            attachment_owner=owner,
            bundle=bundle,
        ),
        db_path=db_path,
    )

    assert prepared["obs_file_list"] == [
        assets["document"].resolver.internal_reference(
            assets["document"].asset_id, owner
        ),
        assets["legacy"].resolver.internal_reference(
            assets["legacy"].asset_id, owner
        ),
    ]
    assert list(prepared["data_list"].values()) == ["legacy", "", ""]
    assert context.evidence is not None
    assert [item.asset.asset_id for item in context.evidence.items] == [
        assets["dataset_a"].asset_id,
        assets["document"].asset_id,
        assets["dataset_b"].asset_id,
        assets["legacy"].asset_id,
    ]
    assert [item.projected_channel for item in context.evidence.items] == [
        "data_list",
        "obs_file_list",
        "data_list",
        "obs_file_list",
    ]


def test_resolve_bundle_keeps_historical_legacy_projection_unchanged(
    tmp_path: Path,
) -> None:
    """Existing legacy rows remain untouched when a typed asset resolves."""
    db_path = str(tmp_path / "uploads.sqlite")
    harness = build_resumable_asset(
        tmp_path,
        db_path=db_path,
        spec=ResumableAssetSpec(purpose="dataset"),
    )
    uploaded = harness.service.registry.get_asset(
        harness.asset_id,
        owner=harness.owner,
    )
    assert uploaded is not None
    assert uploaded.completed_at is not None
    reference = asset_resolver_module.obs_path_from_key(
        harness.service.bucket_name,
        uploaded.object_key,
    )
    legacy_registry = UploadRegistry(db_path)
    legacy_registry.record(
        UploadMetadata(
            file_id=harness.asset_id,
            user_id=harness.owner,
            obs_path=reference,
            filename=uploaded.filename,
            purpose="agent_context",
            byte_size=uploaded.size_bytes,
            format="pdf",
            media_type=uploaded.content_type,
            created_at=uploaded.completed_at.isoformat(),
        )
    )

    bundle = harness.resolver.resolve_bundle(
        [{"asset_id": harness.asset_id}],
        harness.owner,
    )

    assert bundle.datasets[0].purpose == "dataset"
    projection = legacy_registry.get_by_path(reference, owner=harness.owner)
    assert projection is not None
    assert projection.purpose == "agent_context"


def test_resolve_bundle_rejects_duplicate_ids_before_lookup(
    tmp_path: Path,
) -> None:
    """Duplicate opaque IDs fail before loading completed asset state twice."""
    harness = build_resumable_asset(tmp_path)
    with (
        patch.object(
            harness.resolver,
            "_completed_asset",
            wraps=getattr(harness.resolver, "_completed_asset"),
        ) as completed_asset,
        pytest.raises(UploadContractError) as error,
    ):
        harness.resolver.resolve_bundle(
            [
                {"asset_id": harness.asset_id},
                {"asset_id": harness.asset_id},
            ],
            harness.owner,
        )

    assert error.value.code == "upload_state_conflict"
    assert not completed_asset.called


def test_resolve_bundle_rejects_duplicate_references_without_leaks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A collision in derived references is a sanitized state conflict."""
    db_path = str(tmp_path / "uploads.sqlite")
    first = build_resumable_asset(
        tmp_path,
        db_path=db_path,
        spec=ResumableAssetSpec(filename="first.pdf", content=b"first"),
    )
    second = build_resumable_asset(
        tmp_path,
        db_path=db_path,
        spec=ResumableAssetSpec(filename="second.pdf", content=b"second"),
    )
    constant_reference = "obs://generated-reference-sentinel"
    monkeypatch.setattr(
        asset_resolver_module,
        "obs_path_from_key",
        lambda _bucket, _key: constant_reference,
    )

    with pytest.raises(UploadContractError) as error:
        first.resolver.resolve_bundle(
            [{"asset_id": first.asset_id}, {"asset_id": second.asset_id}],
            first.owner,
        )

    assert error.value.code == "upload_state_conflict"
    assert constant_reference not in str(error.value)


def test_resolve_bundle_rejects_unknown_persisted_purpose(
    tmp_path: Path,
) -> None:
    """Corrupt persisted purpose values fail closed without migration."""
    harness = build_resumable_asset(tmp_path)
    unknown_id = "file_unknown_purpose_1234567890"
    uploaded = harness.service.registry.get_asset(
        harness.asset_id,
        owner=harness.owner,
    )
    assert uploaded is not None
    assert uploaded.completed_at is not None
    corrupt_record = {
        "asset_id": unknown_id,
        "owner_subject": uploaded.owner_subject,
        "filename": uploaded.filename,
        "content_type": uploaded.content_type,
        "purpose": "unknown-purpose",
        "size_bytes": uploaded.size_bytes,
        "part_size_bytes": uploaded.part_size_bytes,
        "part_count": uploaded.part_count,
        "status": uploaded.status,
        "object_key": "agent_data/uploads/corrupt-purpose-sentinel",
        "obs_upload_id": uploaded.obs_upload_id,
        "idempotency_key": "corrupt-purpose-idempotency",
        "request_fingerprint": "corrupt-purpose-fingerprint",
        "state_version": uploaded.state_version,
        "reserved_bytes": uploaded.reserved_bytes,
        "created_at": uploaded.created_at.isoformat(),
        "updated_at": uploaded.updated_at.isoformat(),
        "session_expires_at": uploaded.session_expires_at.isoformat(),
        "completed_at": uploaded.completed_at.isoformat(),
    }
    with sqlite3.connect(harness.service.registry.db_path) as connection:
        connection.execute(
            f"INSERT INTO upload_assets "
            f"({', '.join(corrupt_record)}) "
            f"VALUES ({', '.join('?' for _key in corrupt_record)})",
            tuple(corrupt_record.values()),
        )

    with pytest.raises(UploadContractError) as error:
        harness.resolver.resolve_bundle(
            [{"asset_id": unknown_id}],
            harness.owner,
        )

    assert error.value.code == "upload_state_conflict"
    assert "unknown-purpose" not in str(error.value)


def test_resolve_bundle_hides_missing_foreign_and_incomplete_details(
    tmp_path: Path,
) -> None:
    """Finite resolver failures keep owner and storage state private."""
    foreign = build_resumable_asset(
        tmp_path,
        spec=ResumableAssetSpec(
            owner="foreign-owner-sentinel",
            filename="private-object-sentinel.pdf",
            content=b"foreign",
        ),
    )
    incomplete = build_resumable_asset(
        tmp_path,
        spec=ResumableAssetSpec(
            filename="incomplete.pdf",
            content=b"incomplete",
            complete=False,
        ),
    )
    foreign_asset = foreign.service.registry.get_asset(
        foreign.asset_id,
        owner=foreign.owner,
    )
    assert foreign_asset is not None
    reference = asset_resolver_module.obs_path_from_key(
        foreign.service.bucket_name,
        foreign_asset.object_key,
    )

    with pytest.raises(UploadContractError) as missing:
        foreign.resolver.resolve_bundle(
            [{"asset_id": "file_missing_asset_1234567890"}],
            "owner-1",
        )
    with pytest.raises(UploadContractError) as cross_owner:
        foreign.resolver.resolve_bundle(
            [{"asset_id": foreign.asset_id}],
            "owner-1",
        )
    with pytest.raises(UploadContractError) as not_completed:
        incomplete.resolver.resolve_bundle(
            [{"asset_id": incomplete.asset_id}],
            incomplete.owner,
        )

    assert (
        missing.value.code
        == cross_owner.value.code
        == "upload_asset_not_found"
    )
    assert str(missing.value) == str(cross_owner.value)
    assert not_completed.value.code == "upload_state_conflict"
    for value in (
        foreign.owner,
        foreign.asset_id,
        foreign_asset.object_key,
        foreign.capability,
        reference,
    ):
        assert value not in str(cross_owner.value)


def test_attachment_normalization_rejects_dataset_assets(
    tmp_path: Path,
) -> None:
    """The legacy document-only normalizer cannot silently downgrade data."""
    harness = build_resumable_asset(
        tmp_path,
        spec=ResumableAssetSpec(purpose="dataset"),
    )

    with pytest.raises(UploadContractError) as error:
        normalize_asset_attachments(
            {"attachments": [{"asset_id": harness.asset_id}]},
            owner=harness.owner,
            resolver=harness.resolver,
        )

    assert error.value.code == "upload_state_conflict"


def test_unsupported_new_asset_format_has_stable_agent_boundary_error(
    tmp_path: Path,
) -> None:
    """A new asset format fails as unsupported rather than as a timeout."""
    resolver, asset_id, owner, _content = _build_completed_asset(
        tmp_path,
        filename="reads.bam",
        content=b"BAM\x01fixture",
    )
    normalized = normalize_asset_attachments(
        {"attachments": [{"asset_id": asset_id}]},
        owner=owner,
        resolver=resolver,
    )
    with pytest.raises(AttachmentContractError) as raised:
        validate_agent_attachments(
            "chat",
            normalized,
            owner=owner,
            registry=UploadRegistry(str(tmp_path / "uploads.sqlite")),
        )
    assert getattr(raised.value, "code", None) == "unsupported_asset_format"
