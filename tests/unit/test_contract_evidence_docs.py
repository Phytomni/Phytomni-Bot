# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Validate tracked contract-convergence evidence documents."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.capture_contract_evidence import A2UI_FIXTURES, HTTP_GOLDENS

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "docs/ops/bot-contract-convergence-ledger.md"
REGISTER = ROOT / "docs/ops/bot-compatibility-register.md"
ACCEPTANCE_RUNBOOK = ROOT / "docs/ops/bot-contract-acceptance-runbook.md"
ALLOWED_STATUSES = {
    "Unknown",
    "Needs Verification",
    "Bot Ready",
    "External Pending",
    "Accepted",
    "Blocked",
    "Rejected",
}
LEDGER_COLUMNS = {
    "Requirement",
    "Source",
    "SHA",
    "Environment",
    "Command",
    "Exit/result",
    "Sample",
    "Owner",
    "Status",
    "Blocker",
    "Rollback",
}
REGISTER_COLUMNS = {
    "Bridge",
    "Canonical replacement",
    "External owner",
    "Known consumers",
    "Status",
    "Blocker evidence",
    "Risk",
    "Review date",
    "Exit condition",
    "Rollback",
}
EXPECTED_HANDOFFS = {
    "2026-07-15-a2ui-bot-contract-handoff.md": "Lifecycle/A2UI plan",
    "2026-07-18-bot-head-web-compatibility-handoff.md": (
        "Projection/acceptance plans"
    ),
    "2026-07-21-real-user-feedback-bot-handoff.md": (
        "Locale, reports, Data/Analyst plans"
    ),
    "2026-07-24-instant-expert-routing-bot-handoff.md": "Expert plan",
    "2026-07-24-dataagent-bot-handoff.md": "Data/Analyst plan",
}
EXPECTED_BRIDGES = {
    "run_id alias",
    "formatted execution fields",
    "DeepGenome formatted report metadata",
    "top-level degraded_tracking",
    "legacy A2A Expert optional selection",
    "MCP stdio legacy response",
}
FOCUSED_FILES = (
    "tests/unit/test_lifecycle_contract.py",
    "tests/server/test_lifecycle_invariants_http.py",
    "tests/server/test_agent_capabilities.py",
    "tests/server/test_a2ui_actions_http.py",
    "tests/server/test_a2ui_review_http.py",
    "tests/server/test_a2ui_contract_fixtures.py",
    "tests/server/test_a2ui_limits.py",
    "tests/server/test_api_chat_streaming.py",
    "tests/server/test_api_runs_status.py",
    "tests/server/test_api_runs_list.py",
    "tests/server/test_locale_http.py",
    "tests/agents/test_locale_propagation.py",
    "tests/unit/test_csv_upload_validation.py",
    "tests/server/test_attachment_validation_http.py",
    "tests/unit/test_artifact_roles.py",
    "tests/unit/test_terminal_report.py",
    "tests/server/test_scientific_execution_projection.py",
    "tests/agents/test_expert_router.py",
    "tests/server/test_query_route.py",
    "tests/server/test_expert_contract_http.py",
    "tests/unit/test_stage_trace.py",
    "tests/server/test_data_agent_native_http.py",
    "tests/agents/test_chat_a2ui_graph.py",
    "tests/unit/test_contract_evidence_docs.py",
    "tests/unit/scripts/test_capture_contract_evidence.py",
)
STAGING_SMOKES = (
    "Exact DataAgent cDNA question",
    "Review pause, classic resume, and A2UI action",
    "Expert autonomous and forced",
    "Expert attachment whitelist",
    "Forced ungranted tool rejected before upload/dispatch",
    "Strict router outside allowlist returns 502 and zero dispatch",
    "Instant makes no /v1/query/route call",
    "Literal @Agent in Instant has no routing side effect",
    "Remote partial acceptance",
    "Registry-degraded accepted-task response",
    "Final reports for Analyst, Research, Design, Network, and DeepGenome",
    "Request-to-run-to-task-to-stage correlation",
)


