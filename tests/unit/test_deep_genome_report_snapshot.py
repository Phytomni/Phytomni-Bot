# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for pure DeepGenome report snapshot helpers."""

from __future__ import annotations

from importlib import import_module
from typing import Any

import pytest

from mcp_server_phytomni.agents.deep_genome.report_snapshot import (
    ReportRows,
    assemble_intermediate_report,
    derive_degraded_reason,
    derive_progress,
    derive_report_classification,
)
from mcp_server_phytomni.contracts.deep_genome import (
    DEEP_GENOME_PROGRESS_FIELDS,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "module_path",
    [
        "mcp_server_phytomni.runtime.deep_genome_report_snapshot",
        "mcp_server_phytomni.agents.deep_genome.report_snapshot",
    ],
)
def test_snapshot_api_has_no_operational_markdown_renderer(
    module_path: str,
) -> None:
    """Neither snapshot API exposes the obsolete notice renderer."""
    assert not hasattr(import_module(module_path), "render_failure_notices")


def _section(
    key: str,
    status: str,
    summary: str | None = None,
    order: int = 0,
) -> dict[str, Any]:
    """Build one logical section fixture."""
    return {
        "section_key": key,
        "section_kind": "brief_gene" if key == "brief_gene" else "analysis",
        "display_order": order,
        "status": status,
        "summary_markdown": summary,
        "failure_reason": None,
    }


def _item(
    key: str,
    status: str,
    summary: str | None = None,
    *,
    section_key: str | None = None,
    order: int = 0,
) -> dict[str, Any]:
    """Build one concrete work-item fixture."""
    return {
        "work_item_key": key,
        "section_key": section_key or key,
        "display_order": order,
        "status": status,
        "summary_markdown": summary,
        "failure_reason": None,
    }


def waiting_rows() -> ReportRows:
    """Return the pre-BriefGene state."""
    return ReportRows((_section("brief_gene", "running"),), ())


def failed_brief_gene_rows() -> ReportRows:
    """Return the required-profile failure state."""
    return ReportRows((_section("brief_gene", "failed"),), ())


def partial_rows() -> ReportRows:
    """Return one usable and one unavailable optional analysis."""
    sections = (
        _section("brief_gene", "succeeded", "BriefGene summary", 0),
        _section("expression", "succeeded", None, 1),
        _section("promoter", "failed", None, 2),
    )
    items = tuple(
        [
            _item(
                "promoter_design", "failed", section_key="promoter", order=2
            ),
            _item("expression", "succeeded", "Expression", order=1),
        ]
    )
    return ReportRows(sections, items)


def test_waiting_for_brief_gene_has_no_report() -> None:
    """No intermediate report is exposed before BriefGene succeeds."""
    rows = waiting_rows()
    assert assemble_intermediate_report(rows) is None
    assert derive_report_classification(rows) == (
        "waiting_for_brief_gene",
        "none",
        False,
    )


def test_failed_brief_gene_has_no_report() -> None:
    """A required-profile failure cannot produce a report."""
    rows = failed_brief_gene_rows()
    assert assemble_intermediate_report(rows) is None
    assert derive_report_classification(rows) == (
        "waiting_for_brief_gene",
        "none",
        False,
    )


def test_partial_report_order_is_completion_independent() -> None:
    """Stable display order is independent of completion order."""
    first = assemble_intermediate_report(partial_rows())
    second = assemble_intermediate_report(
        ReportRows(
            partial_rows().sections,
            tuple(reversed(partial_rows().work_items)),
        )
    )

    assert first == second
    assert first is not None
    assert first.index("BriefGene") < first.index("Expression")
    assert "Unavailable:" not in first
    assert "analysis task failed" not in first
    assert derive_degraded_reason(partial_rows(), existing_reason=None) == (
        "1 of 12 optional analyses unavailable"
    )


def test_progress_contains_all_local_work_item_states() -> None:
    """Progress exposes stable planning and state counters."""
    rows = partial_rows()
    progress = derive_progress(rows)

    assert tuple(progress) == DEEP_GENOME_PROGRESS_FIELDS
    assert progress["planning_complete"] is True
    assert progress["brief_gene_status"] == "succeeded"
    assert progress["total"] == 2
    assert progress["succeeded"] == 1
    assert progress["failed"] == 1
    assert progress["planned"] == 0
    assert progress["timed_out"] == 0


def test_complete_final_classification_is_not_degraded() -> None:
    """A successful final report is complete when every item is usable."""
    rows = ReportRows(
        (_section("brief_gene", "succeeded", "BriefGene", 0),),
        (_item("expression", "succeeded", "Expression", order=1),),
    )

    assert derive_report_classification(
        rows,
        final_report="# final",
        final_succeeded=True,
    ) == ("final", "complete", False)


def test_failure_metadata_stays_outside_scientific_markdown() -> None:
    """Neither fixed operational notices nor raw errors enter science."""
    failed_item = _item(
        "smep_analysis",
        "failed",
        section_key="smep_analysis",
    )
    failed_item["failure_reason"] = "secret DSN and upstream traceback"
    rows = ReportRows(
        (_section("brief_gene", "succeeded", "BriefGene", 0),),
        (failed_item,),
    )

    report = assemble_intermediate_report(rows)
    assert report is not None
    assert "Unavailable:" not in report
    assert "secret DSN" not in report
    assert "analysis task failed" not in report
    assert derive_progress(rows)["failed"] == 1
    assert derive_report_classification(rows) == (
        "intermediate",
        "partial",
        True,
    )
    assert (
        derive_degraded_reason(rows) == "1 of 12 optional analyses unavailable"
    )


def test_scientific_failed_experiment_prose_is_preserved() -> None:
    """Operational cleanup never strips legitimate scientific statements."""
    science = "The failed experiments support a condition-dependent effect."
    rows = ReportRows(
        (
            _section("brief_gene", "succeeded", "BriefGene", 0),
            _section("expression", "succeeded", None, 1),
        ),
        (_item("expression", "succeeded", science, order=1),),
    )
    report = assemble_intermediate_report(rows)
    assert report is not None
    assert science in report


def test_degraded_reason_keeps_the_fixed_deep_genome_denominator() -> None:
    """Custom row counts cannot change the twelve-analysis contract."""
    rows = ReportRows(
        (_section("brief_gene", "succeeded", "BriefGene", 0),),
        tuple(
            _item(f"analysis_{index}", "failed", order=index)
            for index in range(13)
        ),
    )

    assert (
        derive_degraded_reason(rows)
        == "13 of 12 optional analyses unavailable"
    )
