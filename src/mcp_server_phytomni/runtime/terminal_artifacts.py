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
    list_artifact_objects_with_runtime,
    list_artifact_paths_with_runtime,
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
from .outbound import current_obs_runtime

logger = logging.getLogger(__name__)

__all__ = [
    "ArtifactLister",
    "ArtifactObjectLister",
    "ManifestLoader",
    "TerminalArtifactSet",
    "collect_terminal_artifact_set",
    "collect_terminal_artifacts",
    "enumerate_artifact_paths",
    "repair_unescaped_json_string_controls",
    "sanitize_artifact_relpath",
]

_SUCCESS_STATUSES = frozenset({"succeeded", "success", "completed", "done"})
_DEFAULT_PATH_CAP = 200
_MAX_MANIFEST_BYTES = 32_768
_MANIFEST_TEMP_DIR = "terminal-manifest"
_INVALID_MANIFEST: Mapping[str, Any] = {
    "version": "invalid",
    "artifacts": [],
}
_JSON_STRING_CONTROLS = frozenset({0x09, 0x0A, 0x0D})
_PATH_REPLACEMENT = "_"


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


async def _default_artifact_lister(
    output_dir: str,
    *,
    limit: int | None = None,
) -> list[str]:
    """List public artifact paths through the existing storage helper."""
    config = ServerConfig()
    if limit is None:
        return await list_artifact_paths_with_runtime(
            output_dir,
            bucket_name=config.BUCKET_NAME,
            obs_runtime=current_obs_runtime(),
        )
    return await list_artifact_paths_with_runtime(
        output_dir,
        bucket_name=config.BUCKET_NAME,
        obs_runtime=current_obs_runtime(),
        limit=limit,
    )


async def _default_artifact_object_lister(
    output_dir: str,
    *,
    limit: int | None = None,
) -> list[ListedArtifactObject]:
    """List output objects and actual sizes off the event loop."""
    del limit
    config = ServerConfig()
    return await list_artifact_objects_with_runtime(
        output_dir,
        bucket_name=config.BUCKET_NAME,
        obs_runtime=current_obs_runtime(),
        limit=_DEFAULT_PATH_CAP + 1,
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
            if lister is None:
                paths = await _default_artifact_lister(
                    str(output_dir), limit=cap + 1
                )
            else:
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
        if lister is None:
            raw_listed = await _default_artifact_object_lister(
                output_dir, limit=cap + 1
            )
        else:
            raw_listed = await use_lister(output_dir)
        listed = tuple(
            sorted(
                raw_listed,
                key=lambda item: item.relative_path,
            )
        )
        listed = tuple(_sanitize_listed_object(item) for item in listed)
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
) -> list[dict[str, Any]]:
    """Project legacy artifact dicts from reconciled task rows.

    This positional form stays synchronous for MCP formatting. New
    terminal assembly should use the keyword ``task_id`` /
    ``output_dir`` form or ``collect_terminal_artifact_set``.
    """


@overload
def collect_terminal_artifacts(
    task_results: None = None,
    *,
    task_id: str,
    output_dir: str,
    lister: ArtifactObjectLister | None = None,
    manifest_loader: ManifestLoader | None = None,
) -> Awaitable[TerminalArtifactSet]:
    """Return the manifest-backed artifact-set coroutine.

    Supplying ``task_id`` and ``output_dir`` selects the structured
    path so callers can ``await collect_terminal_artifacts(...)``
    during gradual migration.
    """


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


def repair_unescaped_json_string_controls(text: str) -> str:
    """Replace unescaped TAB/LF/CR inside JSON strings with '_'."""
    out: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if not in_string:
            if char == '"':
                in_string = True
            out.append(char)
            continue
        if escaped:
            out.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            out.append(char)
            continue
        if char == '"':
            in_string = False
            out.append(char)
            continue
        if ord(char) in _JSON_STRING_CONTROLS:
            out.append(_PATH_REPLACEMENT)
            continue
        out.append(char)
    return "".join(out)


def sanitize_artifact_relpath(value: str) -> str:
    """Replace C0 controls and DEL so ZIP names and matching stay POSIX."""
    return "".join(
        _PATH_REPLACEMENT if ord(char) < 32 or ord(char) == 127 else char
        for char in value
    )


def _sanitize_listed_object(
    item: ListedArtifactObject,
) -> ListedArtifactObject:
    """Keep OBS keys; normalize only the classification relative path."""
    safe_relative = sanitize_artifact_relpath(item.relative_path)
    if safe_relative == item.relative_path:
        return item
    return ListedArtifactObject(
        relative_path=safe_relative,
        source_path=item.source_path,
        size_bytes=item.size_bytes,
        download_ref=item.download_ref,
    )


def _payload_with_sanitized_paths(payload: Any) -> Any:
    """Rewrite manifest paths before Pydantic rejects control characters."""
    if not isinstance(payload, dict):
        return payload
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        return payload
    rewritten = []
    for item in artifacts:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            item = {
                **item,
                "path": sanitize_artifact_relpath(item["path"]),
            }
        rewritten.append(item)
    return {**payload, "artifacts": rewritten}


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
    decoded = content.decode("utf-8")
    try:
        payload = json.loads(decoded, object_pairs_hook=_unique_json_object)
    except json.JSONDecodeError:
        payload = json.loads(
            repair_unescaped_json_string_controls(decoded),
            object_pairs_hook=_unique_json_object,
        )
    return ArtifactManifest.model_validate(
        _payload_with_sanitized_paths(payload)
    )


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
