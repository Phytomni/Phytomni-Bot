# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Contract tests for the external Web and Go integration handoff."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
WEB_PACKET = ROOT / "docs/handoffs/evidence/web-go-deep-genome-acceptance.md"
OPS_PACKET = ROOT / "docs/handoffs/evidence/operations-acceptance.md"
OWNER_PACKET_INDEX = ROOT / "docs/handoffs/evidence/owner-packet-index.md"
EVIDENCE_TEMPLATE = ROOT / "docs/handoffs/evidence/record-template.json"
VALID_EVIDENCE = ROOT / "tests/fixtures/handoff/valid-evidence-record.json"
INVALID_EVIDENCE = ROOT / "tests/fixtures/handoff/invalid-evidence-record.json"
ACCEPTANCE_IDS = [
    "RC-WEB-001",
    "RC-WEB-002",
    "RC-WEB-003",
    "RC-WEB-004",
    "RC-WEB-005",
    "RC-WEB-006",
    "RC-WEB-007",
    "RC-OPS-001",
    "RC-OPS-002",
    "RC-OPS-003",
    "RC-DB-001",
    "RC-DB-002",
    "RC-LIVE-001",
    "RC-REL-001",
    "RC-REL-002",
]
EXAMPLE_PATHS = tuple(
    ROOT / "docs/contracts/deep-genome" / name
    for name in (
        "running.json",
        "partial-final.json",
        "failed-with-intermediate.json",
        "brief-gene-failed.json",
    )
)


def assert_public_deep_genome_payload(payload: Any) -> None:
    """Validate the stable, owner-scoped ``GET /v1/runs`` projection."""
    assert isinstance(payload, dict)
    assert {"run_id", "agent", "status", "task_ids", "result"} <= set(payload)
    assert payload["agent"] == "deep_genome"
    assert payload["status"] in {
        "running",
        "succeeded",
        "failed",
        "input_required",
    }
    assert isinstance(payload["task_ids"], list)
    result = payload["result"]
    assert isinstance(result, dict)
    assert {
        "intermediate_report",
        "final_report",
        "report_stage",
        "report_completeness",
        "report_revision",
        "report_updated_at",
        "progress",
        "degraded",
        "degraded_reason",
        "failures",
    } <= set(result)
    assert result["report_stage"] in {
        "waiting_for_brief_gene",
        "intermediate",
        "final",
    }
    assert result["report_completeness"] in {"none", "partial", "complete"}
    assert isinstance(result["report_revision"], int)
    assert result["report_revision"] >= 0
    assert isinstance(result["progress"], dict)
    assert {
        "planning_complete",
        "brief_gene_status",
        "total",
        "planned",
        "submitted",
        "pending",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "timed_out",
    } <= set(result["progress"])
    assert isinstance(result["failures"], list)
    for failure in result["failures"]:
        assert set(failure) == {"work_item_key", "status", "message"}


def test_web_go_handoff_contains_every_external_obligation() -> None:
    """Keep the tracked Web/Go packet evidence-gated and redacted."""
    text = WEB_PACKET.read_text(encoding="utf-8")
    for heading in (
        "Submit (`RC-WEB-001`)",
        "Revision polling (`RC-WEB-002`)",
        "Failure and partial matrix (`RC-WEB-003`)",
        "Artifact projection (`RC-WEB-004`)",
        "Timeout and upstream error (`RC-WEB-005`)",
    ):
        assert heading in text
    assert "Evidence: `Not returned`" in text
    assert "Do not include child analysis IDs, bearer tokens" in text


def test_deep_genome_handoff_examples_match_public_schema() -> None:
    """Keep all copyable response examples on the public projection."""
    for path in EXAMPLE_PATHS:
        assert_public_deep_genome_payload(
            json.loads(path.read_text(encoding="utf-8"))
        )


def test_deep_genome_failure_examples_pin_report_semantics() -> None:
    """Distinguish post-profile failure from required-profile failure."""
    failed_after_profile = json.loads(EXAMPLE_PATHS[2].read_text())
    result = failed_after_profile["result"]
    assert failed_after_profile["status"] == "failed"
    assert result["progress"]["brief_gene_status"] == "succeeded"
    assert result["intermediate_report"]
    assert result["final_report"] is None

    failed_brief_gene = json.loads(EXAMPLE_PATHS[3].read_text())
    result = failed_brief_gene["result"]
    assert failed_brief_gene["status"] == "failed"
    assert result["progress"]["brief_gene_status"] == "failed"
    assert result["intermediate_report"] is None
    assert result["final_report"] is None
    assert result["report_stage"] == "waiting_for_brief_gene"
    assert result["report_completeness"] == "none"


