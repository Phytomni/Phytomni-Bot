# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Build deterministic, private result archives from terminal artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from ..config.defaults import ServerConfig
from ..storage.obs_relay_ops import (
    ObsAccessOptions,
    ObsStreamOptions,
    iter_object_chunks,
    object_size,
    put_object_file,
)
from .artifact_roles import ARCHIVE_ELIGIBLE_ROLES, ArtifactRole
from .outbound import ObsProfileName

__all__ = [
    "FORBIDDEN_NESTED_ARCHIVE_SUFFIXES",
    "MAX_RESULT_ARCHIVE_ARTIFACTS",
    "MAX_RESULT_ARCHIVE_UNCOMPRESSED_BYTES",
    "RESERVED_RESULT_PATHS",
    "ResultArchiveError",
    "ResultArchiveInventory",
    "ResultArchiveMember",
    "build_and_publish_result_archive",
    "build_and_publish_result_archive_with_runtime",
    "build_result_archive_inventory",
    "inventory_digest",
    "result_archive_member_to_data",
    "validate_result_archive_inventory",
]

MAX_RESULT_ARCHIVE_ARTIFACTS = 200
MAX_RESULT_ARCHIVE_UNCOMPRESSED_BYTES = 10 * 1024**3
RESERVED_RESULT_PATHS = frozenset(
    {
        ".phytomni-artifacts.json",
        "result_files.json",
    }
)
FORBIDDEN_NESTED_ARCHIVE_SUFFIXES = (
    ".zip",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".7z",
)
_SALVAGE_EXCLUDED_NAMES = frozenset({"inventory.json"})
_SALVAGE_MEDIA_TYPE = "application/octet-stream"
_ALLOWED_ERROR_CODES = frozenset(
    {
        "artifact_listing_failed",
        "artifact_manifest_invalid",
        "no_user_deliverables",
        "archive_inventory_limit_exceeded",
        "archive_generation_failed",
        "archive_publish_failed",
        "archive_contract_invalid",
    }
)
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_COPY_CHUNK_SIZE = 1024 * 1024

SERVER_CONFIG = ServerConfig()
ARCHIVE_TEMP_ROOT = Path(SERVER_CONFIG.TEMP_DIR)


class _ReportArtifactGroup(Protocol):
    """Structural input required from report collection."""

    @property
    def output_dir(self) -> str:
        """Return the child output directory."""
        raise NotImplementedError

    @property
    def artifact_set(self) -> Any:
        """Return the classified artifact set."""
        raise NotImplementedError


class ResultArchiveError(Exception):
    """One stable archive boundary error without provider details."""

    def __init__(self, code: str, retryable: bool = False) -> None:
        if code not in _ALLOWED_ERROR_CODES:
            code = "archive_contract_invalid"
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ResultArchiveMember:
    """One immutable producer result selected for archive inclusion."""

    child_index: int
    download_ref: str
    archive_path: str
    role: ArtifactRole
    media_type: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ResultArchiveInventory:
    """Bounded immutable result selection for one terminal run."""

    run_root: str
    members: tuple[ResultArchiveMember, ...]
    digest: str
    total_size_bytes: int


def build_result_archive_inventory(
    groups: Sequence[_ReportArtifactGroup],
) -> ResultArchiveInventory:
    """Select bounded user deliverables, salvaging unknown files if needed."""
    if not groups:
        raise ResultArchiveError("no_user_deliverables")
    run_root = _run_root(groups)
    members: list[ResultArchiveMember] = []
    paths: set[str] = set()
    for child_index, group in enumerate(groups, start=1):
        salvage = _raise_group_errors(group)
        for artifact in group.artifact_set.artifacts:
            relative_path = _safe_relative_path(artifact.relative_path)
            if _excluded_artifact(relative_path, artifact.role):
                continue
            media_type = _eligible_archive_media_type(
                artifact, relative_path, salvage
            )
            if media_type is None:
                continue
            if (
                not isinstance(artifact.download_ref, str)
                or not artifact.download_ref
            ):
                raise ResultArchiveError("archive_contract_invalid")
            if not isinstance(media_type, str) or not media_type:
                raise ResultArchiveError("archive_contract_invalid")
            if (
                isinstance(artifact.size_bytes, bool)
                or not isinstance(artifact.size_bytes, int)
                or artifact.size_bytes < 0
            ):
                raise ResultArchiveError("archive_contract_invalid")
            archive_path = f"results/part-{child_index:03d}/{relative_path}"
            if archive_path in paths:
                raise ResultArchiveError("archive_contract_invalid")
            paths.add(archive_path)
            members.append(
                ResultArchiveMember(
                    child_index=child_index,
                    download_ref=artifact.download_ref,
                    archive_path=archive_path,
                    role=artifact.role,
                    media_type=media_type,
                    size_bytes=artifact.size_bytes,
                )
            )
    if not members:
        raise ResultArchiveError("no_user_deliverables")
    total_size_bytes = sum(member.size_bytes for member in members)
    if (
        len(members) > MAX_RESULT_ARCHIVE_ARTIFACTS
        or total_size_bytes > MAX_RESULT_ARCHIVE_UNCOMPRESSED_BYTES
    ):
        raise ResultArchiveError("archive_inventory_limit_exceeded")
    frozen_members = tuple(members)
    return ResultArchiveInventory(
        run_root=run_root,
        members=frozen_members,
        digest=inventory_digest(frozen_members),
        total_size_bytes=total_size_bytes,
    )


