# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Edge coverage for result-archive validation and publish failures."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

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
    build_and_publish_result_archive,
    build_and_publish_result_archive_with_runtime,
    build_result_archive_inventory,
    inventory_digest,
    validate_result_archive_inventory,
)
from mcp_server_phytomni.runtime.run_registry_reports import (
    ReportArtifactGroup,
)
from mcp_server_phytomni.runtime.terminal_artifacts import TerminalArtifactSet

pytestmark = pytest.mark.unit


def _artifact(
    path: str = "report.md",
    *,
    role: ArtifactRole = ArtifactRole.SCIENTIFIC_DATA,
    media_type: str = "text/plain",
    size: int = 3,
    download_ref: str | None = "/obs/phytomni/runs/r/report.md",
) -> ClassifiedArtifact:
    """Build one classified artifact for inventory-edge cases."""
    return ClassifiedArtifact(
        source_path=f"private/{path}",
        relative_path=path,
        role=role,
        media_type=media_type,
        size_bytes=size,
        download_ref=download_ref,
    )


def _group(
    *artifacts: ClassifiedArtifact,
    output_dir: object = "/obs/phytomni/runs/r/part-001",
    warnings: tuple[ExecutionWarning, ...] = (),
) -> ReportArtifactGroup:
    """Build one report group, including invalid output directories."""
    return ReportArtifactGroup(
        task_id="task-1",
        output_dir=cast(str, output_dir),
        artifact_set=TerminalArtifactSet(
            artifacts=artifacts,
            warnings=warnings,
        ),
    )


def _member(
    *,
    child_index: object = 1,
    download_ref: object = "/obs/phytomni/runs/r/report.md",
    archive_path: str = "results/part-001/report.md",
    role: ArtifactRole = ArtifactRole.SCIENTIFIC_DATA,
    media_type: object = "text/plain",
    size_bytes: object = 3,
) -> ResultArchiveMember:
    """Build one member, including scalar types validate must reject."""
    return ResultArchiveMember(
        cast(int, child_index),
        cast(str, download_ref),
        archive_path,
        role,
        cast(str, media_type),
        cast(int, size_bytes),
    )


def _inventory(
    members: tuple[ResultArchiveMember, ...] | None = None,
    *,
    run_root: object = "/obs/phytomni/runs/r",
    digest: str | None = None,
    total_size_bytes: int | None = None,
) -> ResultArchiveInventory:
    """Build one inventory around optional invalid header fields."""
    frozen = members if members is not None else (_member(),)
    return ResultArchiveInventory(
        cast(str, run_root),
        frozen,
        digest or inventory_digest(frozen),
        (
            total_size_bytes
            if total_size_bytes is not None
            else sum(item.size_bytes for item in frozen)
        ),
    )


def _patch_publish(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    *,
    chunks=None,
    size=None,
    put=None,
) -> None:
    """Install the OBS seams used by both publish entry points."""
    monkeypatch.setattr(result_archive, "ARCHIVE_TEMP_ROOT", tmp_path)
    monkeypatch.setattr(
        result_archive,
        "SERVER_CONFIG",
        SimpleNamespace(BUCKET_NAME="phytomni"),
    )
    monkeypatch.setattr(
        result_archive,
        "iter_object_chunks",
        chunks or (lambda *_args, **_kwargs: iter((b"abc",))),
    )
    monkeypatch.setattr(
        result_archive,
        "object_size",
        size or (lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError())),
    )
    monkeypatch.setattr(
        result_archive,
        "put_object_file",
        put or (lambda *_args, **_kwargs: "ok"),
    )


def test_unknown_error_code_is_rewritten() -> None:
    """Provider-specific codes collapse to the stable contract code."""
    error = ResultArchiveError("not-a-real-code", retryable=True)
    assert error.code == "archive_contract_invalid"
    assert error.retryable is True


def test_inventory_rejects_empty_groups() -> None:
    """A run without child groups has no user deliverables."""
    with pytest.raises(ResultArchiveError, match="no_user_deliverables"):
        build_result_archive_inventory(())


def test_inventory_skips_ineligible_roles() -> None:
    """Diagnostic objects are ignored while eligible files stay."""
    inventory = build_result_archive_inventory(
        (
            _group(
                _artifact("notes.log", role=ArtifactRole.DIAGNOSTIC),
                _artifact("report.md"),
            ),
        )
    )
    assert [item.archive_path for item in inventory.members] == [
        "results/part-001/report.md"
    ]


