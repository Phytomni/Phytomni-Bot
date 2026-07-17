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

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = ROOT / "docs/contracts/deep-genome"
EXAMPLE_PATHS = tuple(
    CONTRACT_ROOT / name
    for name in (
        "running.json",
        "partial-final.json",
        "failed-with-intermediate.json",
        "brief-gene-failed.json",
    )
)


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
