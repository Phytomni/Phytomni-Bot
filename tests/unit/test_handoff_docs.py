# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Contract tests for the external Web and Go integration handoff."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
HANDOFF = ROOT / "docs/handoffs/2026-07-15-deep-genome-web-go-handoff.md"
OPS_HANDOFF = (
    ROOT / "docs/handoffs/2026-07-15-bot-operations-acceptance-handoff.md"
)
EXAMPLE_PATHS = tuple(
    ROOT / "docs/handoffs/examples" / name
    for name in (
        "deep-genome-running.json",
        "deep-genome-partial-final.json",
        "deep-genome-failed-with-intermediate.json",
        "deep-genome-brief-gene-failed.json",
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