@pytest.mark.parametrize(
    "artifact",
    [
        _artifact(download_ref=None),
        _artifact(download_ref=""),
        _artifact(media_type=""),
    ],
)
def test_inventory_rejects_blank_source_metadata(
    artifact: ClassifiedArtifact,
) -> None:
    """Eligible files still need a download ref and media type."""
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        build_result_archive_inventory((_group(artifact),))


def test_inventory_rejects_blank_or_split_run_roots() -> None:
    """Every child must share one absolute parent output directory."""
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        build_result_archive_inventory((_group(_artifact(), output_dir=""),))
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        build_result_archive_inventory(
            (
                _group(_artifact(), output_dir="/obs/runs/a/part-001"),
                ReportArtifactGroup(
                    task_id="task-2",
                    output_dir="/obs/runs/b/part-001",
                    artifact_set=TerminalArtifactSet(
                        artifacts=(_artifact("other.md"),),
                        warnings=(),
                    ),
                ),
            )
        )


def test_inventory_maps_listing_failure() -> None:
    """A listing warning becomes the retryable archive listing error."""
    warning = ExecutionWarning("artifact_listing_failed", True, "listing")
    with pytest.raises(ResultArchiveError, match="artifact_listing_failed"):
        build_result_archive_inventory(
            (_group(_artifact(), warnings=(warning,)),)
        )


def test_inventory_rejects_backslash_relative_path() -> None:
    """Windows separators are not valid ZIP member names."""
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        build_result_archive_inventory((_group(_artifact("dir\\file.md")),))


@pytest.mark.parametrize(
    "run_root",
    ["", None, r"/obs/foo\bar", "relative/root", "/obs/foo/../r"],
)
def test_validate_rejects_unsafe_run_root(run_root: object) -> None:
    """Persisted inventories cannot carry an unsafe or empty run root."""
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        validate_result_archive_inventory(_inventory(run_root=run_root))


def test_validate_rejects_empty_or_oversized_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty and over-limit inventories fail before member walks."""
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        validate_result_archive_inventory(_inventory(members=()))
    monkeypatch.setattr(result_archive, "MAX_RESULT_ARCHIVE_ARTIFACTS", 0)
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        validate_result_archive_inventory(_inventory())


def test_validate_rejects_ineligible_or_duplicate_members() -> None:
    """Only eligible unique archive paths may be persisted."""
    ineligible = _inventory(
        (_member(role=ArtifactRole.INPUT),),
    )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        validate_result_archive_inventory(ineligible)
    duplicate = _inventory((_member(), _member()))
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        validate_result_archive_inventory(duplicate)


def test_validate_rejects_size_and_digest_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persisted totals and digests must match the recomputed values."""
    monkeypatch.setattr(
        result_archive,
        "MAX_RESULT_ARCHIVE_UNCOMPRESSED_BYTES",
        0,
    )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        validate_result_archive_inventory(_inventory())
    monkeypatch.setattr(
        result_archive,
        "MAX_RESULT_ARCHIVE_UNCOMPRESSED_BYTES",
        10 * 1024**3,
    )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        validate_result_archive_inventory(
            _inventory(digest="sha256:" + "0" * 64)
        )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        validate_result_archive_inventory(_inventory(total_size_bytes=99))


@pytest.mark.parametrize(
    "member",
    [
        _member(child_index=True),
        _member(child_index=0),
        _member(download_ref=""),
        _member(media_type=""),
        _member(size_bytes=True),
        _member(size_bytes=-1),
        _member(archive_path="results/part-002/report.md"),
        _member(archive_path="results/part-001/summary.md"),
    ],
)
def test_validate_rejects_malformed_member_scalars(
    member: ResultArchiveMember,
) -> None:
    """Each persisted scalar is rechecked without coercion."""
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        validate_result_archive_inventory(_inventory((member,)))


@pytest.mark.parametrize("agent", ["", "bad-agent", 12])
def test_publish_rejects_invalid_agent(agent: object) -> None:
    """Archive object keys only accept alphanumeric agent names."""
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        build_and_publish_result_archive(
            _inventory(),
            agent=cast(str, agent),
            summary_markdown="ok",
            client=object(),
        )