def test_operations_handoff_has_safe_commands_and_rollbacks() -> None:
    """Keep operational acceptance staged, key-safe, and reversible."""
    text = OPS_PACKET.read_text(encoding="utf-8")
    assert "scripts/gauss_live_probe.py" in text
    assert "scripts/compare_gauss_queries.py" in text
    assert "stage and validate citation data" in text
    assert "atomically rename" in text
    assert "no secret or customer result" in text
    assert "Evidence: `Not returned`" in text


def test_release_acceptance_ids_are_unique_and_pending() -> None:
    """Keep every release obligation mapped exactly once in owner packets."""
    mapping = OWNER_PACKET_INDEX.read_text(encoding="utf-8")
    for acceptance_id in ACCEPTANCE_IDS:
        assert mapping.count(f"`{acceptance_id}`") == 1


def test_repository_guidance_links_release_evidence_contract() -> None:
    """Keep top-level guidance connected to the closure packet."""
    readme = README.read_text(encoding="utf-8")
    assert "docs/handoffs/evidence/README.md" in readme
    assert "local Bot gates do not close external" in readme


def test_evidence_template_has_required_redacted_shape() -> None:
    """Keep owner evidence records copyable and credential-free."""
    record = json.loads(EVIDENCE_TEMPLATE.read_text(encoding="utf-8"))
    assert set(record) == {
        "acceptance_id",
        "owner",
        "state",
        "commit",
        "environment",
        "command_or_request",
        "expected",
        "observed",
        "artifact",
        "redaction",
        "recorded_at",
    }
    assert record["acceptance_id"] in ACCEPTANCE_IDS
    assert record["state"] == "Prepared"
    serialized = json.dumps(record)
    assert not re.search(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{24,}", serialized)
    assert not re.search(
        r"\b(?:sk|gh[pousr])_[A-Za-z0-9_-]{20,}\b", serialized
    )


def test_evidence_credential_shape_is_rejected_by_contract() -> None:
    """Make the redaction rule executable with a synthetic bad value."""
    bad_record = {
        "observed": "Authorization: Bearer " + "a" * 24,
    }
    serialized = json.dumps(bad_record)
    assert re.search(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{24,}", serialized)


def _assert_evidence_record(record: dict[str, Any]) -> None:
    """Apply the offline evidence contract used by the fixtures."""
    required = {
        "acceptance_id",
        "owner",
        "state",
        "commit",
        "environment",
        "command_or_request",
        "expected",
        "observed",
        "artifact",
        "redaction",
        "recorded_at",
    }
    assert set(record) == required
    assert record["acceptance_id"] in ACCEPTANCE_IDS
    assert record["owner"]
    assert record["state"] in {
        "Prepared",
        "External Pending",
        "Evidence Returned",
        "Accepted",
        "Blocked",
        "Closed",
    }
    if record["state"] == "Closed":
        assert record["artifact"] not in {"", "not-returned"}
    serialized = json.dumps(record)
    assert not re.search(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{24,}", serialized)


def test_valid_evidence_fixture_satisfies_contract() -> None:
    """Accept a fully populated synthetic owner record."""
    _assert_evidence_record(
        json.loads(VALID_EVIDENCE.read_text(encoding="utf-8"))
    )
    _assert_evidence_record(
        json.loads(EVIDENCE_TEMPLATE.read_text(encoding="utf-8"))
    )


def test_invalid_evidence_fixture_cannot_close_unknown_item() -> None:
    """Reject unknown IDs, missing owners, and closed records without proof."""
    with pytest.raises(AssertionError):
        _assert_evidence_record(
            json.loads(INVALID_EVIDENCE.read_text(encoding="utf-8"))
        )


def test_closed_evidence_requires_reviewed_artifact() -> None:
    """Reject a closed record that has no returned artifact."""
    record = json.loads(VALID_EVIDENCE.read_text(encoding="utf-8"))
    record["state"] = "Closed"
    record["artifact"] = "not-returned"
    with pytest.raises(AssertionError):
        _assert_evidence_record(record)


def test_evidence_fixture_rejects_credential_shape() -> None:
    """Reject a credential-shaped value even when the rest is valid."""
    record = json.loads(VALID_EVIDENCE.read_text(encoding="utf-8"))
    record["observed"] = "Authorization: Bearer " + "a" * 24
    with pytest.raises(AssertionError):
        _assert_evidence_record(record)
