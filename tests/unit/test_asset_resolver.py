# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Owner isolation and byte-faithful delivery tests for upload assets."""

from __future__ import annotations

import gzip
import hashlib
import stat
from io import BytesIO
from pathlib import Path

import pytest

from mcp_server_phytomni.api.asset_resolver import (
    AssetResolver,
    normalize_asset_attachments,
)
from mcp_server_phytomni.api.attachments import (
    AttachmentContractError,
    validate_agent_attachments,
)
from mcp_server_phytomni.api.resumable_uploads import (
    ResumableUploadService,
    UploadContractError,
    UploadServiceConfig,
)
from mcp_server_phytomni.api.schemas import (
    UploadCompletionRequest,
    UploadCreateRequest,
)
from mcp_server_phytomni.runtime.resumable_uploads import (
    ResumableUploadRegistry,
)
from mcp_server_phytomni.runtime.upload_registry import UploadRegistry
from mcp_server_phytomni.storage.multipart import (
    FakeMultipartStorage,
    PartInput,
)

pytestmark = pytest.mark.unit


def _build_completed_asset(
    tmp_path: Path,
    *,
    filename: str,
    content: bytes,
) -> tuple[AssetResolver, str, str, bytes]:
    """Create one completed fake asset and its owner-scoped resolver."""
    registry = ResumableUploadRegistry(str(tmp_path / "uploads.sqlite"))
    storage = FakeMultipartStorage()
    service = ResumableUploadService(
        registry,
        storage,
        UploadServiceConfig(
            bucket_name="resolver-bucket",
            upload_origin="https://upload.example",
        ),
    )
    request = UploadCreateRequest(
        owner_subject="owner-1",
        filename=filename,
        content_type="application/octet-stream",
        size_bytes=len(content),
        purpose="chat_attachment",
        idempotency_key=f"resolver-{hashlib.sha256(content).hexdigest()}",
    )
    created = service.create(request)
    digest = hashlib.sha256(content).hexdigest()
    service.put_part(
        created.asset_id,
        created.capability,
        PartInput(1, BytesIO(content), len(content), digest),
    )
    service.complete(
        created.asset_id,
        created.capability,
        UploadCompletionRequest(),
    )
    resolver = AssetResolver(
        registry,
        storage.download_to_path,
        bucket_name="resolver-bucket",
        workspace_root=tmp_path / "materialized",
    )
    return resolver, created.asset_id, "owner-1", content


def test_resolve_requires_owner_and_completion(tmp_path: Path) -> None:
    """Missing, foreign, and unfinished assets fail without storage details."""
    registry = ResumableUploadRegistry(str(tmp_path / "uploads.sqlite"))
    storage = FakeMultipartStorage()
    service = ResumableUploadService(
        registry,
        storage,
        UploadServiceConfig(
            bucket_name="resolver-bucket",
            upload_origin="https://upload.example",
        ),
    )
    created = service.create(
        UploadCreateRequest(
            owner_subject="owner-1",
            filename="input.fa",
            size_bytes=3,
            purpose="chat_attachment",
            idempotency_key="unfinished-asset",
        )
    )
    resolver = AssetResolver(
        registry,
        storage.download_to_path,
        bucket_name="resolver-bucket",
        workspace_root=tmp_path / "materialized",
    )

    with pytest.raises(UploadContractError) as unfinished:
        resolver.resolve(created.asset_id, "owner-1")
    assert unfinished.value.code == "upload_state_conflict"

    with pytest.raises(UploadContractError) as foreign:
        resolver.resolve(created.asset_id, "owner-2")
    assert foreign.value.code == "upload_asset_not_found"
    with pytest.raises(UploadContractError) as missing:
        resolver.resolve("file_does_not_exist", "owner-1")
    assert missing.value.code == "upload_asset_not_found"
    assert "resolver-bucket" not in str(foreign.value)
    assert created.capability not in str(foreign.value)


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