def _table_rows(path: Path, heading: str) -> list[dict[str, str]]:
    """Parse the first Markdown table following a heading."""
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        start = next(
            index for index, line in enumerate(lines) if line == heading
        )
    except StopIteration as exc:
        raise AssertionError(f"missing heading: {heading}") from exc

    table: list[list[str]] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        if not line.strip().startswith("|"):
            if table:
                break
            continue
        cells = [
            cell.strip().strip("`")
            for cell in line.strip().strip("|").split("|")
        ]
        if cells and all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        table.append(cells)

    assert table, f"missing table after {heading}"
    headers = table[0]
    return [dict(zip(headers, row, strict=True)) for row in table[1:]]


def parse_requirement_ledger(path: Path = LEDGER) -> list[dict[str, str]]:
    """Return requirement rows from the authoritative ledger."""
    return _table_rows(path, "## Requirement ledger")


def parse_handoff_dispositions(
    path: Path = LEDGER,
) -> dict[str, dict[str, str]]:
    """Return one disposition row for each handoff."""
    rows = _table_rows(path, "## Handoff dispositions")
    return {row["Handoff"]: row for row in rows}


def parse_compatibility_register(
    path: Path = REGISTER,
) -> list[dict[str, str]]:
    """Return compatibility bridge rows from the register."""
    return _table_rows(path, "## Compatibility register")


def test_every_requirement_row_has_required_columns() -> None:
    """Every requirement row uses the fixed schema and status vocabulary."""
    rows = parse_requirement_ledger()
    assert rows
    assert all(set(row) == LEDGER_COLUMNS for row in rows)
    assert {row["Status"] for row in rows} <= ALLOWED_STATUSES
    assert all(row["SHA"] for row in rows)


def test_b_and_c_rows_are_complete() -> None:
    """The ledger contains every B and C requirement ID exactly once."""
    ids = [row["Requirement"] for row in parse_requirement_ledger()]
    assert len(ids) == len(set(ids))
    assert {f"B{number}" for number in range(1, 19)} <= set(ids)
    assert {f"C{number}" for number in range(1, 11)} <= set(ids)


def test_each_handoff_has_one_provenance_disposition() -> None:
    """All five handoffs have one provenance-only disposition."""
    dispositions = parse_handoff_dispositions()
    assert set(dispositions) == set(EXPECTED_HANDOFFS)
    assert all(
        row["Authority"] == "provenance-only" for row in dispositions.values()
    )
    assert {row["Owner plan"] for row in dispositions.values()} == set(
        EXPECTED_HANDOFFS.values()
    )


def test_compatibility_register_has_exact_initial_bridges() -> None:
    """The initial register is complete and uses allowed statuses."""
    rows = parse_compatibility_register()
    assert all(set(row) == REGISTER_COLUMNS for row in rows)
    assert {row["Bridge"] for row in rows} == EXPECTED_BRIDGES
    assert {row["Status"] for row in rows} <= ALLOWED_STATUSES
    assert all(row["Review date"] and row["Exit condition"] for row in rows)


def test_legacy_a2a_bridge_keeps_strict_route_external_pending() -> None:
    """The legacy A2A optional bridge cannot activate strict routing."""
    row = next(
        row
        for row in parse_compatibility_register()
        if row["Bridge"] == "legacy A2A Expert optional selection"
    )

    assert row["External owner"] == "A2A consumer owner"
    assert row["Known consumers"] == "Unknown"
    assert row["Status"] == "External Pending"
    assert row["Blocker evidence"] == (
        "caller lacks `allowed_tools`/`forced_tool` contract"
    )
    assert row["Review date"] == "2026-08-01"
    assert row["Exit condition"] == (
        "consumer sends constraints and paired compatibility tests pass"
    )
    assert row["Rollback"].replace("`", "") == (
        "keep Web bot.expert_enabled=false"
    )


def test_acceptance_runbook_locks_current_sha_packet() -> None:
    """The runbook names every asset and smoke in the acceptance packet."""
    text = ACCEPTANCE_RUNBOOK.read_text(encoding="utf-8")

    for status in ALLOWED_STATUSES:
        assert f"`{status}`" in text
    for path in FOCUSED_FILES + A2UI_FIXTURES + HTTP_GOLDENS:
        assert path in text
    for smoke in STAGING_SMOKES:
        assert smoke in text
    assert "Bot Ready != Accepted" in text
