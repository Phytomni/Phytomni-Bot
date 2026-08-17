# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for ``runtime.terminal_artifacts``.

``collect_terminal_artifacts`` walks the reconciled task_results blob
that ``_terminal_payload`` produces and emits one artifact descriptor
per succeeded task that carries an ``output_dir``. Its ``paths`` field
is populated from a prior ``enumerate_artifact_paths`` pass and stays
empty only when that pass has not run (e.g. ``GetTaskStatus``).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.analyst.graph import AnalystGraphMixin
from mcp_server_phytomni.runtime import terminal_artifacts
from mcp_server_phytomni.runtime.artifact_roles import ArtifactRole
from mcp_server_phytomni.runtime.terminal_artifacts import (
    ArtifactLister,
    TerminalArtifactSet,
    collect_terminal_artifacts,
)
from mcp_server_phytomni.storage.artifact_listing import ListedArtifactObject

pytestmark = pytest.mark.unit


@pytest.fixture(name="artifacts_caplog")
def _artifacts_caplog(
    caplog: pytest.LogCaptureFixture,
) -> Iterator[pytest.LogCaptureFixture]:
    """Capture warnings from the terminal_artifacts logger.

    ``common/logging_config.configure_logging`` sets the package logger
    ``propagate=False``, so once an earlier suite test triggers it (e.g.
    via an api/app import) warnings never reach pytest's root-attached
    caplog. Attaching the caplog handler directly to this module's
    logger sidesteps the propagation gap; it is removed at teardown.
    """
    module_logger = terminal_artifacts.logger
    module_logger.addHandler(caplog.handler)
    module_logger.setLevel(logging.WARNING)
    try:
        yield caplog
    finally:
        module_logger.removeHandler(caplog.handler)


def test_collects_succeeded_task_with_output_dir() -> None:
    """One succeeded row with output_dir becomes one artifact entry."""
    rows = [
        {"task_id": "t-1", "status": "succeeded", "output_dir": "/obs/x"},
    ]
    assert collect_terminal_artifacts(rows) == [
        {"task_id": "t-1", "output_dir": "/obs/x", "paths": []},
    ]


def test_skips_failed_tasks() -> None:
    """A failed task contributes no artifact descriptor.

    The artifacts index advertises product paths to clients; failed
    tasks have no products to point at, so emitting an entry would be
    misleading.
    """
    rows = [
        {"task_id": "t-2", "status": "failed", "output_dir": "/obs/y"},
    ]
    assert not collect_terminal_artifacts(rows)


def test_skips_succeeded_without_output_dir() -> None:
    """A succeeded row missing output_dir is skipped, not crashed.

    Defensive against synthesized rows (e.g. retry pathways) that have
    a terminal status but no path field; the helper must stay
    type-safe so the terminal-write hot path never raises.
    """
    rows = [{"task_id": "t-3", "status": "completed"}]
    assert not collect_terminal_artifacts(rows)


def test_accepts_full_success_vocabulary() -> None:
    """Every success-like status maps to an artifact entry.

    Mirrors ``_SUCCESS_STATUSES`` in ``runtime/run_registry`` so the
    index does not silently drop ``success`` / ``completed`` / ``done``
    rows on backends that use those instead of ``succeeded``.
    """
    rows = [
        {"task_id": "a", "status": "succeeded", "output_dir": "/a"},
        {"task_id": "b", "status": "success", "output_dir": "/b"},
        {"task_id": "c", "status": "completed", "output_dir": "/c"},
        {"task_id": "d", "status": "done", "output_dir": "/d"},
    ]
    result = collect_terminal_artifacts(rows)
    assert [entry["task_id"] for entry in result] == ["a", "b", "c", "d"]


def test_mixed_batch_filters_correctly() -> None:
    """A batch with mixed statuses surfaces only the succeeded artifacts."""
    rows = [
        {"task_id": "t-1", "status": "succeeded", "output_dir": "/a"},
        {"task_id": "t-2", "status": "failed", "output_dir": "/b"},
        {"task_id": "t-3", "status": "running", "output_dir": "/c"},
        {"task_id": "t-4", "status": "done", "output_dir": "/d"},
    ]
    assert collect_terminal_artifacts(rows) == [
        {"task_id": "t-1", "output_dir": "/a", "paths": []},
        {"task_id": "t-4", "output_dir": "/d", "paths": []},
    ]


