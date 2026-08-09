#!/usr/bin/env python3
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Generate the sanitized Research input-resolution contract packet."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Final

from mcp_server_phytomni.agents.research.scientific_formats import (
    advertised_research_formats,
)
from mcp_server_phytomni.api.agent_capabilities import (
    MAX_FILE_BYTES as MAX_DOCUMENT_FILE_BYTES,
)
from mcp_server_phytomni.api.agent_capabilities import (
    MAX_TOTAL_BYTES as MAX_DOCUMENT_TOTAL_BYTES,
)
from mcp_server_phytomni.api.agent_capabilities import (
    build_research_input_descriptor,
)
from mcp_server_phytomni.config.api_limits import (
    MAX_RESEARCH_REFERENCES,
    MAX_RESEARCH_USER_QUERY_CHARS,
    ApiLimitsConfig,
)
from mcp_server_phytomni.runtime.run_registry_models import (
    RESEARCH_FAILURE_CODES,
    RESEARCH_FAILURE_CONTRACTS,
    RESEARCH_FAILURE_MESSAGES,
    research_failure_contract_values,
)

PROTOCOL: Final = "research_input_resolution_v1"
VERSION: Final = 1
ERROR_CODES: Final[tuple[str, ...]] = tuple(sorted(RESEARCH_FAILURE_CODES))
LIFECYCLE_FILES: Final[tuple[str, ...]] = (
    "lifecycle/cancelled.json",
    "lifecycle/execution.json",
    "lifecycle/failed.json",
    "lifecycle/input_resolution.json",
    "lifecycle/planning.json",
    "lifecycle/report_assembly.json",
    "lifecycle/succeeded.json",
)
ERROR_FILES: Final[tuple[str, ...]] = tuple(
    f"errors/{code}.json" for code in ERROR_CODES
)
EXPECTED_JSON_FILES: Final[tuple[str, ...]] = tuple(
    sorted(
        (
            "accepted_request.json",
            "catalog.json",
            "manifest.json",
            "relay_capabilities.json",
            *LIFECYCLE_FILES,
            *ERROR_FILES,
        )
    )
)

_STAGES: Final[tuple[str, ...]] = (
    "input_resolution",
    "planning",
    "execution",
    "report_assembly",
)


