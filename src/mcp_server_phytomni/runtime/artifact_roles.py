# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Role-aware validation and projection for terminal output artifacts.

Producer manifests are the only source of scientific meaning. Object
enumeration supplies existence, actual size, and a bounded download
reference, but it never infers a report role from a filename extension.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from ..storage.artifact_listing import ListedArtifactObject
from .execution_models import ExecutionWarning

__all__ = [
    "ARTIFACT_MANIFEST_FILENAME",
    "ARTIFACT_MANIFEST_INSTRUCTIONS",
    "ARCHIVE_ELIGIBLE_ROLES",
    "ArtifactManifest",
    "ArtifactManifestItem",
    "ArtifactRole",
    "ClassifiedArtifact",
    "PublicArtifactDescriptor",
    "append_artifact_manifest_contract",
    "classify_artifacts",
]

ARTIFACT_MANIFEST_FILENAME = ".phytomni-artifacts.json"
ARTIFACT_MANIFEST_INSTRUCTIONS = """### ARTIFACT MANIFEST CONTRACT

Write `.phytomni-artifacts.json` as the last output file in the output
directory. Use this exact JSON shape and list every other output artifact
exactly once:

{
  "version": "1.0",
  "artifacts": [
    {
      "path": "tables/gene_summary.csv",
      "role": "scientific_table",
      "media_type": "text/csv"
    },
    {
      "path": "logs/analysis.log",
      "role": "execution_log",
      "media_type": "text/plain"
    }
  ]
}

Allowed roles are `scientific_report`, `scientific_table`,
`scientific_text`, `scientific_figure`, `scientific_data`, `input`,
`execution_log`, `diagnostic`, and `unknown`. `result_archive` is reserved for
Bot and must not appear in this producer manifest. Use paths relative to the
output directory. Do not classify by filename extension; classify by semantic
producer knowledge. Use `unknown` when the producer cannot prove a role. Do
not put credentials, provider payloads, or absolute paths in the manifest.
Keep writing `result_files.json` for compatibility, but it does not grant
report eligibility.
"""
_UNKNOWN_MEDIA_TYPE = "application/octet-stream"
_MANIFEST_WARNING_STAGE = "artifact_manifest"
_REPORT_TEXT_ROLES = frozenset(
    {
        "scientific_report",
        "scientific_table",
        "scientific_text",
    }
)


class ArtifactRole(StrEnum):
    """Semantic role assigned by the producer of one output object."""

    SCIENTIFIC_REPORT = "scientific_report"
    SCIENTIFIC_TABLE = "scientific_table"
    SCIENTIFIC_TEXT = "scientific_text"
    SCIENTIFIC_FIGURE = "scientific_figure"
    SCIENTIFIC_DATA = "scientific_data"
    RESULT_ARCHIVE = "result_archive"
    INPUT = "input"
    EXECUTION_LOG = "execution_log"
    DIAGNOSTIC = "diagnostic"
    UNKNOWN = "unknown"


ARCHIVE_ELIGIBLE_ROLES = frozenset(
    {
        ArtifactRole.SCIENTIFIC_REPORT,
        ArtifactRole.SCIENTIFIC_TABLE,
        ArtifactRole.SCIENTIFIC_TEXT,
        ArtifactRole.SCIENTIFIC_FIGURE,
        ArtifactRole.SCIENTIFIC_DATA,
    }
)


