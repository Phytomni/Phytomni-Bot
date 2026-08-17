# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Branch edges for terminal artifact listing and manifest ingestion."""

# pylint: disable=protected-access

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from mcp_server_phytomni.runtime import terminal_artifacts
from mcp_server_phytomni.runtime.artifact_roles import (
    ARTIFACT_MANIFEST_FILENAME,
    ArtifactRole,
    ClassifiedArtifact,
)
from mcp_server_phytomni.runtime.terminal_artifacts import (
    ManifestLoader,
    TerminalArtifactSet,
    collect_terminal_artifact_set,
    collect_terminal_artifacts,
)
from mcp_server_phytomni.storage.artifact_listing import ListedArtifactObject

pytestmark = pytest.mark.unit


def _listed(
    relative_path: str,
    *,
    source_path: str = "",
    size_bytes: int = 32,
    download_ref: str | None = "download://item",
) -> ListedArtifactObject:
    """Build one listed object for structured listing tests."""
    return ListedArtifactObject(
        relative_path=relative_path,
        source_path=source_path or f"/private/{relative_path}",
        size_bytes=size_bytes,
        download_ref=download_ref,
    )


def _classified(name: str) -> ClassifiedArtifact:
    """Build one classified artifact for public projection tests."""
    return ClassifiedArtifact(
        source_path=f"/private/{name}",
        relative_path=name,
        role=ArtifactRole.SCIENTIFIC_TEXT,
        media_type="text/plain",
        size_bytes=8,
        download_ref=f"download://{name}",
    )


def test_terminal_artifact_set_projects_public_descriptors() -> None:
    """Public projection drops private source paths."""
    public = TerminalArtifactSet(
        artifacts=(_classified("notes.txt"),),
        warnings=(),
    ).to_public()

    assert len(public) == 1
    assert public[0].name == "notes.txt"
    assert public[0].role == ArtifactRole.SCIENTIFIC_TEXT.value
    assert public[0].download_ref == "download://notes.txt"


async def test_default_listers_forward_bucket_and_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default path and object listers lease the configured OBS runtime."""
    seen: dict[str, Any] = {}

    async def _paths(
        output_dir: str, *, bucket_name: str, obs_runtime: Any
    ) -> list[str]:
        seen["paths"] = (output_dir, bucket_name, obs_runtime)
        return [f"{output_dir}/a.txt"]

    async def _objects(
        output_dir: str, *, bucket_name: str, obs_runtime: Any
    ) -> list[ListedArtifactObject]:
        seen["objects"] = (output_dir, bucket_name, obs_runtime)
        return [_listed("a.txt")]

    monkeypatch.setattr(
        terminal_artifacts, "list_artifact_paths_with_runtime", _paths
    )
    monkeypatch.setattr(
        terminal_artifacts, "list_artifact_objects_with_runtime", _objects
    )

    def _runtime() -> str:
        return "runtime"

    def _config() -> Any:
        return type("Cfg", (), {"BUCKET_NAME": "phytomni"})()

    monkeypatch.setattr(terminal_artifacts, "current_obs_runtime", _runtime)
    monkeypatch.setattr(terminal_artifacts, "ServerConfig", _config)

    assert await terminal_artifacts._default_artifact_lister("out") == [
        "out/a.txt"
    ]
    listed = await terminal_artifacts._default_artifact_object_lister("out")
    assert listed[0].relative_path == "a.txt"
    assert seen["paths"] == ("out", "phytomni", "runtime")
    assert seen["objects"] == ("out", "phytomni", "runtime")


async def test_collect_set_degrades_when_listing_fails() -> None:
    """A listing exception becomes a retryable warning and empty set."""

    async def _boom(_output_dir: str) -> list[ListedArtifactObject]:
        raise OSError("obs down")

    async def _no_manifest(_dir: str) -> None:
        del _dir
        return None

    result = await collect_terminal_artifact_set(
        task_id="task-1",
        output_dir="owner/out",
        lister=_boom,
        manifest_loader=_no_manifest,
    )

    assert result.artifacts == ()
    assert result.warnings[0].code == "artifact_listing_failed"
    assert result.warnings[0].retryable is True


async def test_collect_set_truncates_over_cap_listing() -> None:
    """Over-cap listings keep the first N objects and warn once."""

    async def _many(_output_dir: str) -> list[ListedArtifactObject]:
        return [_listed(f"f{index}.txt") for index in range(5)]

    async def _no_manifest(_dir: str) -> None:
        del _dir
        return None

    result = await collect_terminal_artifact_set(
        task_id="task-1",
        output_dir="owner/out",
        lister=_many,
        manifest_loader=_no_manifest,
        cap=2,
    )

    assert len(result.artifacts) == 2
    assert result.warnings[0].code == "artifact_listing_truncated"


async def test_collect_set_loads_default_manifest(tmp_path: Path) -> None:
    """Omitting a loader reads the listed manifest through the default path."""
    manifest = tmp_path / ARTIFACT_MANIFEST_FILENAME
    manifest.write_text(
        json.dumps(
            {
                "version": "1.0",
                "artifacts": [
                    {
                        "path": "summary.txt",
                        "role": "scientific_text",
                        "media_type": "text/plain",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    async def _listed_objects(
        _output_dir: str,
    ) -> list[ListedArtifactObject]:
        return [
            _listed(
                ARTIFACT_MANIFEST_FILENAME,
                source_path=str(manifest),
                size_bytes=manifest.stat().st_size,
            ),
            _listed("summary.txt", source_path=str(tmp_path / "summary.txt")),
        ]

    result = await collect_terminal_artifact_set(
        task_id="task-1",
        output_dir=str(tmp_path),
        lister=_listed_objects,
    )

    roles = {item.relative_path: item.role for item in result.artifacts}
    assert roles["summary.txt"] is ArtifactRole.SCIENTIFIC_TEXT


async def test_collect_set_accepts_sync_manifest_loader() -> None:
    """A non-awaitable loader still supplies the producer manifest."""

    async def _listed_objects(
        _output_dir: str,
    ) -> list[ListedArtifactObject]:
        return [_listed("summary.txt")]

    def _loader(_output_dir: str) -> dict[str, Any]:
        return {
            "version": "1.0",
            "artifacts": [
                {
                    "path": "summary.txt",
                    "role": "scientific_text",
                    "media_type": "text/plain",
                }
            ],
        }

    result = await collect_terminal_artifact_set(
        task_id="task-1",
        output_dir="owner/out",
        lister=_listed_objects,
        manifest_loader=cast(ManifestLoader, _loader),
    )

    assert result.artifacts[0].role is ArtifactRole.SCIENTIFIC_TEXT


async def test_collect_set_invalidates_failed_manifest_loader() -> None:
    """A loader exception degrades to the invalid-manifest sentinel."""

    async def _listed_objects(
        _output_dir: str,
    ) -> list[ListedArtifactObject]:
        return [_listed("summary.txt")]

    async def _boom(_output_dir: str) -> dict[str, Any]:
        raise ValueError("bad manifest")

    result = await collect_terminal_artifact_set(
        task_id="task-1",
        output_dir="owner/out",
        lister=_listed_objects,
        manifest_loader=_boom,
    )

    assert result.artifacts[0].role is ArtifactRole.UNKNOWN
    assert result.warnings[0].code == "artifact_manifest_invalid"


def test_structured_collect_requires_task_and_output_dir() -> None:
    """The structured seam rejects a missing task or output directory."""
    with pytest.raises(ValueError, match="task_id and output_dir"):
        cast(Any, collect_terminal_artifacts)(task_id="task-1")


async def test_load_manifest_from_objects_handles_missing_and_oversize(
    tmp_path: Path,
) -> None:
    """Missing manifests stay None; oversized listed objects fail closed."""
    missing = await terminal_artifacts._load_manifest_from_objects(
        str(tmp_path),
        [_listed("summary.txt")],
    )
    assert missing is None

    with pytest.raises(ValueError, match="exceeds size cap"):
        await terminal_artifacts._load_manifest_from_objects(
            str(tmp_path),
            [
                _listed(
                    ARTIFACT_MANIFEST_FILENAME,
                    size_bytes=terminal_artifacts._MAX_MANIFEST_BYTES + 1,
                )
            ],
        )


async def test_load_manifest_rejects_oversize_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A listed size under the cap still fails when bytes exceed it."""
    manifest = tmp_path / ARTIFACT_MANIFEST_FILENAME
    manifest.write_bytes(b"x")

    async def _huge(_output_dir: str, _item: ListedArtifactObject) -> bytes:
        return b"x" * (terminal_artifacts._MAX_MANIFEST_BYTES + 1)

    monkeypatch.setattr(terminal_artifacts, "_read_manifest_bytes", _huge)
    with pytest.raises(ValueError, match="exceeds size cap"):
        await terminal_artifacts._load_manifest_from_objects(
            str(tmp_path),
            [
                _listed(
                    ARTIFACT_MANIFEST_FILENAME,
                    source_path=str(manifest),
                    size_bytes=1,
                )
            ],
        )


