# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for agent-routing evaluation dataset contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from scripts.agent_routing_eval.dataset import (
    AgentRoutingCase,
    DatasetValidationError,
    load_dataset,
    verify_workbook_sources,
)

pytestmark = pytest.mark.unit


def valid_case_payload() -> dict[str, object]:
    """Return a minimal valid case payload."""
    return {
        "case_id": "dev-001",
        "question": "What is plant height?",
        "expected_agent": "ChatAgent",
        "expected_core_args": {"user_query": "What is plant height?"},
        "language": "en",
        "source": {
            "kind": "authored_chat",
            "category": "general_knowledge",
            "rationale": "Direct general question.",
        },
        "transformation": {"kind": "authored_chat"},
    }


def test_case_rejects_unknown_fields() -> None:
    """Reject fields outside the closed case contract."""
    payload = valid_case_payload()
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        AgentRoutingCase.model_validate(payload)


def test_load_dataset_reports_physical_line(tmp_path: Path) -> None:
    """Report the physical JSONL line containing invalid data."""
    path = tmp_path / "bad.jsonl"
    path.write_text('{"case_id":"broken"}\nnot-json\n', encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="line 1"):
        load_dataset(path)


def test_load_dataset_rejects_blank_line(tmp_path: Path) -> None:
    """Reject blank physical lines in a JSONL dataset."""
    path = tmp_path / "blank.jsonl"
    path.write_text(
        json.dumps(valid_case_payload()) + "\n\n", encoding="utf-8"
    )
    with pytest.raises(DatasetValidationError, match="blank line 2"):
        load_dataset(path)


def test_workbook_verifier_checks_row_id_and_source_text(
    tmp_path: Path,
) -> None:
    """Verify the source ID and retained text on the selected row."""
    openpyxl: Any = __import__("openpyxl")

    source_root = tmp_path / "Supplementary Data"
    source_root.mkdir()
    workbook: Any = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(["title"])
    sheet.append(["ID", "Query"])
    sheet.append([None, None])
    sheet.append(["Q_1", "Plant height in rice"])
    workbook.save(source_root / "Supplementary Data 4.xlsx")
    workbook.close()

    case = valid_case_payload()
    case.update(
        {
            "case_id": "dev-002",
            "source": {
                "kind": "workbook",
                "workbook": "Supplementary Data 4.xlsx",
                "sheet": "Sheet1",
                "row": 4,
                "source_id": "Q_1",
            },
            "transformation": {
                "kind": "verbatim",
                "source_text": "Plant height in rice",
            },
        }
    )
    verify_workbook_sources(
        (AgentRoutingCase.model_validate(case),), source_root
    )


def test_workbook_verifier_rejects_missing_source_text() -> None:
    """Reject workbook records without retained source text."""
    case = valid_case_payload()
    case.update(
        {
            "source": {
                "kind": "workbook",
                "workbook": "missing.xlsx",
                "sheet": "Sheet1",
                "row": 1,
                "source_id": "Q_1",
            },
            "transformation": {"kind": "verbatim"},
        }
    )
    with pytest.raises(ValidationError, match="source_text"):
        AgentRoutingCase.model_validate(case)
