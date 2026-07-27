# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Collect terminal artifacts with bounded producer-manifest ingestion.

The positional ``collect_terminal_artifacts(rows)`` call remains a legacy
projection used by synchronous MCP formatting. The keyword form and the
explicit ``collect_terminal_artifact_set`` coroutine perform the new
manifest-backed classification used by run-level terminal assembly.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, overload

from ..config.defaults import ServerConfig
from ..storage.artifact_listing import (
    ListedArtifactObject,
    list_artifact_objects,
    list_artifact_paths,
)
from ..storage.downloads import download_obs_file
from .artifact_roles import (
    ARTIFACT_MANIFEST_FILENAME,
    ArtifactManifest,
    ClassifiedArtifact,
    PublicArtifactDescriptor,
    classify_artifacts,
)
from .execution_models import ExecutionWarning

logger = logging.getLogger(__name__)

__all__ = [
    "ArtifactLister",
    "ArtifactObjectLister",
    "ManifestLoader",
    "TerminalArtifactSet",
    "collect_terminal_artifact_set",
    "collect_terminal_artifacts",
    "enumerate_artifact_paths",
]

_SUCCESS_STATUSES = frozenset({"succeeded", "success", "completed", "done"})
_DEFAULT_PATH_CAP = 200
_MAX_MANIFEST_BYTES = 32_768
_MANIFEST_TEMP_DIR = "terminal-manifest"
_INVALID_MANIFEST: Mapping[str, Any] = {
    "version": "invalid",
    "artifacts": [],
}


ArtifactLister = Callable[[str], Awaitable[list[str]]]
"""Async callable listing public object paths under one output directory."""

ArtifactObjectLister = Callable[[str], Awaitable[list[ListedArtifactObject]]]
"""Async callable listing object metadata under one output directory."""

ManifestLoader = Callable[
    [str], Awaitable[ArtifactManifest | Mapping[str, Any] | None]
]
"""Async callable loading one bounded producer manifest by output directory."""


@dataclass(frozen=True, slots=True)
class TerminalArtifactSet:
    """Classified terminal objects plus safe manifest/listing warnings."""

    artifacts: tuple[ClassifiedArtifact, ...]
    warnings: tuple[ExecutionWarning, ...]

    def to_public(self) -> tuple[PublicArtifactDescriptor, ...]:
        """Project classified artifacts without private source paths."""
        return tuple(artifact.to_public() for artifact in self.artifacts)


async def _default_artifact_lister(output_dir: str) -> list[str]:
    """List public artifact paths through the existing storage helper."""
    config = ServerConfig()
    return await asyncio.to_thread(
        list_artifact_paths,
        output_dir,
        bucket_name=config.BUCKET_NAME,
        obs_server=config.OBS_SERVER,
    )


async def _default_artifact_object_lister(
    output_dir: str,
) -> list[ListedArtifactObject]:
    """List output objects and actual sizes off the event loop."""
    config = ServerConfig()
    return await asyncio.to_thread(
        list_artifact_objects,
        output_dir,
        bucket_name=config.BUCKET_NAME,
        obs_server=config.OBS_SERVER,
    )


async def enumerate_artifact_paths(
    live: list[dict[str, Any]],
    *,
    lister: ArtifactLister | None = None,
    cap: int = _DEFAULT_PATH_CAP,
) -> list[dict[str, Any]]:
    """Attach legacy ``artifact_paths`` to succeeded rows.

    This compatibility path remains deliberately path-only. New terminal
    report assembly should use ``collect_terminal_artifact_set`` so a file is
    not admitted by extension without a producer manifest role.
    """
    use = lister or _default_artifact_lister
    for row in live:
        status = (row.get("status") or "").lower()
        output_dir = row.get("output_dir")
        if status not in _SUCCESS_STATUSES or not output_dir:
            continue
        try:
            paths = await use(str(output_dir))
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning(
                "artifact listing failed for task %s (%s)",
                row.get("task_id"),
                type(exc).__name__,
            )
            row["artifact_paths"] = []
            continue
        if len(paths) > cap:
            logger.warning(
                "artifact listing for task %s truncated: %d of %d kept",
                row.get("task_id"),
                cap,
                len(paths),
            )
            paths = paths[:cap]
        row["artifact_paths"] = paths
    return live