def _json_bytes(value: object) -> bytes:
    """Serialize one fixture with the repository's public JSON policy."""
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _write_json(root: Path, relative: str, value: object) -> None:
    """Write one deterministic UTF-8 JSON fixture."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _catalog() -> dict[str, Any]:
    """Build the catalog from effective runtime config and classifiers."""
    config = ApiLimitsConfig()
    descriptor = asdict(build_research_input_descriptor(config))
    formats = list(advertised_research_formats())
    return {
        "descriptor": descriptor,
        "formats": formats,
        "grammars": [
            "trailing_data_json",
            "fenced_data_json",
            "standalone_bucket_reference_tab_hint",
        ],
        "limits": {
            "combined_references": {
                "default": config.API_MAX_RESEARCH_INPUT_REFERENCES,
                "hard": MAX_RESEARCH_REFERENCES,
            },
            "document_conversion": {
                "max_file_bytes": MAX_DOCUMENT_FILE_BYTES,
                "max_total_bytes": MAX_DOCUMENT_TOTAL_BYTES,
            },
            "managed_references": {
                "default": config.API_MAX_ATTACHMENTS_PER_REQUEST,
                "hard": MAX_RESEARCH_REFERENCES,
            },
            "pasted_references": {
                "default": config.API_MAX_RESEARCH_DATASET_PATHS,
                "hard": MAX_RESEARCH_REFERENCES,
            },
            "user_query_chars": {
                "default": config.API_MAX_USER_QUERY_CHARS,
                "hard": MAX_RESEARCH_USER_QUERY_CHARS,
            },
        },
        "protocol": PROTOCOL,
        "stages": list(_STAGES),
        "version": VERSION,
    }


def _accepted_request() -> dict[str, Any]:
    """Build an opaque, synthetic accepted-request example."""
    return {
        "protocol": PROTOCOL,
        "request": {
            "attachments": [
                {"asset_id": "asset_fixture_001"},
                {"asset_id": "asset_fixture_002"},
            ],
            "idempotency_key": "research_fixture_idempotency_001",
            "query": "Describe the attached synthetic datasets.",
        },
        "version": VERSION,
    }


def _relay_capabilities() -> dict[str, Any]:
    """Build the metadata-only relay capability handshake example."""
    return {
        "protocol": PROTOCOL,
        "protocols": {"research_object_grant_v1": [1]},
        "research_object_grant": {
            "max_objects": MAX_RESEARCH_REFERENCES,
            "version": 1,
        },
        "service_scope": "relay:research-input",
        "version": VERSION,
    }


def _failure_projection(code: str) -> dict[str, Any]:
    """Return one valid public failure projection from runtime contracts."""
    expected = RESEARCH_FAILURE_CONTRACTS.get(code)
    if not expected:
        raise ValueError(f"missing Research failure contract: {code}")
    status, retryable, stage = sorted(expected)[0]
    contract = research_failure_contract_values(
        code,
        {
            "http_status_hint": status,
            "retryable": retryable,
            "stage": stage,
        },
    )
    if contract is None:
        raise ValueError(f"invalid Research failure contract: {code}")
    return {
        "code": code,
        "message": RESEARCH_FAILURE_MESSAGES[code],
        **contract,
    }


def _lifecycle_fixture(name: str) -> dict[str, Any]:
    """Build one sanitized public lifecycle state fixture."""
    status = {
        "input_resolution": "running",
        "planning": "running",
        "execution": "running",
        "report_assembly": "running",
        "succeeded": "succeeded",
        "failed": "failed",
        "cancelled": "cancelled",
    }[name]
    payload: dict[str, Any] = {
        "protocol": PROTOCOL,
        "run": {
            "run_id": "run_fixture_001",
            "stage": name if name in _STAGES else None,
            "status": status,
        },
        "version": VERSION,
    }
    if name == "succeeded":
        payload["run"].update(
            {
                "datasets": [
                    {
                        "confidence": "high",
                        "description": "Synthetic dataset description.",
                        "id": "dataset_001",
                    }
                ],
                "stage": None,
            }
        )
    elif name == "failed":
        payload["run"].update(
            {
                "error": "run failed",
                "failure": _failure_projection("research_dataset_not_found"),
                "stage": None,
            }
        )
    elif name == "cancelled":
        payload["run"].update(
            {
                "error": "run cancelled",
                "stage": None,
            }
        )
    return payload


def _error_fixture(code: str) -> dict[str, Any]:
    """Build one stable-code error envelope with no request material."""
    failure = _failure_projection(code)
    status = failure["http_status_hint"]
    return {
        "error": {
            "code": code,
            "http_status_hint": status,
            "message": failure["message"],
            "request_id": "req_fixture_001",
            "retryable": failure["retryable"],
            "stage": failure["stage"],
        },
        "http_status": status,
        "protocol": PROTOCOL,
        "version": VERSION,
    }


def _fixture_values() -> dict[str, object]:
    """Return all non-manifest fixture values keyed by relative path."""
    values: dict[str, object] = {
        "accepted_request.json": _accepted_request(),
        "catalog.json": _catalog(),
        "relay_capabilities.json": _relay_capabilities(),
    }
    values.update(
        {
            f"lifecycle/{name}.json": _lifecycle_fixture(name)
            for name in (
                "input_resolution",
                "planning",
                "execution",
                "report_assembly",
                "succeeded",
                "failed",
                "cancelled",
            )
        }
    )
    values.update(
        {f"errors/{code}.json": _error_fixture(code) for code in ERROR_CODES}
    )
    return values


def generate_contracts(output: Path) -> None:
    """Generate every deterministic JSON fixture below ``output``."""
    output.mkdir(parents=True, exist_ok=True)
    values = _fixture_values()
    for relative in sorted(values):
        _write_json(output, relative, values[relative])

    entries = [
        {
            "path": relative,
            "sha256": hashlib.sha256(
                _json_bytes(values[relative])
            ).hexdigest(),
            "trailing_newline": True,
        }
        for relative in sorted(values)
    ]
    manifest = {
        "files": entries,
        "manifest": {
            "path": "manifest.json",
            "sha256": None,
            "trailing_newline": True,
        },
        "protocol": PROTOCOL,
        "trailing_newline": True,
        "version": VERSION,
    }
    _write_json(output, "manifest.json", manifest)


def _parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/contracts/research-input-resolution"),
        help="directory receiving the deterministic contract fixtures",
    )
    return parser


def main() -> int:
    """Generate the configured output directory."""
    args = _parser().parse_args()
    generate_contracts(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