def inventory_digest(members: Sequence[ResultArchiveMember]) -> str:
    """Hash canonical member metadata without local paths or file bodies."""
    payload = [result_archive_member_to_data(member) for member in members]
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def result_archive_member_to_data(
    member: ResultArchiveMember,
) -> dict[str, object]:
    """Return the canonical persisted and digest-bearing member fields."""
    return {
        "archive_path": member.archive_path,
        "child_index": member.child_index,
        "download_ref": member.download_ref,
        "media_type": member.media_type,
        "role": member.role.value,
        "size_bytes": member.size_bytes,
    }


def validate_result_archive_inventory(
    inventory: ResultArchiveInventory,
) -> ResultArchiveInventory:
    """Revalidate every persisted inventory field and its digest."""
    if not isinstance(inventory.run_root, str) or not inventory.run_root:
        raise ResultArchiveError("archive_contract_invalid")
    _safe_absolute_path(inventory.run_root)
    if (
        not inventory.members
        or len(inventory.members) > MAX_RESULT_ARCHIVE_ARTIFACTS
    ):
        raise ResultArchiveError("archive_contract_invalid")
    total_size_bytes = 0
    paths: set[str] = set()
    for member in inventory.members:
        _validate_member_scalars(member)
        _safe_archive_path(member.archive_path, member.child_index)
        if (
            member.role not in ARCHIVE_ELIGIBLE_ROLES
            and member.role is not ArtifactRole.UNKNOWN
        ):
            raise ResultArchiveError("archive_contract_invalid")
        if member.archive_path in paths:
            raise ResultArchiveError("archive_contract_invalid")
        paths.add(member.archive_path)
        total_size_bytes += member.size_bytes
    if total_size_bytes > MAX_RESULT_ARCHIVE_UNCOMPRESSED_BYTES:
        raise ResultArchiveError("archive_contract_invalid")
    digest = inventory_digest(inventory.members)
    if (
        inventory.digest != digest
        or inventory.total_size_bytes != total_size_bytes
    ):
        raise ResultArchiveError("archive_contract_invalid")
    return inventory


def build_and_publish_result_archive(
    inventory: ResultArchiveInventory,
    *,
    agent: str,
    summary_markdown: str,
    client: Any,
) -> str:
    """Create then size-verify one deterministic Zip64 archive in OBS."""
    validate_result_archive_inventory(inventory)
    if (
        not isinstance(agent, str)
        or not agent
        or not agent.replace("_", "").isalnum()
    ):
        raise ResultArchiveError("archive_contract_invalid")
    if not isinstance(summary_markdown, str):
        raise ResultArchiveError("archive_contract_invalid")
    digest_hex = inventory.digest.removeprefix("sha256:")
    object_key = (
        f"{inventory.run_root.rstrip('/')}/delivery/{digest_hex}/"
        f"{agent}-results.zip"
    )
    ARCHIVE_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(
            prefix="result-archive-", dir=ARCHIVE_TEMP_ROOT
        ) as scratch:
            archive_path = Path(scratch) / "results.zip"
            try:
                _write_archive(
                    archive_path,
                    inventory,
                    summary_markdown,
                    client=client,
                )
            except (OSError, TypeError, ValueError):
                raise ResultArchiveError(
                    "archive_generation_failed", retryable=True
                ) from None
            size = archive_path.stat().st_size
            try:
                existing_size = object_size(
                    SERVER_CONFIG.BUCKET_NAME,
                    object_key,
                    access=ObsAccessOptions(client=client),
                )
            except OSError:
                existing_size = None
            if existing_size is not None:
                if existing_size != size:
                    raise ResultArchiveError("archive_publish_failed")
                return object_key
            try:
                put_object_file(
                    SERVER_CONFIG.BUCKET_NAME,
                    object_key,
                    archive_path,
                    access=ObsAccessOptions(client=client),
                )
                published_size = _published_archive_size(
                    SERVER_CONFIG.BUCKET_NAME, object_key, client
                )
            except OSError:
                raise ResultArchiveError(
                    "archive_publish_failed", retryable=True
                ) from None
            if published_size != size:
                raise ResultArchiveError("archive_publish_failed")
    except (OSError, TypeError, ValueError):
        raise ResultArchiveError(
            "archive_generation_failed", retryable=True
        ) from None
    return object_key


