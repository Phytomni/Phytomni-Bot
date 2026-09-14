# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit contracts for manifest-backed artifact roles."""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest
from pydantic import ValidationError

from mcp_server_phytomni.runtime.artifact_roles import (
    ARCHIVE_ELIGIBLE_ROLES,
    ArtifactManifest,
    ArtifactManifestItem,
    ArtifactRole,
    ClassifiedArtifact,
    classify_artifacts,
)
from mcp_server_phytomni.storage.artifact_listing import (
    ListedArtifactObject,
)

pytestmark = pytest.mark.unit


def listed_object(
    relative_path: str,
    *,
    source_path: str = "/private/obsfs/run/object",
    size_bytes: int = 1024,
    download_ref: str | None = "/obs/phytomni/run/object",
) -> ListedArtifactObject:
    """Build one listed object with a deliberately private source path."""
    return ListedArtifactObject(
        relative_path=relative_path,
        source_path=source_path,
        size_bytes=size_bytes,
        download_ref=download_ref,
    )


def manifest_for(*paths: str) -> ArtifactManifest:
    """Build a valid manifest for the supplied paths."""
    return ArtifactManifest.model_validate(
        {
            "version": "1.0",
            "artifacts": [
                {
                    "path": path,
                    "role": "scientific_text",
                    "media_type": "text/plain",
                }
                for path in paths
            ],
        }
    )


@pytest.mark.parametrize(
    "role",
    [
        "scientific_report",
        "scientific_table",
        "scientific_text",
        "scientific_figure",
        "scientific_data",
        "result_archive",
        "input",
        "execution_log",
        "diagnostic",
        "unknown",
    ],
)
def test_exact_artifact_role_enum(role: str) -> None:
    """Every contract role remains an exact stable string value."""
    assert ArtifactRole(role).value == role


def test_csv_without_manifest_is_unknown() -> None:
    """An extension alone never grants report-context eligibility."""
    artifacts, warnings = classify_artifacts(
        listed=(listed_object("summary.csv"),),
        manifest=None,
    )

    assert artifacts[0].role is ArtifactRole.UNKNOWN
    assert artifacts[0].report_context_eligible is False
    assert warnings[0].code == "artifact_manifest_missing"


def test_producer_manifest_allows_scientific_data() -> None:
    """Producer-declared scientific data is eligible for Bot archives."""
    item = ArtifactManifestItem(
        path="data/normalized.parquet",
        role=ArtifactRole.SCIENTIFIC_DATA,
        media_type="application/vnd.apache.parquet",
    )

    assert item.role is ArtifactRole.SCIENTIFIC_DATA
    assert item.role in ARCHIVE_ELIGIBLE_ROLES


def test_producer_manifest_cannot_claim_result_archive() -> None:
    """Only Bot may declare an archive artifact."""
    with pytest.raises(ValidationError):
        ArtifactManifestItem(
            path="results.zip",
            role=ArtifactRole.RESULT_ARCHIVE,
            media_type="application/zip",
        )


def test_public_descriptor_omits_private_source_path() -> None:
    """Public metadata contains no local mount or private source path."""
    descriptor = ClassifiedArtifact(
        source_path="/home/private/sentinel/summary.csv",
        relative_path="summary.csv",
        role=ArtifactRole.SCIENTIFIC_TABLE,
        media_type="text/csv",
        size_bytes=12,
        download_ref="/obs/phytomni/run/summary.csv",
    ).to_public()

    assert "source_path" not in asdict(descriptor)
    assert set(asdict(descriptor)) == {
        "role",
        "name",
        "media_type",
        "size_bytes",
        "downloadable",
        "report_context_eligible",
        "download_ref",
    }
    assert "/home/private" not in json.dumps(asdict(descriptor))
    assert descriptor.report_context_eligible is True


def test_invalid_manifest_keeps_objects_unknown() -> None:
    """Invalid producer declarations fail closed with a stable warning."""
    artifacts, warnings = classify_artifacts(
        listed=(listed_object("summary.txt"),),
        manifest={"version": "broken", "artifacts": []},
    )

    assert artifacts[0].role is ArtifactRole.UNKNOWN
    assert artifacts[0].report_context_eligible is False
    assert [warning.code for warning in warnings] == [
        "artifact_manifest_invalid"
    ]


