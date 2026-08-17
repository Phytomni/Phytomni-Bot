# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Behavior contracts for deterministic terminal result archives."""

from __future__ import annotations

import math
import stat
from io import BytesIO
from types import SimpleNamespace
from typing import cast
from zipfile import ZipFile

import pytest
from tests.support.outbound_fakes import CountingObsRuntime

from mcp_server_phytomni.runtime import result_archive
from mcp_server_phytomni.runtime.artifact_roles import (
    ArtifactRole,
    ClassifiedArtifact,
)
from mcp_server_phytomni.runtime.execution_models import ExecutionWarning
from mcp_server_phytomni.runtime.result_archive import (
    ResultArchiveError,
    ResultArchiveInventory,
    ResultArchiveMember,
    build_result_archive_inventory,
    validate_result_archive_inventory,
)
from mcp_server_phytomni.runtime.run_registry_reports import (
    ReportArtifactGroup,
)
from mcp_server_phytomni.runtime.terminal_artifacts import TerminalArtifactSet

pytestmark = pytest.mark.unit


def _artifact(
    path: str,
    *,
    role: ArtifactRole = ArtifactRole.SCIENTIFIC_DATA,
    size: object = 3,
) -> ClassifiedArtifact:
    return ClassifiedArtifact(
        source_path=f"private/{path}",
        relative_path=path,
        role=role,
        media_type="application/octet-stream",
        size_bytes=cast(int, size),
        download_ref=f"/obs/phytomni/runs/run-1/{path}",
    )


def _groups(*sets: TerminalArtifactSet) -> tuple[ReportArtifactGroup, ...]:
    return tuple(
        ReportArtifactGroup(
            task_id=f"task-{index}",
            output_dir=f"/obs/phytomni/runs/run-1/part-{index:03d}",
            artifact_set=artifact_set,
        )
        for index, artifact_set in enumerate(sets, start=1)
    )


def _set(
    *artifacts: ClassifiedArtifact,
    warnings: tuple[ExecutionWarning, ...] = (),
) -> TerminalArtifactSet:
    return TerminalArtifactSet(artifacts=artifacts, warnings=warnings)


