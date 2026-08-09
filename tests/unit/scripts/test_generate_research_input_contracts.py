# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Deterministic, sanitized contract fixtures for Research input resolution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.generate_research_input_contracts import (
    ERROR_CODES,
    EXPECTED_JSON_FILES,
    LIFECYCLE_FILES,
    generate_contracts,
)
from tests.support.research_fakes import RESEARCH_CONTRACT_FORBIDDEN_MARKERS

from mcp_server_phytomni.agents.research.scientific_formats import (
    advertised_research_formats,
)
from mcp_server_phytomni.api.agent_capabilities import (
    MAX_FILE_BYTES,
    MAX_TOTAL_BYTES,
)
from mcp_server_phytomni.api.lifecycle_contract import (
    project_research_lifecycle,
)
from mcp_server_phytomni.config.api_limits import (
    MAX_RESEARCH_REFERENCES,
    MAX_RESEARCH_USER_QUERY_CHARS,
    ApiLimitsConfig,
)
from mcp_server_phytomni.runtime.run_registry_models import (
    RESEARCH_FAILURE_CONTRACTS,
    RESEARCH_FAILURE_MESSAGES,
)

FORBIDDEN_MARKERS = RESEARCH_CONTRACT_FORBIDDEN_MARKERS + (
    "/tmp/",
    "agent_data/",
    "s3://",
    "access_key",
    "secret_key",
    "api_key",
    "password",
)


def _json_files(root: Path) -> tuple[Path, ...]:
    """Return all generated JSON files in stable relative-path order."""
    return tuple(sorted(root.rglob("*.json")))


def _load(path: Path) -> Any:
    """Parse one UTF-8 JSON fixture."""
    return json.loads(path.read_text(encoding="utf-8"))


def test_generation_is_byte_deterministic_and_complete(tmp_path: Path) -> None:
    """Two fresh output directories contain identical contract bytes."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate_contracts(first)
    generate_contracts(second)

    first_paths = tuple(path.relative_to(first) for path in _json_files(first))
    second_paths = tuple(
        path.relative_to(second) for path in _json_files(second)
    )
    assert (
        first_paths
        == second_paths
        == tuple(Path(relative) for relative in EXPECTED_JSON_FILES)
    )
    for relative in first_paths:
        assert (first / relative).read_bytes() == (
            second / relative
        ).read_bytes()


def test_json_bytes_and_manifest_digests_are_pinned(tmp_path: Path) -> None:
    """Every JSON is valid, newline-terminated, and digest-pinned."""
    root = tmp_path / "contracts"
    generate_contracts(root)
    manifest = _load(root / "manifest.json")
    assert manifest["protocol"] == "research_input_resolution_v1"
    assert manifest["version"] == 1
    assert manifest["trailing_newline"] is True
    entries = manifest["files"]
    assert [entry["path"] for entry in entries] == sorted(
        entry["path"] for entry in entries
    )
    assert "manifest.json" not in {entry["path"] for entry in entries}

    for path in _json_files(root):
        raw = path.read_bytes()
        assert raw.endswith(b"\n")
        assert not raw.endswith(b"\n\n")
        _load(path)
        if path.name != "manifest.json":
            entry = next(
                entry
                for entry in entries
                if entry["path"] == path.relative_to(root).as_posix()
            )
            assert entry["sha256"] == hashlib.sha256(raw).hexdigest()


def test_catalog_uses_effective_runtime_limits_and_formats(
    tmp_path: Path,
) -> None:
    """Catalog values come from the current config and scientific registry."""
    root = tmp_path / "contracts"
    generate_contracts(root)
    catalog = _load(root / "catalog.json")
    limits = ApiLimitsConfig()
    assert catalog["descriptor"] == {
        "max_user_query_chars": limits.API_MAX_USER_QUERY_CHARS,
        "max_attachments_per_request": limits.API_MAX_ATTACHMENTS_PER_REQUEST,
        "max_research_dataset_paths": limits.API_MAX_RESEARCH_DATASET_PATHS,
        "max_research_input_references": (
            limits.API_MAX_RESEARCH_INPUT_REFERENCES
        ),
        "dataset_formats": list(advertised_research_formats()),
    }
    assert catalog["limits"] == {
        "combined_references": {
            "default": limits.API_MAX_RESEARCH_INPUT_REFERENCES,
            "hard": MAX_RESEARCH_REFERENCES,
        },
        "document_conversion": {
            "max_file_bytes": MAX_FILE_BYTES,
            "max_total_bytes": MAX_TOTAL_BYTES,
        },
        "managed_references": {
            "default": limits.API_MAX_ATTACHMENTS_PER_REQUEST,
            "hard": MAX_RESEARCH_REFERENCES,
        },
        "pasted_references": {
            "default": limits.API_MAX_RESEARCH_DATASET_PATHS,
            "hard": MAX_RESEARCH_REFERENCES,
        },
        "user_query_chars": {
            "default": limits.API_MAX_USER_QUERY_CHARS,
            "hard": MAX_RESEARCH_USER_QUERY_CHARS,
        },
    }


def test_all_stable_errors_and_public_states_are_present(
    tmp_path: Path,
) -> None:
    """Every runtime stable error and lifecycle state has one fixture."""
    root = tmp_path / "contracts"
    generate_contracts(root)
    errors = {path.stem for path in (root / "errors").glob("*.json")}
    assert errors == set(ERROR_CODES)
    stages = {path.stem for path in (root / "lifecycle").glob("*.json")}
    assert stages == {Path(name).stem for name in LIFECYCLE_FILES}


def test_failed_fixture_matches_runtime_lifecycle_projection(
    tmp_path: Path,
) -> None:
    """The failed golden remains aligned with the public runtime projector."""
    root = tmp_path / "contracts"
    generate_contracts(root)
    failed = _load(root / "lifecycle/failed.json")
    failure = failed["run"]["failure"]
    code = failure["code"]

    assert code in RESEARCH_FAILURE_CONTRACTS
    stage, projected = project_research_lifecycle(
        "failed",
        failed["run"]["stage"],
        failure,
    )

    assert stage is None
    assert projected == failure
    assert projected == {
        "code": code,
        "message": RESEARCH_FAILURE_MESSAGES[code],
        "stage": failure["stage"],
        "retryable": failure["retryable"],
        "http_status_hint": failure["http_status_hint"],
    }


def test_fixture_bytes_have_no_private_markers(tmp_path: Path) -> None:
    """Synthetic fixtures never carry coordinates, credentials, or paths."""
    root = tmp_path / "contracts"
    generate_contracts(root)
    for path in _json_files(root):
        text = path.read_text(encoding="utf-8").lower()
        assert not any(marker in text for marker in FORBIDDEN_MARKERS), path
