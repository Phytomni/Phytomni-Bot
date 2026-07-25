# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Validate tracked contract-convergence evidence documents."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "docs/ops/bot-contract-convergence-ledger.md"
REGISTER = ROOT / "docs/ops/bot-compatibility-register.md"
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
    "A2A omitted constraints",
    "MCP stdio legacy response",
}


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