def test_empty_input_returns_empty_list() -> None:
    """No tasks means no artifacts; the helper must accept the empty case."""
    assert not collect_terminal_artifacts([])


@pytest.mark.asyncio
async def test_enumerate_fills_paths_for_succeeded_rows() -> None:
    """Only succeeded rows with an output_dir gain ``artifact_paths``."""
    live = [
        {"task_id": "t1", "status": "succeeded", "output_dir": "/obs/p/r1"},
        {"task_id": "t2", "status": "running", "output_dir": "/obs/p/r2"},
        {"task_id": "t3", "status": "succeeded", "output_dir": ""},
    ]

    async def lister(output_dir: str) -> list[str]:
        return [f"{output_dir}/fig.png"]

    typed_lister: ArtifactLister = lister

    out = await terminal_artifacts.enumerate_artifact_paths(
        live, lister=typed_lister
    )

    assert out[0]["artifact_paths"] == ["/obs/p/r1/fig.png"]
    assert "artifact_paths" not in out[1]  # not succeeded -> untouched
    assert out[2].get("artifact_paths", []) == []  # no output_dir -> skipped


@pytest.mark.asyncio
async def test_enumerate_swallows_lister_errors(
    artifacts_caplog: pytest.LogCaptureFixture,
) -> None:
    """A listing failure degrades to empty paths and logs, never raises."""
    live = [
        {"task_id": "t1", "status": "succeeded", "output_dir": "/obs/p/r1"},
    ]

    async def boom(output_dir: str) -> list[str]:
        raise OSError(f"obs down for {output_dir}")

    typed_lister: ArtifactLister = boom

    out = await terminal_artifacts.enumerate_artifact_paths(
        live, lister=typed_lister
    )

    assert out[0]["artifact_paths"] == []
    assert any("t1" in rec.message for rec in artifacts_caplog.records)


@pytest.mark.asyncio
async def test_enumerate_caps_and_logs_truncation(
    artifacts_caplog: pytest.LogCaptureFixture,
) -> None:
    """Over-cap results are truncated with a non-silent warning."""
    live = [
        {"task_id": "t1", "status": "succeeded", "output_dir": "/obs/p/r1"},
    ]

    async def many(output_dir: str) -> list[str]:
        return [f"{output_dir}/f{i}.png" for i in range(5)]

    typed_lister: ArtifactLister = many

    out = await terminal_artifacts.enumerate_artifact_paths(
        live, lister=typed_lister, cap=2
    )

    assert len(out[0]["artifact_paths"]) == 2
    assert any(
        "truncated" in rec.message.lower() for rec in artifacts_caplog.records
    )


def test_collect_reads_enumerated_paths() -> None:
    """``collect_terminal_artifacts`` surfaces a prior enumeration."""
    live = [
        {
            "task_id": "t1",
            "status": "succeeded",
            "output_dir": "/obs/p/r1",
            "artifact_paths": ["/obs/p/r1/fig.png"],
        }
    ]
    artifacts = collect_terminal_artifacts(live)
    assert artifacts[0]["paths"] == ["/obs/p/r1/fig.png"]


def test_submit_meta_keeps_manifest_contract_without_zip_authority() -> None:
    """Producers declare artifacts but never construct the Bot archive."""
    submit_meta = getattr(AnalystGraphMixin, "_submit_meta")
    prompt = submit_meta(cast(Any, {"plan": "plan", "tool_usages": "tools"}))
    normalized_prompt = " ".join(prompt.split())

    assert ".phytomni-artifacts.json" in prompt
    assert "scientific_data" in prompt
    assert "`result_archive` is reserved for Bot" in normalized_prompt
    assert "zip -r" not in prompt


def _listed_object(relative_path: str) -> ListedArtifactObject:
    """Build one listed object with private provenance for structured tests."""
    return ListedArtifactObject(
        relative_path=relative_path,
        source_path=f"/private/obsfs/{relative_path}",
        size_bytes=1024,
        download_ref=f"/obs/phytomni/run/{relative_path}",
    )