class ArtifactManifestItem(BaseModel):
    """One producer-declared artifact path and semantic role."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    path: StrictStr = Field(min_length=1, max_length=1024)
    role: ArtifactRole
    media_type: StrictStr = Field(
        default=_UNKNOWN_MEDIA_TYPE,
        min_length=1,
        max_length=255,
    )

    @field_validator("role")
    @classmethod
    def _validate_producer_role(cls, value: ArtifactRole) -> ArtifactRole:
        """Keep Bot-owned result archives out of producer declarations."""
        if value is ArtifactRole.RESULT_ARCHIVE:
            raise ValueError("result_archive is reserved for Bot")
        return value

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        """Require one normalized, relative POSIX path."""
        if value != value.strip():
            raise ValueError("manifest path must not have surrounding space")
        if "\\" in value:
            raise ValueError("manifest path must use POSIX separators")
        if value.startswith("/") or PurePosixPath(value).is_absolute():
            raise ValueError("manifest path must be relative")
        if value.split("/", maxsplit=1)[0].endswith(":"):
            raise ValueError("manifest path must not contain a drive root")
        parts = value.split("/")
        if any(not part or part in {".", ".."} for part in parts):
            raise ValueError("manifest path contains an empty or parent part")
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("manifest path contains a control character")
        normalized = PurePosixPath(value).as_posix()
        if normalized != value:
            raise ValueError("manifest path must be normalized")
        return value


class ArtifactManifest(BaseModel):
    """Strict producer manifest for one output directory."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    version: Literal["1.0"]
    artifacts: list[ArtifactManifestItem] = Field(
        default_factory=list,
        max_length=1000,
    )

    @model_validator(mode="after")
    def _validate_unique_paths(self) -> ArtifactManifest:
        """Reject duplicate declarations before classification."""
        paths = [item.path for item in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("manifest paths must be unique")
        return self


@dataclass(frozen=True, slots=True)
class PublicArtifactDescriptor:
    """Safe artifact metadata exposed to a client or report projection."""

    role: str
    name: str
    media_type: str
    size_bytes: int
    downloadable: bool
    report_context_eligible: bool
    download_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ClassifiedArtifact:
    """Internal artifact record retaining source provenance privately."""

    source_path: str
    relative_path: str
    role: ArtifactRole
    media_type: str
    size_bytes: int
    download_ref: str | None

    @property
    def report_context_eligible(self) -> bool:
        """Return whether this role may contribute report text."""
        return self.role.value in _REPORT_TEXT_ROLES

    def to_public(self) -> PublicArtifactDescriptor:
        """Drop the private source path and retain safe display metadata."""
        return PublicArtifactDescriptor(
            role=self.role.value,
            name=PurePosixPath(self.relative_path).name,
            media_type=self.media_type,
            size_bytes=self.size_bytes,
            downloadable=self.download_ref is not None,
            report_context_eligible=self.report_context_eligible,
            download_ref=self.download_ref,
        )


def classify_artifacts(
    listed: Iterable[ListedArtifactObject],
    manifest: ArtifactManifest | Mapping[str, object] | None,
) -> tuple[tuple[ClassifiedArtifact, ...], tuple[ExecutionWarning, ...]]:
    """Classify listed objects using only a valid producer manifest.

    Missing or invalid manifests make every listed object unknown. A
    manifest declaration for an object that was not actually listed produces
    a warning but never creates a synthetic artifact descriptor.
    """
    listed_objects = tuple(listed)
    parsed_manifest, manifest_error = _parse_manifest(manifest)
    if parsed_manifest is None:
        artifacts = tuple(
            _unknown_artifact(item, diagnostic=_is_manifest_file(item))
            for item in listed_objects
        )
        return artifacts, (_manifest_warning(manifest_error or "invalid"),)

    declarations = {item.path: item for item in parsed_manifest.artifacts}
    listed_paths = {item.relative_path for item in listed_objects}
    artifacts = tuple(
        _classify_listed(item, declarations.get(item.relative_path))
        for item in listed_objects
    )
    warnings = tuple(
        _manifest_warning("path_not_listed", retryable=False)
        for item in parsed_manifest.artifacts
        if item.path not in listed_paths
    )
    return artifacts, warnings


def _parse_manifest(
    manifest: ArtifactManifest | Mapping[str, object] | None,
) -> tuple[ArtifactManifest | None, str | None]:
    """Return a validated manifest and a stable missing/invalid reason."""
    if manifest is None:
        return None, "missing"
    if isinstance(manifest, ArtifactManifest):
        return manifest, None
    if not isinstance(manifest, Mapping):
        return None, "invalid"
    try:
        return ArtifactManifest.model_validate(manifest), None
    except (TypeError, ValidationError):
        return None, "invalid"


def _manifest_warning(
    reason: str, *, retryable: bool = False
) -> ExecutionWarning:
    """Build a stable, exception-free manifest warning."""
    code = {
        "missing": "artifact_manifest_missing",
        "invalid": "artifact_manifest_invalid",
        "path_not_listed": "artifact_manifest_path_not_listed",
    }.get(reason, "artifact_manifest_invalid")
    return ExecutionWarning(
        code=code,
        retryable=retryable,
        stage=_MANIFEST_WARNING_STAGE,
    )


def _classify_listed(
    listed: ListedArtifactObject,
    declaration: ArtifactManifestItem | None,
) -> ClassifiedArtifact:
    """Project one listed object without trusting its filename."""
    if _is_manifest_file(listed):
        return _unknown_artifact(listed, diagnostic=True)
    if declaration is None:
        return _unknown_artifact(listed)
    return ClassifiedArtifact(
        source_path=listed.source_path,
        relative_path=listed.relative_path,
        role=declaration.role,
        media_type=declaration.media_type,
        size_bytes=listed.size_bytes,
        download_ref=listed.download_ref,
    )


def _unknown_artifact(
    listed: ListedArtifactObject, *, diagnostic: bool = False
) -> ClassifiedArtifact:
    """Build a fail-closed descriptor with no extension-derived meaning."""
    return ClassifiedArtifact(
        source_path=listed.source_path,
        relative_path=listed.relative_path,
        role=ArtifactRole.DIAGNOSTIC if diagnostic else ArtifactRole.UNKNOWN,
        media_type=("application/json" if diagnostic else _UNKNOWN_MEDIA_TYPE),
        size_bytes=listed.size_bytes,
        download_ref=listed.download_ref,
    )


def _is_manifest_file(listed: ListedArtifactObject) -> bool:
    """Return whether a listed object is the producer manifest itself."""
    return listed.relative_path == ARTIFACT_MANIFEST_FILENAME


def append_artifact_manifest_contract(text: str) -> str:
    """Append the producer manifest contract once to a prompt."""
    if ARTIFACT_MANIFEST_INSTRUCTIONS in text:
        return text
    return f"{text.rstrip()}\n\n{ARTIFACT_MANIFEST_INSTRUCTIONS}"
