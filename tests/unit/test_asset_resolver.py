# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Owner isolation and byte-faithful delivery tests for upload assets."""

from __future__ import annotations

import gzip
import hashlib
import sqlite3
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from tests.support.attachment_fakes import (
    managed_dataset_evidence_item,
    managed_document_evidence_item,
)
from tests.support.resumable_asset_fakes import (
    ResumableAssetHarness,
    ResumableAssetSpec,
    build_resumable_asset,
)

from mcp_server_phytomni.api import asset_resolver as asset_resolver_module
from mcp_server_phytomni.api.asset_resolver import (
    AssetResolver,
    bind_research_asset_resolver,
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
from mcp_server_phytomni.api.schemas import AttachmentAsset
from mcp_server_phytomni.runtime.resumable_uploads import AssetRecord
from mcp_server_phytomni.runtime.upload_registry import (
    UploadMetadata,
    UploadRegistry,
)
from mcp_server_phytomni.storage.multipart import MultipartStorageError

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


def test_historical_chat_attachment_reads_as_document_without_row_update(
    tmp_path: Path,
) -> None:
    """Historical chat rows remain stored while resolving as documents."""
    harness = build_resumable_asset(
        tmp_path,
        spec=ResumableAssetSpec(purpose="chat_attachment"),
    )
    with sqlite3.connect(harness.service.registry.db_path) as connection:
        before = connection.execute(
            "SELECT purpose FROM upload_assets WHERE asset_id = ?",
            (harness.asset_id,),
        ).fetchone()[0]

    bundle = harness.resolver.resolve_bundle(
        [{"asset_id": harness.asset_id}], harness.owner
    )

    with sqlite3.connect(harness.service.registry.db_path) as connection:
        after = connection.execute(
            "SELECT purpose FROM upload_assets WHERE asset_id = ?",
            (harness.asset_id,),
        ).fetchone()[0]
    assert before == after == "chat_attachment"
    assert bundle.documents[0].purpose == "document"
    assert bundle.documents[0].state_version >= 1
    assert bundle.documents[0].completed_at


def test_bound_research_asset_resolver_rechecks_effective_owner(
    tmp_path: Path,
) -> None:
    """A bound Research resolver re-reads current state under its owner."""
    resolver, asset_id, owner, _content = _build_completed_asset(
        tmp_path,
        filename="current.tsv",
        content=b"value\n",
    )
    bound = bind_research_asset_resolver(owner=owner, resolver=resolver)

    snapshots = bound((asset_id,))

    assert [snapshot.asset_id for snapshot in snapshots] == [asset_id]
    assert snapshots[0].exact_reference.startswith("obs://resolver-bucket/")
    assert snapshots[0].completed
    assert snapshots[0].state_version >= 1
    assert snapshots[0].completed_at
    foreign = bind_research_asset_resolver(
        owner="foreign-owner",
        resolver=resolver,
    )
    with pytest.raises(UploadContractError) as caught:
        foreign((asset_id,))
    assert caught.value.code == "upload_asset_not_found"


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
            managed_document_evidence_item(
                asset_id="file_document",
                reference=document_reference,
            ),
            managed_dataset_evidence_item(
                asset_id="file_dataset",
                reference=dataset_reference,
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


def _fault_asset_record(*, completed: bool = True) -> AssetRecord:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return AssetRecord(
        asset_id="file_abcdefghijklmnopqrstuv",
        owner_subject="owner-1",
        filename="doc.pdf",
        content_type="application/pdf",
        purpose="document",
        size_bytes=4,
        part_size_bytes=1024,
        part_count=1,
        status="completed",
        object_key="agent_data/uploads/doc",
        obs_upload_id="upload-1",
        idempotency_key="idem-1",
        state_version=1,
        reserved_bytes=4,
        created_at=now,
        updated_at=now,
        session_expires_at=now,
        completed_at=now if completed else None,
        activated_at=now,
    )


def test_materialize_rejects_oversize_and_unsafe_run_ids(
    tmp_path: Path,
) -> None:
    """Materialization stays bounded and refuses unsafe generated paths."""
    resolver, asset_id, owner, _c = _build_completed_asset(
        tmp_path, filename="big.bin", content=b"1234"
    )
    resolver.max_materialized_bytes = 1
    with pytest.raises(UploadContractError) as oversize:
        resolver.materialize(asset_id, owner, "run-1")
    assert oversize.value.code == "upload_limit_exceeded"
    resolver.max_materialized_bytes = 1024
    with pytest.raises(UploadContractError) as unsafe:
        resolver.materialize(asset_id, owner, "../escape")
    assert unsafe.value.code == "invalid_upload_metadata"


def test_materialize_maps_storage_and_cleanup_faults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Storage faults stay sanitized and leftover partials are removed."""
    resolver, asset_id, owner, content = _build_completed_asset(
        tmp_path, filename="doc.bin", content=b"abcd"
    )
    run_id = "run-fault"
    leftover = (
        resolver.workspace_root
        / run_id
        / (
            ".asset-"
            + hashlib.sha256(asset_id.encode()).hexdigest()
            + ".bin.partial"
        )
    )
    leftover.parent.mkdir(parents=True, exist_ok=True)
    leftover.write_bytes(b"stale")

    def _mismatch(**kw):
        Path(str(kw["destination"])).write_bytes(content)
        return len(content) + 1

    monkeypatch.setattr(resolver, "storage", _mismatch)
    with pytest.raises(UploadContractError) as e:
        resolver.materialize(asset_id, owner, run_id)
    assert e.value.code == "upload_state_conflict"
    monkeypatch.setattr(
        resolver,
        "storage",
        lambda **k: (_ for _ in ()).throw(
            MultipartStorageError("upload_asset_not_found")
        ),
    )
    with pytest.raises(UploadContractError) as e:
        resolver.materialize(asset_id, owner, run_id)
    assert e.value.status_code == 404
    monkeypatch.setattr(
        resolver,
        "storage",
        lambda **k: (_ for _ in ()).throw(
            UploadContractError("upload_limit_exceeded", 413)
        ),
    )
    with pytest.raises(UploadContractError) as e:
        resolver.materialize(asset_id, owner, run_id)
    assert e.value.code == "upload_limit_exceeded"
    monkeypatch.setattr(
        resolver, "storage", lambda **k: (_ for _ in ()).throw(OSError("disk"))
    )
    with pytest.raises(UploadContractError) as e:
        resolver.materialize(asset_id, owner, run_id)
    assert e.value.code == "upload_storage_unavailable"
    original = Path.unlink

    def busy(self, *a, **k):
        if self.name.endswith(".partial"):
            raise OSError("busy")
        return original(self, *a, **k)

    leftover.write_bytes(b"stale")
    monkeypatch.setattr(Path, "unlink", busy)
    with pytest.raises(UploadContractError) as e:
        resolver.materialize(asset_id, owner, run_id)
    assert e.value.code == "upload_storage_unavailable"


def test_cleanup_run_ignores_unsafe_ids_and_unlinks_symlinks(
    tmp_path: Path,
) -> None:
    """Cleanup refuses unsafe ids and does not follow generated symlinks."""
    resolver, *_ = _build_completed_asset(
        tmp_path, filename="doc.bin", content=b"abcd"
    )
    resolver.cleanup_run("../escape")
    target = tmp_path / "real-target"
    target.mkdir()
    (target / "keep.txt").write_text("keep")
    resolver.workspace_root.mkdir(parents=True, exist_ok=True)
    link = resolver.workspace_root / "run-link"
    link.symlink_to(target)
    resolver.cleanup_run("run-link")
    assert not link.exists()
    assert (target / "keep.txt").read_text() == "keep"
    resolver.cleanup_run("missing-run")


def test_completed_asset_requires_completion_timestamp(tmp_path: Path) -> None:
    """A completed row without a timestamp is a sanitized state conflict."""
    resolver, _a, owner, _c = _build_completed_asset(
        tmp_path, filename="doc.bin", content=b"abcd"
    )
    record = _fault_asset_record(completed=False)
    with (
        patch.object(resolver.registry, "get_asset", return_value=record),
        pytest.raises(UploadContractError) as e,
    ):
        getattr(resolver, "_completed_asset")(record.asset_id, owner)
    assert e.value.code == "upload_state_conflict"


def test_legacy_projection_integrity_conflict_fails_closed(
    tmp_path: Path,
) -> None:
    """A vanished row after IntegrityError stays a state conflict."""
    resolver, _a, owner, _c = _build_completed_asset(
        tmp_path, filename="doc.bin", content=b"abcd"
    )
    with (
        patch.object(
            resolver.legacy_registry, "get_by_path", return_value=None
        ),
        patch.object(
            resolver.legacy_registry,
            "record",
            side_effect=sqlite3.IntegrityError("dup"),
        ),
        pytest.raises(UploadContractError) as e,
    ):
        getattr(resolver, "_ensure_legacy_projection")(
            _fault_asset_record(), owner, "obs://bucket/key", "document"
        )
    assert e.value.code == "upload_state_conflict"


def test_normalize_attachments_merges_and_rejects_invalid_lists(
    tmp_path: Path,
) -> None:
    """Empty lists no-op; existing path lists merge; other types fail."""
    resolver, asset_id, owner, _c = _build_completed_asset(
        tmp_path, filename="context.pdf", content=b"pdf"
    )
    assert normalize_asset_attachments(
        {"attachments": [], "user_query": "keep"},
        owner=owner,
        resolver=resolver,
    ) == {"user_query": "keep"}
    with pytest.raises(UploadContractError) as e:
        normalize_asset_attachments(
            {"attachments": "file_not_a_sequence"},
            owner=owner,
            resolver=resolver,
        )
    assert e.value.code == "invalid_upload_metadata"
    merged = normalize_asset_attachments(
        {
            "attachments": [{"asset_id": asset_id}],
            "obs_file_list": ["obs://legacy/one"],
        },
        owner=owner,
        resolver=resolver,
    )
    assert (
        merged["obs_file_list"][0] == "obs://legacy/one"
        and len(merged["obs_file_list"]) == 2
    )
    with pytest.raises(UploadContractError) as e:
        normalize_asset_attachments(
            {
                "attachments": [{"asset_id": asset_id}],
                "obs_file_list": "not-a-list",
            },
            owner=owner,
            resolver=resolver,
        )
    assert e.value.code == "invalid_upload_metadata"


def test_asset_id_extraction_accepts_model_and_attribute_items(
    tmp_path: Path,
) -> None:
    """Resolver input accepts the public model or an attribute carrier."""
    resolver, asset_id, owner, _c = _build_completed_asset(
        tmp_path, filename="context.pdf", content=b"pdf"
    )
    bundle = resolver.resolve_bundle(
        (AttachmentAsset(asset_id=asset_id),), owner
    )
    assert bundle.documents[0].asset_id == asset_id
    assert (
        getattr(asset_resolver_module, "_asset_id_from_item")(
            SimpleNamespace(asset_id=asset_id)
        )
        == asset_id
    )
    with pytest.raises(UploadContractError):
        getattr(asset_resolver_module, "_asset_id_from_item")(object())


def test_descriptor_and_iso_helpers_fail_closed_without_timestamp() -> None:
    """Public projection helpers refuse incomplete completion metadata."""
    helpers = asset_resolver_module
    with pytest.raises(UploadContractError) as e:
        getattr(helpers, "_descriptor")(_fault_asset_record(completed=False))
    assert e.value.code == "upload_state_conflict"
    with pytest.raises(UploadContractError) as e:
        getattr(helpers, "_iso")(None)
    assert e.value.code == "upload_state_conflict"
    assert getattr(helpers, "_status_for")("upload_asset_not_found") == 404
    assert getattr(helpers, "_status_for")("unknown-code") == 503
    assert getattr(helpers, "_filename_format")("no-suffix") == "binary"