async def test_async_publish_leases_each_source_and_archive_sdk_attempt(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One source GET plus HEAD, PUT, HEAD uses four OBS leases."""
    inventory = build_result_archive_inventory(
        _groups(_set(_artifact("report.md", size=3)))
    )
    runtime = CountingObsRuntime(object())
    uploaded: dict[str, bytes] = {}
    sizes: dict[str, int] = {}
    monkeypatch.setattr(result_archive, "ARCHIVE_TEMP_ROOT", tmp_path)
    monkeypatch.setattr(
        result_archive,
        "SERVER_CONFIG",
        SimpleNamespace(BUCKET_NAME="phytomni"),
    )
    monkeypatch.setattr(
        result_archive,
        "iter_object_chunks",
        lambda *_args, **_kwargs: iter((b"abc",)),
    )

    def size(_bucket: str, key: str, **_kwargs: object) -> int:
        if key not in sizes:
            raise OSError("missing")
        return sizes[key]

    def put(_bucket: str, key: str, source, **_kwargs: object) -> str:
        uploaded[key] = source.read_bytes()
        sizes[key] = len(uploaded[key])
        return key

    monkeypatch.setattr(result_archive, "object_size", size)
    monkeypatch.setattr(result_archive, "put_object_file", put)

    key = await result_archive.build_and_publish_result_archive_with_runtime(
        inventory,
        agent="analyst",
        summary_markdown="safe answer",
        obs_runtime=runtime,
    )

    assert key in uploaded
    assert runtime.calls == 4


def test_inventory_uses_child_prefixes_and_excludes_internal_files() -> None:
    """Keep public report files under child-specific archive prefixes."""
    inventory = build_result_archive_inventory(
        _groups(
            _set(_artifact("report.md"), _artifact("result_files.json")),
            _set(_artifact("data/result.csv")),
        )
    )

    assert [member.archive_path for member in inventory.members] == [
        "results/part-001/report.md",
        "results/part-002/data/result.csv",
    ]
    assert all(
        "result_files.json" not in item.archive_path
        for item in inventory.members
    )
    assert inventory.digest.startswith("sha256:")


@pytest.mark.parametrize(
    ("artifact", "warning"),
    [
        (
            _artifact("report.md"),
            ExecutionWarning(
                "artifact_manifest_missing", False, "artifact_manifest"
            ),
        ),
        (
            _artifact("report.md"),
            ExecutionWarning(
                "artifact_manifest_invalid", False, "artifact_manifest"
            ),
        ),
    ],
)
def test_inventory_rejects_missing_or_invalid_manifest(
    artifact: ClassifiedArtifact, warning: ExecutionWarning
) -> None:
    """Reject terminal groups whose producer manifest is unusable."""
    with pytest.raises(ResultArchiveError, match="artifact_manifest_invalid"):
        build_result_archive_inventory(
            _groups(_set(artifact, warnings=(warning,)))
        )


@pytest.mark.parametrize(
    "path",
    [
        "archive.zip",
        "archive.tar",
        "archive.tar.gz",
        "archive.tgz",
        "archive.7z",
    ],
)
def test_inventory_excludes_nested_archives_even_when_scientific_data(
    path: str,
) -> None:
    """Reject nested archives even when their role is otherwise eligible."""
    with pytest.raises(ResultArchiveError, match="no_user_deliverables"):
        build_result_archive_inventory(_groups(_set(_artifact(path))))


def test_inventory_keeps_scientific_gzip_data() -> None:
    """Retain ordinary compressed scientific data files."""
    inventory = build_result_archive_inventory(
        _groups(_set(_artifact("sample.fastq.gz")))
    )

    assert [member.archive_path for member in inventory.members] == [
        "results/part-001/sample.fastq.gz"
    ]


@pytest.mark.parametrize("path", ["../report.md", "/report.md"])
def test_inventory_rejects_traversal_member(path: str) -> None:
    """Reject archive members that would escape their root."""
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        build_result_archive_inventory(_groups(_set(_artifact(path))))


def test_inventory_rejects_duplicate_members_and_bounds() -> None:
    """Reject duplicate members and inventories beyond configured bounds."""
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        build_result_archive_inventory(
            _groups(_set(_artifact("report.md"), _artifact("report.md")))
        )
    with pytest.raises(
        ResultArchiveError, match="archive_inventory_limit_exceeded"
    ):
        build_result_archive_inventory(
            _groups(_set(*[_artifact(f"{index}.csv") for index in range(201)]))
        )
    with pytest.raises(
        ResultArchiveError, match="archive_inventory_limit_exceeded"
    ):
        build_result_archive_inventory(
            _groups(_set(_artifact("large.csv", size=10 * 1024**3 + 1)))
        )


@pytest.mark.parametrize("size", ["3", 3.5, math.nan, True])
def test_inventory_rejects_non_integer_source_sizes(size: object) -> None:
    """Reject source metadata that cannot establish an exact archive size."""
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        build_result_archive_inventory(
            _groups(_set(_artifact("report.md", size=size)))
        )


def test_inventory_excludes_summary_and_producer_archive() -> None:
    """Build stable inventory membership without internal producer files."""
    groups = _groups(
        _set(
            _artifact("report.md"),
            _artifact("producer.zip", role=ArtifactRole.RESULT_ARCHIVE),
        )
    )
    first = build_result_archive_inventory(groups)
    second = build_result_archive_inventory(groups)

    assert first == second
    assert "summary.md" not in {
        member.archive_path for member in first.members
    }


def test_publish_writes_deterministic_zip_and_reuses_matching_object(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publish stable archive bytes once and reuse a size-matched object."""
    inventory = result_archive.build_result_archive_inventory(
        _groups(_set(_artifact("report.md", size=3)))
    )
    data = {inventory.members[0].download_ref: b"abc"}
    uploaded: dict[str, bytes] = {}
    sizes: dict[str, int] = {}
    streamed_refs: list[str] = []

    monkeypatch.setattr(result_archive, "ARCHIVE_TEMP_ROOT", tmp_path)
    monkeypatch.setattr(
        result_archive,
        "SERVER_CONFIG",
        SimpleNamespace(
            BUCKET_NAME="phytomni", OBS_SERVER="https://obs.example"
        ),
    )

    def iter_chunks(
        _bucket: str,
        reference: str,
        **_kwargs: object,
    ):
        """Record one source read while yielding the expected bytes."""
        streamed_refs.append(reference)
        return iter((data[reference],))

    monkeypatch.setattr(result_archive, "iter_object_chunks", iter_chunks)

    def put_file(_bucket: str, key: str, source, **_kwargs: object) -> str:
        uploaded[key] = source.read_bytes()
        sizes[key] = len(uploaded[key])
        return key

    monkeypatch.setattr(result_archive, "put_object_file", put_file)

    def existing_size(_bucket: str, key: str, **_kwargs: object) -> int:
        if key not in sizes:
            raise OSError("not found")
        return sizes[key]

    monkeypatch.setattr(result_archive, "object_size", existing_size)

    key = result_archive.build_and_publish_result_archive(
        inventory,
        agent="analyst",
        summary_markdown="safe answer\n\n",
        client=object(),
    )
    assert key == (
        f"{inventory.run_root}/delivery/"
        f"{inventory.digest.removeprefix('sha256:')}/analyst-results.zip"
    )
    with ZipFile(BytesIO(uploaded[key])) as archive:
        assert archive.namelist() == [
            "summary.md",
            "results/part-001/report.md",
        ]
        assert archive.read("summary.md") == b"safe answer\n"
        assert archive.read("results/part-001/report.md") == b"abc"
        assert all(
            item.date_time == (1980, 1, 1, 0, 0, 0)
            for item in archive.infolist()
        )
        assert all(
            stat.S_IFMT(item.external_attr >> 16) == stat.S_IFREG
            and stat.S_IMODE(item.external_attr >> 16) == 0o644
            for item in archive.infolist()
        )
    assert not list(tmp_path.iterdir())

    assert (
        result_archive.build_and_publish_result_archive(
            inventory,
            agent="analyst",
            summary_markdown="safe answer\n\n",
            client=object(),
        )
        == key
    )
    assert len(uploaded) == 1
    assert streamed_refs == [
        inventory.members[0].download_ref,
        inventory.members[0].download_ref,
    ]


@pytest.mark.parametrize(
    "stream", [lambda: iter((b"ab",)), lambda: iter((b"abcd",))]
)
def test_publish_rejects_stream_size_mismatch_before_publication(
    tmp_path, monkeypatch: pytest.MonkeyPatch, stream
) -> None:
    """Reject incomplete or oversized source streams before uploading."""
    inventory = result_archive.build_result_archive_inventory(
        _groups(_set(_artifact("report.md", size=3)))
    )
    published: list[str] = []
    monkeypatch.setattr(result_archive, "ARCHIVE_TEMP_ROOT", tmp_path)
    monkeypatch.setattr(
        result_archive,
        "SERVER_CONFIG",
        SimpleNamespace(
            BUCKET_NAME="phytomni", OBS_SERVER="https://obs.example"
        ),
    )
    monkeypatch.setattr(
        result_archive,
        "iter_object_chunks",
        lambda *_args, **_kwargs: stream(),
    )
    monkeypatch.setattr(
        result_archive,
        "put_object_file",
        lambda *_args, **_kwargs: published.append("called"),
    )

    with pytest.raises(
        result_archive.ResultArchiveError, match="archive_generation_failed"
    ):
        result_archive.build_and_publish_result_archive(
            inventory,
            agent="analyst",
            summary_markdown="safe",
            client=object(),
        )
    assert not published
    assert not list(tmp_path.iterdir())


def test_publish_rejects_post_upload_size_mismatch(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail when the published OBS object has an unexpected byte size."""
    inventory = result_archive.build_result_archive_inventory(
        _groups(_set(_artifact("report.md", size=3)))
    )
    calls = 0
    monkeypatch.setattr(result_archive, "ARCHIVE_TEMP_ROOT", tmp_path)
    monkeypatch.setattr(
        result_archive,
        "SERVER_CONFIG",
        SimpleNamespace(
            BUCKET_NAME="phytomni", OBS_SERVER="https://obs.example"
        ),
    )
    monkeypatch.setattr(
        result_archive,
        "iter_object_chunks",
        lambda *_args, **_kwargs: iter((b"abc",)),
    )
    monkeypatch.setattr(
        result_archive,
        "put_object_file",
        lambda *_args, **_kwargs: "published",
    )

    def size_after_upload(*_args: object, **_kwargs: object) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("not found")
        return 1

    monkeypatch.setattr(result_archive, "object_size", size_after_upload)

    with pytest.raises(
        result_archive.ResultArchiveError, match="archive_publish_failed"
    ):
        result_archive.build_and_publish_result_archive(
            inventory,
            agent="analyst",
            summary_markdown="safe",
            client=object(),
        )
    assert not list(tmp_path.iterdir())


def _valid_inventory() -> ResultArchiveInventory:
    """Return one validated inventory for publish-boundary tests."""
    return build_result_archive_inventory(
        _groups(_set(_artifact("report.md", size=3)))
    )


def test_unknown_error_code_and_inventory_boundaries() -> None:
    """Unknown codes remap; empty, ineligible, and missing fields fail."""
    error = ResultArchiveError("not-a-real-code")
    assert error.code == "archive_contract_invalid"
    with pytest.raises(ResultArchiveError, match="no_user_deliverables"):
        build_result_archive_inventory(())
    with pytest.raises(ResultArchiveError, match="no_user_deliverables"):
        build_result_archive_inventory(
            _groups(
                _set(_artifact("trace.log", role=ArtifactRole.EXECUTION_LOG))
            )
        )
    missing_ref = ClassifiedArtifact(
        source_path="private/report.md",
        relative_path="report.md",
        role=ArtifactRole.SCIENTIFIC_DATA,
        media_type="application/octet-stream",
        size_bytes=3,
        download_ref="",
    )
    missing_media = ClassifiedArtifact(
        source_path="private/report.md",
        relative_path="report.md",
        role=ArtifactRole.SCIENTIFIC_DATA,
        media_type="",
        size_bytes=3,
        download_ref="/obs/phytomni/runs/run-1/report.md",
    )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        build_result_archive_inventory(_groups(_set(missing_ref)))
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        build_result_archive_inventory(_groups(_set(missing_media)))
    listing = ExecutionWarning(
        "artifact_listing_failed", True, "artifact_listing"
    )
    with pytest.raises(ResultArchiveError, match="artifact_listing_failed"):
        build_result_archive_inventory(
            _groups(_set(_artifact("report.md"), warnings=(listing,)))
        )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        build_result_archive_inventory(
            (
                ReportArtifactGroup(
                    task_id="task-1",
                    output_dir="/obs/phytomni/runs/run-1/part-001",
                    artifact_set=_set(_artifact("report.md")),
                ),
                ReportArtifactGroup(
                    task_id="task-2",
                    output_dir="/obs/other/runs/run-2/part-002",
                    artifact_set=_set(_artifact("data.csv")),
                ),
            )
        )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        build_result_archive_inventory(
            (
                ReportArtifactGroup(
                    task_id="task-1",
                    output_dir="",
                    artifact_set=_set(_artifact("report.md")),
                ),
            )
        )


def test_validate_inventory_and_member_helpers() -> None:
    """Revalidation and scalar helpers reject contract violations."""
    good = _valid_inventory()
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        validate_result_archive_inventory(
            ResultArchiveInventory("", good.members, good.digest, 3)
        )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        validate_result_archive_inventory(
            ResultArchiveInventory(good.run_root, (), good.digest, 0)
        )
    ineligible = ResultArchiveMember(
        1,
        "/obs/phytomni/runs/run-1/trace.log",
        "results/part-001/trace.log",
        ArtifactRole.EXECUTION_LOG,
        "text/plain",
        3,
    )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        validate_result_archive_inventory(
            ResultArchiveInventory(
                good.run_root, (ineligible,), "sha256:" + "0" * 64, 3
            )
        )
    duplicate = ResultArchiveMember(
        1,
        good.members[0].download_ref,
        good.members[0].archive_path,
        good.members[0].role,
        good.members[0].media_type,
        3,
    )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        validate_result_archive_inventory(
            ResultArchiveInventory(
                good.run_root,
                (good.members[0], duplicate),
                "sha256:" + "0" * 64,
                6,
            )
        )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        validate_result_archive_inventory(
            ResultArchiveInventory(
                good.run_root, good.members, good.digest, 99
            )
        )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        result_archive._validate_member_scalars(
            ResultArchiveMember(
                True,  # type: ignore[arg-type]
                "/obs/x",
                "results/part-001/a.md",
                ArtifactRole.SCIENTIFIC_DATA,
                "text/plain",
                1,
            )
        )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        result_archive._validate_member_scalars(
            ResultArchiveMember(
                0,
                "/obs/x",
                "results/part-001/a.md",
                ArtifactRole.SCIENTIFIC_DATA,
                "text/plain",
                1,
            )
        )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        result_archive._validate_member_scalars(
            ResultArchiveMember(
                1,
                "",
                "results/part-001/a.md",
                ArtifactRole.SCIENTIFIC_DATA,
                "text/plain",
                1,
            )
        )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        result_archive._validate_member_scalars(
            ResultArchiveMember(
                1,
                "/obs/x",
                "results/part-001/a.md",
                ArtifactRole.SCIENTIFIC_DATA,
                "",
                1,
            )
        )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        result_archive._validate_member_scalars(
            ResultArchiveMember(
                1,
                "/obs/x",
                "results/part-001/a.md",
                ArtifactRole.SCIENTIFIC_DATA,
                "text/plain",
                True,  # type: ignore[arg-type]
            )
        )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        result_archive._validate_member_scalars(
            ResultArchiveMember(
                1,
                "/obs/x",
                "results/part-001/a.md",
                ArtifactRole.SCIENTIFIC_DATA,
                "text/plain",
                -1,
            )
        )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        result_archive._safe_relative_path("dir\\file.md")
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        result_archive._safe_absolute_path("relative/run")
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        result_archive._safe_absolute_path("/obs/phytomni/../escape")
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        result_archive._safe_archive_path("results/part-001/summary.md", 1)


def test_publish_rejects_invalid_inputs_and_maps_failures(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publish rejects bad inputs and maps generation and publish failures."""
    inventory = _valid_inventory()
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        result_archive.build_and_publish_result_archive(
            inventory,
            agent="bad-agent",
            summary_markdown="safe",
            client=object(),
        )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        result_archive.build_and_publish_result_archive(
            inventory,
            agent="analyst",
            summary_markdown=None,  # type: ignore[arg-type]
            client=object(),
        )
    monkeypatch.setattr(result_archive, "ARCHIVE_TEMP_ROOT", tmp_path)
    monkeypatch.setattr(
        result_archive,
        "SERVER_CONFIG",
        SimpleNamespace(BUCKET_NAME="phytomni"),
    )

    def boom(*_args: object, **_kwargs: object):
        raise OSError("read")

    monkeypatch.setattr(result_archive, "iter_object_chunks", boom)
    with pytest.raises(ResultArchiveError, match="archive_generation_failed"):
        result_archive.build_and_publish_result_archive(
            inventory,
            agent="analyst",
            summary_markdown="safe",
            client=object(),
        )
    monkeypatch.setattr(
        result_archive,
        "iter_object_chunks",
        lambda *_args, **_kwargs: iter((b"abc",)),
    )
    monkeypatch.setattr(result_archive, "object_size", lambda *_a, **_k: 1)
    with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
        result_archive.build_and_publish_result_archive(
            inventory,
            agent="analyst",
            summary_markdown="safe",
            client=object(),
        )

    def missing(*_args: object, **_kwargs: object) -> int:
        raise OSError("missing")

    def denied(*_args: object, **_kwargs: object) -> str:
        raise OSError("denied")

    monkeypatch.setattr(result_archive, "object_size", missing)
    monkeypatch.setattr(result_archive, "put_object_file", denied)
    with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
        result_archive.build_and_publish_result_archive(
            inventory,
            agent="analyst",
            summary_markdown="safe",
            client=object(),
        )
    monkeypatch.setattr(
        result_archive, "put_object_file", lambda *_a, **_k: "ok"
    )
    monkeypatch.setattr(result_archive, "object_size", lambda *_a, **_k: None)
    with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
        result_archive.build_and_publish_result_archive(
            inventory,
            agent="analyst",
            summary_markdown="safe",
            client=object(),
        )
    monkeypatch.setattr(
        result_archive,
        "iter_object_chunks",
        lambda *_args, **_kwargs: iter(("abc",)),
    )
    with pytest.raises(ResultArchiveError, match="archive_generation_failed"):
        result_archive.build_and_publish_result_archive(
            inventory,
            agent="analyst",
            summary_markdown="safe",
            client=object(),
        )

    def boom_temp(*_args: object, **_kwargs: object) -> None:
        raise OSError("scratch unavailable")

    monkeypatch.setattr(
        result_archive,
        "iter_object_chunks",
        lambda *_args, **_kwargs: iter((b"abc",)),
    )
    monkeypatch.setattr(
        result_archive.tempfile, "TemporaryDirectory", boom_temp
    )
    with pytest.raises(ResultArchiveError, match="archive_generation_failed"):
        result_archive.build_and_publish_result_archive(
            inventory,
            agent="analyst",
            summary_markdown="safe",
            client=object(),
        )


async def test_async_publish_and_size_helpers(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runtime publish covers reuse, failures, and size helper mapping."""
    inventory = _valid_inventory()
    runtime = CountingObsRuntime(object())
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        await result_archive.build_and_publish_result_archive_with_runtime(
            inventory, agent="", summary_markdown="safe", obs_runtime=runtime
        )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        await result_archive.build_and_publish_result_archive_with_runtime(
            inventory,
            agent="analyst",
            summary_markdown=1,  # type: ignore[arg-type]
            obs_runtime=runtime,
        )
    monkeypatch.setattr(result_archive, "ARCHIVE_TEMP_ROOT", tmp_path)
    monkeypatch.setattr(
        result_archive,
        "SERVER_CONFIG",
        SimpleNamespace(BUCKET_NAME="phytomni"),
    )
    monkeypatch.setattr(
        result_archive,
        "iter_object_chunks",
        lambda *_args, **_kwargs: iter((b"abc",)),
    )
    original_write = result_archive._write_archive_from_sources

    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("write")

    monkeypatch.setattr(
        result_archive, "_write_archive_from_sources", fail_write
    )
    with pytest.raises(ResultArchiveError, match="archive_generation_failed"):
        await result_archive.build_and_publish_result_archive_with_runtime(
            inventory,
            agent="analyst",
            summary_markdown="safe",
            obs_runtime=runtime,
        )
    monkeypatch.setattr(
        result_archive, "_write_archive_from_sources", original_write
    )
    monkeypatch.setattr(result_archive, "object_size", lambda *_a, **_k: 1)
    with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
        await result_archive.build_and_publish_result_archive_with_runtime(
            inventory,
            agent="analyst",
            summary_markdown="safe",
            obs_runtime=runtime,
        )
    sizes: dict[str, int] = {}

    def size(_bucket: str, key: str, **_kwargs: object) -> int:
        if key not in sizes:
            raise OSError("missing")
        return sizes[key]

    def put(_bucket: str, key: str, source, **_kwargs: object) -> str:
        sizes[key] = source.stat().st_size
        return key

    monkeypatch.setattr(result_archive, "object_size", size)
    monkeypatch.setattr(result_archive, "put_object_file", put)
    key = await result_archive.build_and_publish_result_archive_with_runtime(
        inventory,
        agent="analyst",
        summary_markdown="safe",
        obs_runtime=runtime,
    )
    reused = (
        await result_archive.build_and_publish_result_archive_with_runtime(
            inventory,
            agent="analyst",
            summary_markdown="safe",
            obs_runtime=runtime,
        )
    )
    assert reused == key

    def missing(*_args: object, **_kwargs: object) -> int:
        raise OSError("missing")

    def denied(*_args: object, **_kwargs: object) -> str:
        raise OSError("denied")

    monkeypatch.setattr(result_archive, "object_size", missing)
    monkeypatch.setattr(result_archive, "put_object_file", denied)
    with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
        await result_archive.build_and_publish_result_archive_with_runtime(
            inventory,
            agent="analyst",
            summary_markdown="safe",
            obs_runtime=runtime,
        )
    monkeypatch.setattr(
        result_archive, "put_object_file", lambda *_a, **_k: "ok"
    )
    calls = {"n": 0}

    def size_then_none(*_args: object, **_kwargs: object) -> int | None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("missing")
        return None

    monkeypatch.setattr(result_archive, "object_size", size_then_none)
    with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
        await result_archive.build_and_publish_result_archive_with_runtime(
            inventory,
            agent="analyst",
            summary_markdown="safe",
            obs_runtime=runtime,
        )
    monkeypatch.setattr(
        result_archive,
        "iter_object_chunks",
        lambda *_args, **_kwargs: iter((b"ab",)),
    )
    with pytest.raises(ResultArchiveError, match="archive_generation_failed"):
        await result_archive.build_and_publish_result_archive_with_runtime(
            inventory,
            agent="analyst",
            summary_markdown="safe",
            obs_runtime=runtime,
        )

    def down(*_args: object, **_kwargs: object) -> int:
        raise OSError("down")

    monkeypatch.setattr(result_archive, "object_size", down)
    with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
        result_archive._published_archive_size("phytomni", "key", object())
    with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
        await result_archive._published_archive_size_with_runtime(
            "phytomni",
            "key",
            obs_runtime=CountingObsRuntime(object()),
        )