def _manifest_item(path: str) -> dict[str, str]:
    """Build one manifest declaration for a negative-path test."""
    return {
        "path": path,
        "role": "scientific_text",
        "media_type": "text/plain",
    }


@pytest.mark.asyncio
async def test_unknown_is_listed_but_not_report_eligible() -> None:
    """Unmanifested files remain visible while staying out of reports."""

    async def fake_objects(_output_dir: str) -> list[ListedArtifactObject]:
        return [_listed_object("summary.csv"), _listed_object("analysis.log")]

    async def missing_manifest(
        _output_dir: str,
    ) -> None:
        return None

    result = await collect_terminal_artifacts(
        task_id="task-1",
        output_dir="owner/out",
        lister=fake_objects,
        manifest_loader=missing_manifest,
    )

    assert isinstance(result, TerminalArtifactSet)
    assert [item.role for item in result.artifacts] == [
        ArtifactRole.UNKNOWN,
        ArtifactRole.UNKNOWN,
    ]
    assert not any(item.report_context_eligible for item in result.artifacts)
    assert result.warnings[0].code == "artifact_manifest_missing"


@pytest.mark.parametrize(
    "bad_path",
    ["/etc/passwd", "../escape.txt", "a/../../b.txt", "a\\b.txt"],
)
@pytest.mark.asyncio
async def test_manifest_path_escape_fails_closed(bad_path: str) -> None:
    """Unsafe manifest paths leave every listed object unknown."""

    async def fake_objects(_output_dir: str) -> list[ListedArtifactObject]:
        return [_listed_object("summary.csv")]

    async def manifest_loader(_output_dir: str) -> dict[str, Any]:
        return {
            "version": "1.0",
            "artifacts": [_manifest_item(bad_path)],
        }

    result = await collect_terminal_artifacts(
        task_id="task-1",
        output_dir="owner/out",
        lister=fake_objects,
        manifest_loader=manifest_loader,
    )

    assert isinstance(result, TerminalArtifactSet)
    assert all(item.role is ArtifactRole.UNKNOWN for item in result.artifacts)
    assert result.warnings[0].code == "artifact_manifest_invalid"


@pytest.mark.asyncio
async def test_result_archive_manifest_role_fails_closed() -> None:
    """A producer cannot elevate an object to the Bot-owned archive role."""

    async def fake_objects(_output_dir: str) -> list[ListedArtifactObject]:
        return [_listed_object("results.zip")]

    async def manifest_loader(_output_dir: str) -> dict[str, Any]:
        return {
            "version": "1.0",
            "artifacts": [
                {
                    "path": "results.zip",
                    "role": "result_archive",
                    "media_type": "application/zip",
                }
            ],
        }

    result = await collect_terminal_artifacts(
        task_id="task-1",
        output_dir="owner/out",
        lister=fake_objects,
        manifest_loader=manifest_loader,
    )

    assert result.artifacts[0].role is ArtifactRole.UNKNOWN
    assert result.warnings[0].code == "artifact_manifest_invalid"


def test_artifact_set_to_public_drops_source_path() -> None:
    """Public projection keeps the download ref and hides the source path."""
    from mcp_server_phytomni.runtime.artifact_roles import ClassifiedArtifact

    public = TerminalArtifactSet(
        artifacts=(
            ClassifiedArtifact(
                source_path="/private/obsfs/report.md",
                relative_path="report.md",
                role=ArtifactRole.SCIENTIFIC_REPORT,
                media_type="text/markdown",
                size_bytes=12,
                download_ref="/obs/phytomni/run/report.md",
            ),
        ),
        warnings=(),
    ).to_public()
    assert public[0].name == "report.md"
    assert public[0].download_ref == "/obs/phytomni/run/report.md"


@pytest.mark.asyncio
async def test_structured_listing_failure_returns_warning() -> None:
    """A structured listing OSError degrades to one retryable warning."""

    async def boom(_output_dir: str) -> list[ListedArtifactObject]:
        raise OSError("list failed")

    result = await collect_terminal_artifacts(
        task_id="task-1", output_dir="owner/out", lister=boom
    )
    assert result.artifacts == ()
    assert result.warnings[0].code == "artifact_listing_failed"