async def build_and_publish_result_archive_with_runtime(
    inventory: ResultArchiveInventory,
    *,
    agent: str,
    summary_markdown: str,
    obs_runtime: Any,
) -> str:
    """Create and publish one archive with a lease per SDK request."""
    validate_result_archive_inventory(inventory)
    if (
        not isinstance(agent, str)
        or not agent
        or not agent.replace("_", "").isalnum()
    ):
        raise ResultArchiveError("archive_contract_invalid")
    if not isinstance(summary_markdown, str):
        raise ResultArchiveError("archive_contract_invalid")
    digest_hex = inventory.digest.removeprefix("sha256:")
    object_key = (
        f"{inventory.run_root.rstrip('/')}/delivery/{digest_hex}/"
        f"{agent}-results.zip"
    )
    os.makedirs(ARCHIVE_TEMP_ROOT, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(
            prefix="result-archive-", dir=ARCHIVE_TEMP_ROOT
        ) as scratch:
            scratch_path = Path(scratch)
            sources = await _download_archive_sources(
                inventory,
                scratch_path,
                obs_runtime=obs_runtime,
            )
            archive_path = scratch_path / "results.zip"
            try:
                _write_archive_from_sources(
                    archive_path,
                    inventory,
                    summary_markdown,
                    sources,
                )
            except (OSError, TypeError, ValueError):
                raise ResultArchiveError(
                    "archive_generation_failed", retryable=True
                ) from None
            size = archive_path.stat().st_size
            try:
                existing_size = await obs_runtime.run(
                    ObsProfileName.PRIMARY,
                    lambda client: object_size(
                        SERVER_CONFIG.BUCKET_NAME,
                        object_key,
                        access=ObsAccessOptions(client=client),
                    ),
                )
            except OSError:
                existing_size = None
            if existing_size is not None:
                if existing_size != size:
                    raise ResultArchiveError("archive_publish_failed")
                return object_key
            try:
                await obs_runtime.run(
                    ObsProfileName.PRIMARY,
                    lambda client: put_object_file(
                        SERVER_CONFIG.BUCKET_NAME,
                        object_key,
                        archive_path,
                        access=ObsAccessOptions(client=client),
                    ),
                )
                published_size = await _published_archive_size_with_runtime(
                    SERVER_CONFIG.BUCKET_NAME,
                    object_key,
                    obs_runtime=obs_runtime,
                )
            except OSError:
                raise ResultArchiveError(
                    "archive_publish_failed", retryable=True
                ) from None
            if published_size != size:
                raise ResultArchiveError("archive_publish_failed")
    except (OSError, TypeError, ValueError):
        raise ResultArchiveError(
            "archive_generation_failed", retryable=True
        ) from None
    return object_key


async def _download_archive_sources(
    inventory: ResultArchiveInventory,
    scratch_path: Path,
    *,
    obs_runtime: Any,
) -> tuple[Path, ...]:
    """Download each archive member under its own OBS lease."""
    sources: list[Path] = []
    for index, member in enumerate(inventory.members):
        source_path = scratch_path / f"source-{index:04d}"
        await obs_runtime.run(
            ObsProfileName.PRIMARY,
            lambda client, member=member, source_path=source_path: (
                _download_object_to_file(
                    SERVER_CONFIG.BUCKET_NAME,
                    member.download_ref,
                    source_path,
                    client=client,
                )
            ),
        )
        sources.append(source_path)
    return tuple(sources)


def _download_object_to_file(
    bucket: str,
    object_key: str,
    destination: Path,
    *,
    client: Any,
) -> None:
    """Transfer one remote object into a private staging file."""
    with destination.open("wb") as handle:
        for block in iter_object_chunks(
            bucket,
            object_key,
            access=ObsAccessOptions(client=client),
            stream=ObsStreamOptions(chunk_size=_COPY_CHUNK_SIZE),
        ):
            handle.write(block)


def _write_archive_from_sources(
    archive_path: Path,
    inventory: ResultArchiveInventory,
    summary_markdown: str,
    sources: Sequence[Path],
) -> None:
    """Build the deterministic local archive after remote reads complete."""
    with ZipFile(
        archive_path, "w", compression=ZIP_STORED, allowZip64=True
    ) as archive:
        _write_zip_bytes(
            archive, "summary.md", _normalized_summary(summary_markdown)
        )
        for member, source_path in zip(inventory.members, sources):
            actual_size = 0
            info = _zip_info(member.archive_path)
            with (
                source_path.open("rb") as source,
                archive.open(info, mode="w", force_zip64=True) as destination,
            ):
                while block := source.read(_COPY_CHUNK_SIZE):
                    actual_size += len(block)
                    destination.write(block)
            if actual_size != member.size_bytes:
                raise ResultArchiveError("archive_generation_failed")


async def _published_archive_size_with_runtime(
    bucket: str,
    object_key: str,
    *,
    obs_runtime: Any,
) -> int:
    """Read one archive metadata response under its own OBS lease."""
    try:
        size_bytes = await obs_runtime.run(
            ObsProfileName.PRIMARY,
            lambda client: object_size(
                bucket,
                object_key,
                access=ObsAccessOptions(client=client),
            ),
        )
    except OSError:
        raise ResultArchiveError(
            "archive_publish_failed", retryable=True
        ) from None
    if size_bytes is None:
        raise ResultArchiveError("archive_publish_failed", retryable=True)
    return size_bytes


def _published_archive_size(bucket: str, object_key: str, client: Any) -> int:
    """Read one published archive size and map transport errors safely."""
    try:
        size_bytes = object_size(
            bucket,
            object_key,
            access=ObsAccessOptions(client=client),
        )
    except OSError:
        raise ResultArchiveError(
            "archive_publish_failed", retryable=True
        ) from None
    if size_bytes is None:
        raise ResultArchiveError("archive_publish_failed", retryable=True)
    return size_bytes


def _run_root(groups: Sequence[_ReportArtifactGroup]) -> str:
    """Return the common direct parent of every child output directory."""
    parents = []
    for group in groups:
        if not isinstance(group.output_dir, str) or not group.output_dir:
            raise ResultArchiveError("archive_contract_invalid")
        output_dir = _safe_absolute_path(group.output_dir)
        parents.append(str(PurePosixPath(output_dir).parent))
    if len(set(parents)) != 1:
        raise ResultArchiveError("archive_contract_invalid")
    return parents[0]


def _validate_member_scalars(member: ResultArchiveMember) -> None:
    """Reject malformed persisted scalar fields without coercion."""
    if isinstance(member.child_index, bool) or not isinstance(
        member.child_index, int
    ):
        raise ResultArchiveError("archive_contract_invalid")
    if member.child_index < 1:
        raise ResultArchiveError("archive_contract_invalid")
    if not isinstance(member.download_ref, str) or not member.download_ref:
        raise ResultArchiveError("archive_contract_invalid")
    if not isinstance(member.media_type, str) or not member.media_type:
        raise ResultArchiveError("archive_contract_invalid")
    if isinstance(member.size_bytes, bool) or not isinstance(
        member.size_bytes, int
    ):
        raise ResultArchiveError("archive_contract_invalid")
    if member.size_bytes < 0:
        raise ResultArchiveError("archive_contract_invalid")


def _raise_group_errors(group: _ReportArtifactGroup) -> bool:
    """Raise listing errors; return True when UNKNOWN files may be salvaged."""
    codes = {warning.code for warning in group.artifact_set.warnings}
    if "artifact_listing_failed" in codes:
        raise ResultArchiveError("artifact_listing_failed", retryable=True)
    if "artifact_listing_truncated" in codes:
        raise ResultArchiveError("archive_inventory_limit_exceeded")
    salvage = bool(
        {"artifact_manifest_missing", "artifact_manifest_invalid"} & codes
    )
    return salvage and bool(group.artifact_set.artifacts)


def _eligible_archive_media_type(
    artifact: Any, relative_path: str, salvage: bool
) -> str | None:
    """Return a member media type, or None when the artifact is skipped."""
    if salvage:
        if _is_archive_salvage_member(artifact, relative_path):
            return _SALVAGE_MEDIA_TYPE
        return None
    if artifact.role in ARCHIVE_ELIGIBLE_ROLES:
        return artifact.media_type
    return None


def _is_archive_salvage_member(artifact: Any, relative_path: str) -> bool:
    """Keep only unknown files that are not reserved inventory names."""
    name = PurePosixPath(relative_path).name
    return (
        artifact.role is ArtifactRole.UNKNOWN
        and name not in _SALVAGE_EXCLUDED_NAMES
    )


def _excluded_artifact(relative_path: str, role: ArtifactRole) -> bool:
    """Return whether internal or generated archive content is excluded."""
    name = PurePosixPath(relative_path).name
    return (
        name in RESERVED_RESULT_PATHS
        or name == "summary.md"
        or role is ArtifactRole.RESULT_ARCHIVE
        or relative_path.lower().endswith(FORBIDDEN_NESTED_ARCHIVE_SUFFIXES)
    )


def _safe_relative_path(value: object) -> str:
    """Validate one artifact path before it becomes a ZIP member name."""
    if not isinstance(value, str) or not value or "\\" in value:
        raise ResultArchiveError("archive_contract_invalid")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ResultArchiveError("archive_contract_invalid")
    path = PurePosixPath(value)
    parts = value.split("/")
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(not part or part in {".", ".."} for part in parts)
    ):
        raise ResultArchiveError("archive_contract_invalid")
    return value