def test_valid_manifest_assigns_declared_role_and_actual_size() -> None:
    """Valid producer semantics are projected without changing size."""
    artifacts, warnings = classify_artifacts(
        listed=(listed_object("summary.csv", size_bytes=37),),
        manifest=manifest_for("summary.csv"),
    )

    assert not warnings
    assert artifacts[0].role is ArtifactRole.SCIENTIFIC_TEXT
    assert artifacts[0].media_type == "text/plain"
    assert artifacts[0].size_bytes == 37


def test_manifest_file_is_diagnostic_even_if_declared_as_scientific() -> None:
    """The manifest itself is never eligible report context."""
    artifacts, warnings = classify_artifacts(
        listed=(listed_object(".phytomni-artifacts.json"),),
        manifest=manifest_for(".phytomni-artifacts.json"),
    )

    assert not warnings
    assert artifacts[0].role is ArtifactRole.DIAGNOSTIC
    assert artifacts[0].report_context_eligible is False


def test_undeclared_listed_object_is_unknown() -> None:
    """A listed object missing from the manifest is not promoted."""
    artifacts, warnings = classify_artifacts(
        listed=(listed_object("summary.csv"), listed_object("extra.csv")),
        manifest=manifest_for("summary.csv"),
    )

    assert [artifact.role for artifact in artifacts] == [
        ArtifactRole.SCIENTIFIC_TEXT,
        ArtifactRole.UNKNOWN,
    ]
    assert not warnings


def test_manifest_path_not_listed_warns_without_synthetic_artifact() -> None:
    """A declaration for a missing object emits only a stable warning."""
    artifacts, warnings = classify_artifacts(
        listed=(),
        manifest=manifest_for("summary.csv"),
    )

    assert not artifacts
    assert [warning.code for warning in warnings] == [
        "artifact_manifest_path_not_listed"
    ]


@pytest.mark.parametrize(
    "bad_path",
    ["/etc/passwd", "../escape.txt", "a/../../b.txt", "a\\b.txt", "a//b"],
)
def test_manifest_path_escape_fails_closed(bad_path: str) -> None:
    """Unsafe producer paths are rejected by the strict manifest model."""
    with pytest.raises(ValidationError):
        ArtifactManifest.model_validate(
            {
                "version": "1.0",
                "artifacts": [
                    {
                        "path": bad_path,
                        "role": "scientific_text",
                        "media_type": "text/plain",
                    }
                ],
            }
        )


def test_manifest_rejects_duplicate_paths() -> None:
    """One object cannot receive two producer meanings."""
    with pytest.raises(ValidationError):
        ArtifactManifest.model_validate(
            {
                "version": "1.0",
                "artifacts": [
                    {
                        "path": "summary.csv",
                        "role": "scientific_table",
                        "media_type": "text/csv",
                    },
                    {
                        "path": "summary.csv",
                        "role": "execution_log",
                        "media_type": "text/plain",
                    },
                ],
            }
        )


def test_manifest_rejects_invalid_version() -> None:
    """Unsupported producer versions fail at the model layer."""
    with pytest.raises(ValidationError):
        ArtifactManifest.model_validate(
            {
                "version": "broken",
                "artifacts": [],
            }
        )


def test_manifest_ignores_nonsemantic_producer_fields() -> None:
    """Extra producer fields are dropped and never become model attributes."""
    manifest = ArtifactManifest.model_validate(
        {
            "version": "1.0",
            "task_type": "protein_design_analysis",
            "organism": "Arabidopsis thaliana",
            "pipeline": {"name": "external"},
            "artifacts": [
                {
                    "path": "scientific_report.md",
                    "role": "scientific_report",
                    "media_type": "text/markdown",
                    "description": "Producer description",
                }
            ],
        }
    )
    assert len(manifest.artifacts) == 1
    assert not hasattr(manifest.artifacts[0], "description")


def test_manifest_defaults_missing_media_type() -> None:
    """A missing media_type is filled with the opaque binary default."""
    manifest = ArtifactManifest.model_validate(
        {
            "version": "1.0",
            "artifacts": [
                {
                    "path": "plot.png",
                    "role": "scientific_figure",
                    "description": "optional producer field",
                }
            ],
        }
    )
    assert manifest.artifacts[0].media_type == "application/octet-stream"