@pytest.mark.asyncio
async def test_structured_listing_truncates_and_sync_manifest_loader() -> None:
    """Over-cap listings warn and a sync loader is accepted without await."""

    async def many(_output_dir: str) -> list[ListedArtifactObject]:
        return [
            _listed_object("b.md"),
            _listed_object("a.md"),
            _listed_object("c.md"),
        ]

    from mcp_server_phytomni.runtime.terminal_artifacts import (
        collect_terminal_artifact_set,
    )

    result = await collect_terminal_artifact_set(
        task_id="task-1",
        output_dir="owner/out",
        lister=many,
        manifest_loader=lambda _path: None,
        cap=1,
    )
    assert result.artifacts[0].relative_path == "a.md"
    assert any(
        warning.code == "artifact_listing_truncated"
        for warning in result.warnings
    )


@pytest.mark.asyncio
async def test_manifest_loader_exception_fails_closed() -> None:
    """A loader exception becomes an invalid manifest, not a raised error."""

    async def one(_output_dir: str) -> list[ListedArtifactObject]:
        return [_listed_object("report.md")]

    async def boom(_output_dir: str) -> None:
        raise RuntimeError("manifest down")

    result = await collect_terminal_artifacts(
        task_id="task-1",
        output_dir="owner/out",
        lister=one,
        manifest_loader=boom,
    )
    assert result.artifacts[0].role is ArtifactRole.UNKNOWN
    assert result.warnings[0].code == "artifact_manifest_invalid"


def test_structured_collect_requires_task_identity() -> None:
    """Structured collection without task_id and output_dir fails fast."""
    with pytest.raises(ValueError, match="task_id and output_dir"):
        collect_terminal_artifacts(lister=lambda _path: [])


@pytest.mark.asyncio
async def test_default_listers_use_storage_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default path and object listers forward the current OBS runtime."""

    async def fake_paths(output_dir: str, **kwargs: Any) -> list[str]:
        assert kwargs["obs_runtime"] == "runtime"
        return [f"{output_dir}/report.md"]

    async def fake_objects(
        output_dir: str, **kwargs: Any
    ) -> list[ListedArtifactObject]:
        del output_dir
        assert kwargs["obs_runtime"] == "runtime"
        return [_listed_object("report.md")]

    monkeypatch.setattr(
        terminal_artifacts, "current_obs_runtime", lambda: "runtime"
    )
    monkeypatch.setattr(
        terminal_artifacts, "list_artifact_paths_with_runtime", fake_paths
    )
    monkeypatch.setattr(
        terminal_artifacts, "list_artifact_objects_with_runtime", fake_objects
    )
    live = [
        {"task_id": "t1", "status": "succeeded", "output_dir": "owner/out"}
    ]
    enumerated = await terminal_artifacts.enumerate_artifact_paths(live)
    assert enumerated[0]["artifact_paths"] == ["owner/out/report.md"]
    artifact_set = await terminal_artifacts.collect_terminal_artifact_set(
        task_id="t1",
        output_dir="owner/out",
        manifest_loader=lambda _path: None,
    )
    assert artifact_set.artifacts[0].relative_path == "report.md"


@pytest.mark.asyncio
async def test_default_manifest_loader_reads_local_file(tmp_path) -> None:
    """A listed local manifest is parsed when no custom loader is supplied."""
    payload = {
        "version": "1.0",
        "artifacts": [
            {
                "path": "report.md",
                "role": "scientific_report",
                "media_type": "text/markdown",
            }
        ],
    }
    manifest_path = tmp_path / ".phytomni-artifacts.json"
    manifest_path.write_text(
        terminal_artifacts.json.dumps(payload), encoding="utf-8"
    )

    async def listed(_output_dir: str) -> list[ListedArtifactObject]:
        return [
            ListedArtifactObject(
                relative_path=".phytomni-artifacts.json",
                source_path=str(manifest_path),
                size_bytes=manifest_path.stat().st_size,
                download_ref="/obs/phytomni/run/.phytomni-artifacts.json",
            ),
            ListedArtifactObject(
                relative_path="report.md",
                source_path=str(tmp_path / "report.md"),
                size_bytes=12,
                download_ref="/obs/phytomni/run/report.md",
            ),
        ]

    result = await collect_terminal_artifacts(
        task_id="task-1", output_dir="owner/out", lister=listed
    )
    assert result.artifacts[1].role is ArtifactRole.SCIENTIFIC_REPORT


@pytest.mark.asyncio
async def test_manifest_size_cap_and_download_fallback(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Oversize manifests fail closed and missing local files download."""

    async def oversized(_output_dir: str) -> list[ListedArtifactObject]:
        return [
            ListedArtifactObject(
                relative_path=".phytomni-artifacts.json",
                source_path=str(tmp_path / "missing.json"),
                size_bytes=40_000,
                download_ref="/obs/phytomni/run/.phytomni-artifacts.json",
            )
        ]

    result = await collect_terminal_artifacts(
        task_id="task-1", output_dir="owner/out", lister=oversized
    )
    assert result.warnings[0].code == "artifact_manifest_invalid"
    payload = terminal_artifacts.json.dumps(
        {
            "version": "1.0",
            "artifacts": [
                {
                    "path": "report.md",
                    "role": "scientific_report",
                    "media_type": "text/markdown",
                }
            ],
        }
    ).encode()
    downloaded = tmp_path / "downloaded.json"
    downloaded.write_bytes(payload)

    async def fake_download(reference: str, dest_dir: str) -> str:
        assert dest_dir == "terminal-manifest"
        return str(downloaded)

    monkeypatch.setattr(terminal_artifacts, "download_obs_file", fake_download)

    async def remote(_output_dir: str) -> list[ListedArtifactObject]:
        return [
            ListedArtifactObject(
                relative_path=".phytomni-artifacts.json",
                source_path=str(tmp_path / "not-mounted.json"),
                size_bytes=len(payload),
                download_ref="/obs/phytomni/run/.phytomni-artifacts.json",
            )
        ]

    remote_result = await collect_terminal_artifacts(
        task_id="task-1", output_dir="owner/out", lister=remote
    )
    assert remote_result.artifacts[0].role is ArtifactRole.DIAGNOSTIC