def _safe_absolute_path(value: str) -> str:
    """Validate an OBS-style absolute directory without resolving locally."""
    if "\\" in value:
        raise ResultArchiveError("archive_contract_invalid")
    path = PurePosixPath(value)
    if not path.is_absolute() or any(
        part in {".", ".."} for part in path.parts
    ):
        raise ResultArchiveError("archive_contract_invalid")
    return path.as_posix()


def _safe_archive_path(value: object, child_index: int) -> None:
    """Ensure persisted member paths retain their original child prefix."""
    path = _safe_relative_path(value)
    prefix = f"results/part-{child_index:03d}/"
    if not path.startswith(prefix) or _excluded_artifact(
        path.removeprefix(prefix), ArtifactRole.SCIENTIFIC_DATA
    ):
        raise ResultArchiveError("archive_contract_invalid")


def _write_archive(
    archive_path: Path,
    inventory: ResultArchiveInventory,
    summary_markdown: str,
    *,
    client: Any,
) -> None:
    """Write summary and members while checking every source byte count."""
    with ZipFile(
        archive_path, "w", compression=ZIP_STORED, allowZip64=True
    ) as archive:
        _write_zip_bytes(
            archive, "summary.md", _normalized_summary(summary_markdown)
        )
        for member in inventory.members:
            info = _zip_info(member.archive_path)
            actual_size = 0
            with archive.open(info, mode="w", force_zip64=True) as destination:
                for block in iter_object_chunks(
                    SERVER_CONFIG.BUCKET_NAME,
                    member.download_ref,
                    access=ObsAccessOptions(client=client),
                    stream=ObsStreamOptions(chunk_size=_COPY_CHUNK_SIZE),
                ):
                    if not isinstance(block, bytes):
                        raise ValueError("invalid stream chunk")
                    actual_size += len(block)
                    destination.write(block)
            if actual_size != member.size_bytes:
                raise ResultArchiveError("archive_generation_failed")


def _normalized_summary(summary_markdown: str) -> bytes:
    """Return caller-supplied sanitized answer as UTF-8 with one newline."""
    normalized = summary_markdown.replace("\r\n", "\n").replace("\r", "\n")
    return (normalized.rstrip("\n") + "\n").encode("utf-8", "strict")


def _write_zip_bytes(archive: ZipFile, name: str, content: bytes) -> None:
    """Write one deterministic generated ZIP member."""
    info = _zip_info(name)
    with archive.open(info, mode="w", force_zip64=True) as destination:
        destination.write(content)


def _zip_info(name: str) -> ZipInfo:
    """Build fixed ZIP metadata for a regular UTF-8 file."""
    info = ZipInfo(name, date_time=_ZIP_TIMESTAMP)
    info.compress_type = ZIP_STORED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info
