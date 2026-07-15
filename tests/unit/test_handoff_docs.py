# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Contract tests for the external Web and Go integration handoff."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
HANDOFF = ROOT / "docs/handoffs/2026-07-15-deep-genome-web-go-handoff.md"
OPS_HANDOFF = (
    ROOT / "docs/handoffs/2026-07-15-bot-operations-acceptance-handoff.md"
)
MATRIX = ROOT / "docs/handoffs/2026-07-15-handoff-disposition-matrix.md"
EVIDENCE_TEMPLATE = ROOT / "docs/handoffs/evidence/record-template.json"
VALID_EVIDENCE = ROOT / "tests/fixtures/handoff/valid-evidence-record.json"
INVALID_EVIDENCE = ROOT / "tests/fixtures/handoff/invalid-evidence-record.json"
ORIGINAL_HANDOFF_NAMES = [
    "2026-05-23-python-service-consolidation.md",
    "2026-06-09-web-gateway-cutover-bot-asks.md",
    "2026-06-13-analyst-class-result-assembly-workorder.md",
    "2026-06-26-bot-progress-streaming-handoff.md",
    "2026-06-28-agui-streaming-contract.md",
    "2026-06-29-expert-route-endpoint-workorder.md",
    "2026-06-30-citation-table-ops-migration-handoff.md",
    "2026-07-08-a2ui-contract-handoff.md",
    "2026-07-08-gaussdb-unlisten-p0.md",
    "2026-07-09-early-issues-p0.md",
    "decouple-bot-handoff.md",
    "decouple-ops-handoff.md",
    "decouple-overview.md",
    "decouple-web-handoff.md",
]
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
    ROOT / "docs/handoffs/examples" / name
    for name in (
        "deep-genome-running.json",
        "deep-genome-partial-final.json",
        "deep-genome-failed-with-intermediate.json",
        "deep-genome-brief-gene-failed.json",
    )
)


@dataclass(frozen=True)
class DispositionRow:
    """One parsed row from the tracked handoff disposition matrix."""

    name: str
    status: str
    bot_evidence: str
    owner: str
    evidence: str
    remaining: str


def parse_disposition_rows(path: Path = MATRIX) -> list[DispositionRow]:
    """Parse the matrix rows without trusting its checklist prose."""
    rows: list[DispositionRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("| ---"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 6 or cells[0] == "Handoff":
            continue
        label = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", cells[0])
        rows.append(
            DispositionRow(
                name=label.strip("`"),
                status=cells[1].strip("`"),
                bot_evidence=cells[2],
                owner=cells[3],
                evidence=cells[4],
                remaining=cells[5],
            )
        )
    return rows


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
    """Keep every Web/Go/Ops dependency visible and evidence-gated."""
    text = HANDOFF.read_text(encoding="utf-8")
    for heading in (
        "DeepGenome submit and polling",
        "Revision-aware rendering",
        "Failed run with intermediate report",
        "BriefGene failure without report",
        "Analyst-class terminal reports",
        "Gateway timeout mapping",
        "A2UI passthrough",
        "Expert route migration",
        "History ETL and retirement",
        "Live Bot-Go-Web evidence",
    ):
        assert heading in text
    assert "Evidence: Not returned" in text
    assert "Authorization: Bearer $PHYTOMNI_API_KEY" in text


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
    """Keep production procedures staged, key-safe, and reversible."""
    text = OPS_HANDOFF.read_text(encoding="utf-8")
    assert (
        "phytomni-api-key create --user-id web --name production-web "
        "--expires-days 90 --scope agents"
    ) in text
    assert "phytomni-task-db prepare-deep-genome-rollback --db" in text
    assert "scripts/gauss_live_probe.py" in text
    assert "scripts/compare_gauss_queries.py" in text
    assert "staging table" in text and "atomic rename" in text
    assert "BI_LEGACY_HTTP" not in text
    assert "Evidence: Not returned" in text


def test_disposition_matrix_covers_all_original_handoffs_once() -> None:
    """Keep the inventory complete and statuses within the approved set."""
    rows = parse_disposition_rows()
    assert [row.name for row in rows] == ORIGINAL_HANDOFF_NAMES
    assert len({row.name for row in rows}) == 14
    assert {row.status for row in rows} <= {
        "Closed",
        "Superseded",
        "External Pending",
        "Blocked",
    }


def test_external_rows_cannot_claim_returned_evidence() -> None:
    """Missing owner proof must remain visibly pending."""
    for row in parse_disposition_rows():
        if row.status == "External Pending":
            assert row.evidence == "Not returned"


def test_release_acceptance_ids_are_unique_and_pending() -> None:
    """Keep every release obligation mapped exactly once."""
    text = MATRIX.read_text(encoding="utf-8")
    mapping = text.split("## Acceptance ID mapping", maxsplit=1)[1]
    for acceptance_id in ACCEPTANCE_IDS:
        assert mapping.count(f"`{acceptance_id}`") == 1
        line = next(
            line
            for line in mapping.splitlines()
            if f"`{acceptance_id}`" in line
        )
        assert "External Pending" in line
        assert "Not returned" in line


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


def test_invalid_evidence_fixture_cannot_close_unknown_item() -> None:
    """Reject unknown IDs, missing owners, and closed records without proof."""
    with pytest.raises(AssertionError):
        _assert_evidence_record(
            json.loads(INVALID_EVIDENCE.read_text(encoding="utf-8"))
        )


def test_matrix_bot_commit_evidence_exists_in_git() -> None:
    """Require local evidence hashes to resolve in the current repository."""
    rows = parse_disposition_rows()
    for row in rows:
        hashes = re.findall(
            r"(?<![0-9a-f])[0-9a-f]{7,40}(?![0-9a-f])",
            row.bot_evidence,
        )
        assert hashes, row.name
        for commit in hashes:
            result = subprocess.run(
                ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
                cwd=ROOT,
                check=False,
            )
            assert result.returncode == 0, (row.name, commit)