@pytest.mark.asyncio
async def test_manifest_download_requires_reference(tmp_path) -> None:
    """A remote manifest without a download ref fails closed."""

    async def listed(_output_dir: str) -> list[ListedArtifactObject]:
        return [
            ListedArtifactObject(
                relative_path=".phytomni-artifacts.json",
                source_path=str(tmp_path / "missing.json"),
                size_bytes=12,
                download_ref=None,
            )
        ]

    result = await collect_terminal_artifacts(
        task_id="task-1", output_dir="owner/out", lister=listed
    )
    assert result.warnings[0].code == "artifact_manifest_invalid"


@pytest.mark.asyncio
async def test_manifest_rejects_duplicate_keys_and_oversize_body(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duplicate JSON keys and post-read oversize bodies fail closed."""
    duplicate = tmp_path / "dup.json"
    duplicate.write_text('{"version":"1.0","version":"2.0"}', encoding="utf-8")

    async def listed(_output_dir: str) -> list[ListedArtifactObject]:
        return [
            ListedArtifactObject(
                relative_path=".phytomni-artifacts.json",
                source_path=str(duplicate),
                size_bytes=duplicate.stat().st_size,
                download_ref="/obs/phytomni/run/.phytomni-artifacts.json",
            )
        ]

    result = await collect_terminal_artifacts(
        task_id="task-1", output_dir="owner/out", lister=listed
    )
    assert result.warnings[0].code == "artifact_manifest_invalid"
    huge = tmp_path / "huge.json"
    huge.write_bytes(b"x" * 40_000)
    monkeypatch.setattr(terminal_artifacts, "_MAX_MANIFEST_BYTES", 10)

    async def huge_listed(_output_dir: str) -> list[ListedArtifactObject]:
        return [
            ListedArtifactObject(
                relative_path=".phytomni-artifacts.json",
                source_path=str(huge),
                size_bytes=8,
                download_ref="/obs/phytomni/run/.phytomni-artifacts.json",
            )
        ]

    huge_result = await collect_terminal_artifacts(
        task_id="task-1", output_dir="owner/out", lister=huge_listed
    )
    assert huge_result.warnings[0].code == "artifact_manifest_invalid"
