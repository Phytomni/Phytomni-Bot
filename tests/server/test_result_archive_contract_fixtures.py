# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Contract tests for sanitized ``result_archive_v1`` run fixtures."""

from __future__ import annotations

import getpass
import gzip
import hashlib
import json
import re
import socket
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from mcp_server_phytomni.api.run_lifecycle import project_public_run_record
from mcp_server_phytomni.mcp.formatting.execution import (
    apply_compatibility_projection,
)
from mcp_server_phytomni.mcp.formatting.models import (
    ExecutionProjection,
    FormattedToolResult,
    ReportExecution,
    ResultArchiveDescriptor,
    ResultDelivery,
)
from mcp_server_phytomni.runtime.run_registry import (
    RunRecord,
    RunSpec,
    Timestamps,
)

pytestmark = pytest.mark.server

_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_ROOT = _ROOT / "tests" / "contract" / "result_archive_v1"
_DEMO_ROOT = _ROOT / "demo_data"
_FIXED_CREATED_AT = "2026-08-05T00:00:00+00:00"
_FIXED_UPDATED_AT = "2026-08-05T01:00:00+00:00"
_FIXED_EXPIRES_AT = "2026-08-12T01:00:00+00:00"
_SYNTHETIC_OWNER = "synthetic@example.invalid"
_DIGESTS = {
    "analyst": "1" * 64,
    "research": "2" * 64,
    "network": "3" * 64,
    "design": "4" * 64,
}
_PAYLOADS = {
    "analyst": "analyst_agent.json",
    "research": "in_silico_research_agent.json",
    "network": "gene_network_agent.json",
    "design": "digital_design_agent.json",
}
_PRIVATE_KEYS = {
    "delivery_internal",
    "inventory_ref",
    "source_path",
    "raw",
}
_DEMO_PREFIX = "/obs/phytomni/demo/"
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _serialized_fixture(slug: str) -> dict[str, Any]:
    """Build one deterministic fixture through the public run serializer."""
    digest = f"sha256:{_DIGESTS[slug]}"
    run_id = f"run-synthetic-{slug}"
    task_id = f"task-synthetic-{slug}-001"
    run_root = (
        "/obs/synthetic-bucket/agent_data/user_data/"
        f"{_SYNTHETIC_OWNER}/runs/{run_id}"
    )
    output_dir = f"{run_root}/children/part-001"
    archive = ResultArchiveDescriptor(
        role="result_archive",
        name=f"{slug}-results.zip",
        media_type="application/zip",
        size_bytes=4096 + list(_DIGESTS).index(slug),
        downloadable=True,
        report_context_eligible=False,
        download_ref=f"result-archive:{digest}",
    )
    delivery = ResultDelivery(
        schema_version=1,
        required=True,
        status="ready",
        revision=1,
        inventory_digest=digest,
        archive=archive,
        error_code=None,
        retryable=False,
    )
    execution = ExecutionProjection(
        tracking={"degraded": False},
        tasks=({"id": task_id, "accepted": True, "status": "succeeded"},),
        artifacts=(
            {
                "role": "scientific_report",
                "name": f"{slug}-summary.md",
                "media_type": "text/markdown",
                "size_bytes": 1024,
                "downloadable": True,
                "report_context_eligible": True,
                "download_ref": f"{output_dir}/{slug}-summary.md",
            },
        ),
        output_dirs=(run_root,),
        report=ReportExecution(
            state="final",
            degraded=False,
            source_artifact_count=1,
        ),
        delivery=delivery,
    )
    formatted = apply_compatibility_projection(
        FormattedToolResult(
            answer=f"# Synthetic {slug.title()} Result\n\nArchive ready.",
        ),
        execution,
    )
    stored_result = {
        "formatted": asdict(formatted),
        "execution": asdict(execution),
        "delivery_internal": {
            "inventory_ref": f"{output_dir}/private-inventory.json",
            "attempts_claimed": 1,
            "last_error_code": None,
        },
        "raw": {"inventory_ref": f"{output_dir}/private-inventory.json"},
    }
    record = RunRecord(
        spec=RunSpec(run_id, _SYNTHETIC_OWNER, slug, "remote"),
        status="succeeded",
        result=json.loads(json.dumps(stored_result)),
        error=None,
        timestamps=Timestamps(
            created_at=_FIXED_CREATED_AT,
            updated_at=_FIXED_UPDATED_AT,
            expires_at=_FIXED_EXPIRES_AT,
        ),
        task_ids=(task_id,),
    )
    return project_public_run_record(record)


def _load_json(path: Path) -> Any:
    """Load one UTF-8 JSON fixture."""
    return json.loads(path.read_text(encoding="utf-8"))