def test_publish_rejects_non_string_summary() -> None:
    """The generated summary member must be a string."""
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        build_and_publish_result_archive(
            _inventory(),
            agent="analyst",
            summary_markdown=cast(str, 1),
            client=object(),
        )


def test_publish_maps_invalid_stream_chunk(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-bytes OBS chunk fails generation before upload."""
    _patch_publish(
        monkeypatch,
        tmp_path,
        chunks=lambda *_args, **_kwargs: iter(("abc",)),
    )
    with pytest.raises(ResultArchiveError, match="archive_generation_failed"):
        build_and_publish_result_archive(
            _inventory(),
            agent="analyst",
            summary_markdown="ok",
            client=object(),
        )


def test_publish_rejects_existing_size_mismatch(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A digest-addressed object with the wrong size is not reused."""
    _patch_publish(
        monkeypatch,
        tmp_path,
        size=lambda *_args, **_kwargs: 1,
    )
    with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
        build_and_publish_result_archive(
            _inventory(),
            agent="analyst",
            summary_markdown="ok",
            client=object(),
        )


def test_publish_maps_put_oserror(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transport failure during PUT stays a retryable publish error."""

    def _fail_put(*_args: object, **_kwargs: object) -> str:
        raise OSError("put failed")

    _patch_publish(monkeypatch, tmp_path, put=_fail_put)
    with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
        build_and_publish_result_archive(
            _inventory(),
            agent="analyst",
            summary_markdown="ok",
            client=object(),
        )


def test_publish_maps_published_size_oserror(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HEAD after PUT maps transport failure to a retryable publish error."""
    calls = 0

    def _size(*_args: object, **_kwargs: object) -> int:
        nonlocal calls
        calls += 1
        raise OSError("missing" if calls == 1 else "head failed")

    _patch_publish(monkeypatch, tmp_path, size=_size)
    with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
        build_and_publish_result_archive(
            _inventory(),
            agent="analyst",
            summary_markdown="ok",
            client=object(),
        )


def test_publish_rejects_published_size_none(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing post-upload size is treated as a publish failure."""
    calls = 0

    def _size(*_args: object, **_kwargs: object) -> int | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("missing")
        return None

    _patch_publish(monkeypatch, tmp_path, size=_size)
    with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
        build_and_publish_result_archive(
            _inventory(),
            agent="analyst",
            summary_markdown="ok",
            client=object(),
        )


def test_publish_maps_scratch_oserror(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scratch-directory failure becomes a retryable generation error."""
    _patch_publish(monkeypatch, tmp_path)

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise OSError("scratch")

    monkeypatch.setattr(result_archive.tempfile, "TemporaryDirectory", _boom)
    with pytest.raises(ResultArchiveError, match="archive_generation_failed"):
        build_and_publish_result_archive(
            _inventory(),
            agent="analyst",
            summary_markdown="ok",
            client=object(),
        )


@pytest.mark.parametrize("agent", ["", "bad-agent", 12])
async def test_async_publish_rejects_invalid_agent(agent: object) -> None:
    """The leased publisher uses the same agent-name contract."""
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        await build_and_publish_result_archive_with_runtime(
            _inventory(),
            agent=cast(str, agent),
            summary_markdown="ok",
            obs_runtime=CountingObsRuntime(object()),
        )


async def test_async_publish_rejects_non_string_summary() -> None:
    """The leased publisher rejects a non-string summary body."""
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        await build_and_publish_result_archive_with_runtime(
            _inventory(),
            agent="analyst",
            summary_markdown=cast(str, None),
            obs_runtime=CountingObsRuntime(object()),
        )


async def test_async_publish_maps_write_value_error(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local ZIP write error stays a retryable generation failure."""
    _patch_publish(monkeypatch, tmp_path)

    def _fail_write(*_args: object, **_kwargs: object) -> None:
        raise ValueError("zip")

    monkeypatch.setattr(
        result_archive,
        "_write_archive_from_sources",
        _fail_write,
    )
    with pytest.raises(ResultArchiveError, match="archive_generation_failed"):
        await build_and_publish_result_archive_with_runtime(
            _inventory(),
            agent="analyst",
            summary_markdown="ok",
            obs_runtime=CountingObsRuntime(object()),
        )


async def test_async_publish_rejects_existing_size_mismatch(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leased HEAD that disagrees with the local ZIP is not reused."""
    _patch_publish(
        monkeypatch,
        tmp_path,
        size=lambda *_args, **_kwargs: 1,
    )
    with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
        await build_and_publish_result_archive_with_runtime(
            _inventory(),
            agent="analyst",
            summary_markdown="ok",
            obs_runtime=CountingObsRuntime(object()),
        )


async def test_async_publish_reuses_matching_existing_object(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leased HEAD that matches the local ZIP returns the object key."""
    uploaded: dict[str, bytes] = {}
    sizes: dict[str, int] = {}

    def _put(_bucket: str, key: str, source, **_kwargs: object) -> str:
        uploaded[key] = source.read_bytes()
        sizes[key] = len(uploaded[key])
        return key

    def _size(_bucket: str, key: str, **_kwargs: object) -> int:
        if key not in sizes:
            raise OSError("missing")
        return sizes[key]

    _patch_publish(monkeypatch, tmp_path, size=_size, put=_put)
    first = await build_and_publish_result_archive_with_runtime(
        _inventory(),
        agent="analyst",
        summary_markdown="ok",
        obs_runtime=CountingObsRuntime(object()),
    )
    reused = await build_and_publish_result_archive_with_runtime(
        _inventory(),
        agent="analyst",
        summary_markdown="ok",
        obs_runtime=CountingObsRuntime(object()),
    )
    assert reused == first


async def test_async_publish_maps_put_oserror(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leased PUT transport failure is a retryable publish error."""

    def _fail_put(*_args: object, **_kwargs: object) -> str:
        raise OSError("put failed")

    _patch_publish(monkeypatch, tmp_path, put=_fail_put)
    with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
        await build_and_publish_result_archive_with_runtime(
            _inventory(),
            agent="analyst",
            summary_markdown="ok",
            obs_runtime=CountingObsRuntime(object()),
        )


async def test_async_publish_maps_published_size_oserror(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leased post-PUT HEAD failure stays retryable."""
    calls = 0

    def _size(*_args: object, **_kwargs: object) -> int:
        nonlocal calls
        calls += 1
        raise OSError("missing" if calls == 1 else "head failed")

    _patch_publish(monkeypatch, tmp_path, size=_size)
    with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
        await build_and_publish_result_archive_with_runtime(
            _inventory(),
            agent="analyst",
            summary_markdown="ok",
            obs_runtime=CountingObsRuntime(object()),
        )


async def test_async_publish_rejects_published_size_none(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leased post-PUT HEAD that returns no size fails closed."""
    calls = 0

    def _size(*_args: object, **_kwargs: object) -> int | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("missing")
        return None

    _patch_publish(monkeypatch, tmp_path, size=_size)
    with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
        await build_and_publish_result_archive_with_runtime(
            _inventory(),
            agent="analyst",
            summary_markdown="ok",
            obs_runtime=CountingObsRuntime(object()),
        )


async def test_async_publish_rejects_published_size_mismatch(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leased post-PUT size that disagrees with the ZIP is rejected."""
    calls = 0

    def _size(*_args: object, **_kwargs: object) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("missing")
        return 1

    _patch_publish(monkeypatch, tmp_path, size=_size)
    with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
        await build_and_publish_result_archive_with_runtime(
            _inventory(),
            agent="analyst",
            summary_markdown="ok",
            obs_runtime=CountingObsRuntime(object()),
        )


async def test_async_publish_maps_scratch_oserror(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leased publication maps scratch failures to generation errors."""
    _patch_publish(monkeypatch, tmp_path)

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise OSError("scratch")

    monkeypatch.setattr(result_archive.tempfile, "TemporaryDirectory", _boom)
    with pytest.raises(ResultArchiveError, match="archive_generation_failed"):
        await build_and_publish_result_archive_with_runtime(
            _inventory(),
            agent="analyst",
            summary_markdown="ok",
            obs_runtime=CountingObsRuntime(object()),
        )


async def test_async_publish_rejects_downloaded_size_mismatch(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Downloaded member bytes must match the inventory size."""
    _patch_publish(
        monkeypatch,
        tmp_path,
        chunks=lambda *_args, **_kwargs: iter((b"ab",)),
    )
    with pytest.raises(ResultArchiveError, match="archive_generation_failed"):
        await build_and_publish_result_archive_with_runtime(
            _inventory(),
            agent="analyst",
            summary_markdown="ok",
            obs_runtime=CountingObsRuntime(object()),
        )
