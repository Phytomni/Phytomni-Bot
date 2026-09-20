# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Public DeepGenome contract fixture tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mcp_server_phytomni.contracts.deep_genome import (
    DEEP_GENOME_PROGRESS_FIELDS,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = ROOT / "docs/contracts/deep-genome"
EVIDENCE_MAP = ROOT / "docs/ops/deep-genome-rc-web-evidence.md"
EXAMPLE_PATHS = tuple(
    CONTRACT_ROOT / name
    for name in (
        "running.json",
        "partial-final.json",
        "failed-with-intermediate.json",
        "brief-gene-failed.json",
    )
)

RC_WEB_CASES = {
    "RC-WEB-001": (
        "running.json",
        "test_arun_returns_immediately_with_submit_envelope",
        "test_fake_backend_persists_acceptance_before_poll_and_snapshots",
    ),
    "RC-WEB-002": (
        "running.json",
        "test_partial_fake_backend_publishes_final_report_after_cas",
    ),
    "RC-WEB-003": (
        "failed-with-intermediate.json",
        "test_post_profile_failure_keeps_intermediate_report",
        "test_all_optional_failures_preserve_profile_and_fail_owner",
    ),
    "RC-WEB-004": (
        "partial-final.json",
        "test_deep_genome_snapshot_uses_canonical_split",
        "test_terminal_report_metadata_survives_run_projection",
    ),
    "RC-WEB-005": (
        "test_transition_sink_persists_every_local_status",
        "test_poll_contract_emits_ordered_transitions_and_summary",
        "test_dispatch_coordinator_receives_effective_poll_id",
    ),
}


def assert_public_deep_genome_payload(payload: Any) -> None:
    """Validate the stable owner-scoped DeepGenome snapshot projection."""
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
    assert set(DEEP_GENOME_PROGRESS_FIELDS) <= set(result["progress"])
    assert isinstance(result["failures"], list)
    for failure in result["failures"]:
        assert set(failure) == {"work_item_key", "status", "message"}


def test_public_deep_genome_fixtures_match_snapshot_schema() -> None:
    """Keep every copyable fixture on the public snapshot projection."""
    for path in EXAMPLE_PATHS:
        assert_public_deep_genome_payload(
            json.loads(path.read_text(encoding="utf-8"))
        )


def test_post_profile_failure_keeps_intermediate_report() -> None:
    """A later failure preserves the last useful report."""
    payload = json.loads(
        (CONTRACT_ROOT / "failed-with-intermediate.json").read_text(
            encoding="utf-8"
        )
    )
    result = payload["result"]
    assert payload["status"] == "failed"
    assert result["progress"]["brief_gene_status"] == "succeeded"
    assert result["intermediate_report"]
    assert result["final_report"] is None


def test_brief_gene_failure_has_no_optional_report_or_submission() -> None:
    """The required profile barrier fails before optional work is launched."""
    payload = json.loads(
        (CONTRACT_ROOT / "brief-gene-failed.json").read_text(encoding="utf-8")
    )
    result = payload["result"]
    assert payload["status"] == "failed"
    assert result["progress"]["brief_gene_status"] == "failed"
    assert result["intermediate_report"] is None
    assert result["final_report"] is None
    assert result["report_stage"] == "waiting_for_brief_gene"
    assert result["report_completeness"] == "none"
    assert result["progress"]["planned"] == 0
    assert result["progress"]["submitted"] == 0


def test_rc_web_cases_are_mapped_to_local_evidence_and_external_pending() -> (
    None
):
    """Every RC-WEB case has a local proof and external boundary."""
    evidence_map = EVIDENCE_MAP.read_text(encoding="utf-8")

    for case, evidence in RC_WEB_CASES.items():
        assert f"`{case}`" in evidence_map
        for item in evidence:
            assert f"`{item}`" in evidence_map
    assert evidence_map.count("External Pending:") == len(RC_WEB_CASES)