def _walk_strings(value: Any) -> Iterable[str]:
    """Yield every mapping key and scalar string recursively."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                yield key
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)
    elif isinstance(value, str):
        yield value


def _walk_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    """Yield every nested mapping recursively."""
    if isinstance(value, Mapping):
        yield value
        for item in value.values():
            yield from _walk_mappings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_mappings(item)


@pytest.mark.parametrize("slug", tuple(_DIGESTS))
def test_result_archive_fixture_matches_public_serializer(slug: str) -> None:
    """Committed JSON remains byte-shaped by the public run serializer."""
    fixture = _load_json(_FIXTURE_ROOT / f"{slug}.json")

    assert fixture == _serialized_fixture(slug)
    assert fixture["agent"] == slug
    assert fixture["user_id"] == _SYNTHETIC_OWNER
    assert fixture["status"] == "succeeded"
    result = fixture["result"]
    assert result["formatted"]["answer"].strip()
    assert len(result["execution"]["output_dirs"]) == 1
    assert "artifacts" not in result


@pytest.mark.parametrize("slug", tuple(_DIGESTS))
def test_result_archive_fixture_is_ready_and_public(slug: str) -> None:
    """Each fixture exposes one valid archive and no private coordination."""
    fixture = _load_json(_FIXTURE_ROOT / f"{slug}.json")
    result = fixture["result"]
    delivery = result["execution"]["delivery"]
    archive = delivery["archive"]
    digest = f"sha256:{_DIGESTS[slug]}"

    assert delivery == {
        "schema_version": 1,
        "required": True,
        "status": "ready",
        "revision": 1,
        "inventory_digest": digest,
        "archive": archive,
        "error_code": None,
        "retryable": False,
    }
    assert archive == {
        "role": "result_archive",
        "name": f"{slug}-results.zip",
        "media_type": "application/zip",
        "size_bytes": 4096 + list(_DIGESTS).index(slug),
        "downloadable": True,
        "report_context_eligible": False,
        "download_ref": f"result-archive:{digest}",
    }
    archive_descriptors = [
        item
        for item in _walk_mappings(fixture)
        if item.get("role") == "result_archive"
    ]
    assert archive_descriptors == [archive]
    assert not any(
        key in _PRIVATE_KEYS
        for mapping in _walk_mappings(fixture)
        for key in mapping
    )

    serialized = json.dumps(fixture, sort_keys=True)
    local_values = {
        socket.gethostname(),
        getpass.getuser(),
        Path.home().name,
    }
    assert all(
        not value or value == "synthetic" or value not in serialized
        for value in local_values
    )
    assert set(_EMAIL.findall(serialized)) == {_SYNTHETIC_OWNER}
    assert all(
        not value.startswith("/obs/")
        or value.startswith("/obs/synthetic-bucket/")
        for value in _walk_strings(fixture)
    )


def test_demo_payload_references_are_manifest_closed() -> None:
    """Every four-agent demo OBS reference resolves to a tracked file."""
    manifest = _load_json(_DEMO_ROOT / "manifest.json")
    files = manifest["files"]
    referenced: set[str] = set()

    for payload_name in _PAYLOADS.values():
        payload = _load_json(_DEMO_ROOT / "payloads" / payload_name)
        for value in _walk_strings(payload):
            if not value.startswith(_DEMO_PREFIX):
                continue
            relative_path = value.removeprefix(_DEMO_PREFIX)
            referenced.add(relative_path)
            path = _DEMO_ROOT / relative_path
            assert path.is_file()
            assert not path.is_symlink()
            metadata = files[relative_path]
            content = path.read_bytes()
            assert metadata["size_bytes"] == len(content)
            assert metadata["sha256"] == hashlib.sha256(content).hexdigest()

    assert referenced


@pytest.mark.parametrize(
    "relative_path",
    (
        "sequences/sample_rep1.fastq.gz",
        "sequences/sample_rep2.fastq.gz",
    ),
)
def test_analyst_fastq_demo_inputs_are_valid(relative_path: str) -> None:
    """Generated Analyst gzip inputs contain complete 50 bp FASTQ rows."""
    path = _DEMO_ROOT / relative_path
    with gzip.open(path, "rt", encoding="ascii", newline="") as stream:
        lines = stream.read().splitlines()

    assert lines and len(lines) % 4 == 0
    for index in range(0, len(lines), 4):
        record = lines[slice(index, index + 4)]
        header, sequence, separator, quality = record
        assert header.startswith("@")
        assert separator == "+"
        assert sequence and set(sequence) <= set("ACGTN")
        assert len(sequence) == len(quality) == 50