async def collect_terminal_artifact_set(
    *,
    task_id: str,
    output_dir: str,
    lister: ArtifactObjectLister | None = None,
    manifest_loader: ManifestLoader | None = None,
    cap: int = _DEFAULT_PATH_CAP,
) -> TerminalArtifactSet:
    """List and classify one terminal task's output objects.

    Listing and manifest failures degrade to stable warnings and an empty or
    unknown-only artifact set. Exception messages, local paths, and provider
    details never enter the returned warning projection.
    """
    use_lister = lister or _default_artifact_object_lister
    listing_warnings: list[ExecutionWarning] = []
    try:
        listed = tuple(
            sorted(
                await use_lister(output_dir),
                key=lambda item: item.relative_path,
            )
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning(
            "structured artifact listing failed for task %s (%s)",
            task_id,
            type(exc).__name__,
        )
        return TerminalArtifactSet(
            artifacts=(),
            warnings=(
                ExecutionWarning(
                    code="artifact_listing_failed",
                    retryable=True,
                    stage="artifact_listing",
                ),
            ),
        )

    if len(listed) > cap:
        logger.warning(
            "structured artifact listing for task %s truncated: %d of %d kept",
            task_id,
            cap,
            len(listed),
        )
        listed = listed[:cap]
        listing_warnings.append(
            ExecutionWarning(
                code="artifact_listing_truncated",
                retryable=False,
                stage="artifact_listing",
            )
        )

    manifest: ArtifactManifest | Mapping[str, Any] | None
    try:
        if manifest_loader is None:
            manifest = await _load_manifest_from_objects(output_dir, listed)
        else:
            loaded = manifest_loader(output_dir)
            manifest = await loaded if inspect.isawaitable(loaded) else loaded
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning(
            "artifact manifest unavailable for task %s (%s)",
            task_id,
            type(exc).__name__,
        )
        manifest = _INVALID_MANIFEST

    artifacts, manifest_warnings = classify_artifacts(listed, manifest)
    return TerminalArtifactSet(
        artifacts=artifacts,
        warnings=(*listing_warnings, *manifest_warnings),
    )


@overload
def collect_terminal_artifacts(
    task_results: Iterable[dict[str, Any]],
    *,
    task_id: None = None,
    output_dir: None = None,
    lister: None = None,
    manifest_loader: None = None,
) -> list[dict[str, Any]]: ...


@overload
def collect_terminal_artifacts(
    task_results: None = None,
    *,
    task_id: str,
    output_dir: str,
    lister: ArtifactObjectLister | None = None,
    manifest_loader: ManifestLoader | None = None,
) -> Awaitable[TerminalArtifactSet]: ...


def collect_terminal_artifacts(
    task_results: Iterable[dict[str, Any]] | None = None,
    *,
    task_id: str | None = None,
    output_dir: str | None = None,
    lister: ArtifactObjectLister | None = None,
    manifest_loader: ManifestLoader | None = None,
) -> Any:
    """Keep the legacy row projection and expose the async set seam.

    Positional row input returns the historical list synchronously. Supplying
    ``task_id`` and ``output_dir`` returns the manifest-backed coroutine so
    callers can use ``await collect_terminal_artifacts(...)`` during gradual
    migration.
    """
    structured = any(
        value is not None
        for value in (task_id, output_dir, lister, manifest_loader)
    )
    if structured:
        if not task_id or not output_dir:
            raise ValueError(
                "task_id and output_dir are required for structured artifacts"
            )
        return collect_terminal_artifact_set(
            task_id=task_id,
            output_dir=output_dir,
            lister=lister,
            manifest_loader=manifest_loader,
        )
    return _collect_legacy_artifacts(task_results or ())


async def _load_manifest_from_objects(
    output_dir: str,
    listed: Iterable[ListedArtifactObject],
) -> ArtifactManifest | None:
    """Load one bounded manifest from a previously listed object set."""
    manifest_object = next(
        (
            item
            for item in listed
            if item.relative_path == ARTIFACT_MANIFEST_FILENAME
        ),
        None,
    )
    if manifest_object is None:
        return None
    if manifest_object.size_bytes > _MAX_MANIFEST_BYTES:
        raise ValueError("artifact manifest exceeds size cap")
    content = await _read_manifest_bytes(output_dir, manifest_object)
    if len(content) > _MAX_MANIFEST_BYTES:
        raise ValueError("artifact manifest exceeds size cap")
    payload = json.loads(
        content.decode("utf-8"),
        object_pairs_hook=_unique_json_object,
    )
    return ArtifactManifest.model_validate(payload)


async def _read_manifest_bytes(
    output_dir: str,
    manifest_object: ListedArtifactObject,
) -> bytes:
    """Read a manifest from a local mount or bounded OBS download."""
    source_path = Path(manifest_object.source_path)
    if await asyncio.to_thread(source_path.is_file):
        return await asyncio.to_thread(source_path.read_bytes)
    if not manifest_object.download_ref:
        raise OSError("manifest download reference unavailable")
    local_path = await download_obs_file(
        manifest_object.download_ref,
        _MANIFEST_TEMP_DIR,
    )
    del output_dir
    return await asyncio.to_thread(Path(local_path).read_bytes)


def _unique_json_object(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    """Reject duplicate keys instead of silently accepting last-wins JSON."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate manifest key")
        result[key] = value
    return result


def _collect_legacy_artifacts(
    task_results: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the historical succeeded-task artifact descriptor list."""
    artifacts: list[dict[str, Any]] = []
    for row in task_results:
        status = (row.get("status") or "").lower()
        if status not in _SUCCESS_STATUSES:
            continue
        output_dir = row.get("output_dir")
        if not output_dir:
            continue
        artifacts.append(
            {
                "task_id": str(row.get("task_id", "")),
                "output_dir": str(output_dir),
                "paths": row.get("artifact_paths", []),
            }
        )
    return artifacts