async def test_read_manifest_bytes_from_local_file(tmp_path: Path) -> None:
    """A local mount path is read directly without OBS download."""
    manifest = tmp_path / ARTIFACT_MANIFEST_FILENAME
    manifest.write_bytes(b'{"version":"1.0"}')
    content = await terminal_artifacts._read_manifest_bytes(
        str(tmp_path),
        _listed(
            ARTIFACT_MANIFEST_FILENAME,
            source_path=str(manifest),
            size_bytes=manifest.stat().st_size,
        ),
    )
    assert content == b'{"version":"1.0"}'


async def test_read_manifest_bytes_requires_download_ref(
    tmp_path: Path,
) -> None:
    """A remote-only object without a download ref fails closed."""
    with pytest.raises(OSError, match="download reference unavailable"):
        await terminal_artifacts._read_manifest_bytes(
            str(tmp_path),
            _listed(
                ARTIFACT_MANIFEST_FILENAME,
                source_path=str(tmp_path / "missing.json"),
                download_ref=None,
            ),
        )


async def test_read_manifest_bytes_downloads_remote_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing local files fall back to a bounded OBS download."""
    downloaded = tmp_path / "downloaded.json"
    downloaded.write_bytes(b'{"version":"1.0"}')

    async def _download(reference: str, temp_dir: str) -> str:
        assert reference == "obs://manifest"
        assert temp_dir == terminal_artifacts._MANIFEST_TEMP_DIR
        return str(downloaded)

    monkeypatch.setattr(terminal_artifacts, "download_obs_file", _download)
    content = await terminal_artifacts._read_manifest_bytes(
        str(tmp_path),
        _listed(
            ARTIFACT_MANIFEST_FILENAME,
            source_path=str(tmp_path / "missing.json"),
            download_ref="obs://manifest",
        ),
    )
    assert content == b'{"version":"1.0"}'


def test_unique_json_object_rejects_duplicate_keys() -> None:
    """Duplicate manifest keys fail instead of last-wins."""
    payload = terminal_artifacts._unique_json_object(
        [("version", "1.0"), ("artifacts", [])]
    )
    assert payload == {"version": "1.0", "artifacts": []}

    with pytest.raises(ValueError, match="duplicate manifest key"):
        json.loads(
            '{"a":1,"a":2}',
            object_pairs_hook=terminal_artifacts._unique_json_object,
        )
