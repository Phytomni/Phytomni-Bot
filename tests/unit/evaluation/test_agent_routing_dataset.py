# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for agent-routing evaluation dataset contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from scripts.agent_routing_eval.dataset import (
    AgentRoutingCase,
    DatasetValidationError,
    WorkbookSource,
    load_dataset,
    validate_dataset,
    validate_dataset_pair,
    verify_workbook_sources,
)

from mcp_server_phytomni.agents.network.to_ontology import (
    DEPRECATED_UPSTREAM_STATUS,
    load_to_ontology,
)
from mcp_server_phytomni.agents.shared.species_catalog import (
    supported_species_codes,
)
from mcp_server_phytomni.mcp.schemas import AGENT_TOOL_DEFINITIONS

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
DATASET_ROOT = ROOT / "evaluation" / "agent_routing" / "datasets"
_WORKBOOK_COLUMNS = {
    ("Supplementary Data 3.xlsx", "KnowledgeAgent"): ("A", "C"),
    ("Supplementary Data 4.xlsx", "KnowledgeAgent"): ("A", "B"),
    ("Supplementary Data 4.xlsx", "InSilicoResearchAgent"): ("A", "D"),
    ("Supplementary Data 5.xlsx", "BriefGeneAgent"): ("A", "C"),
    ("Supplementary Data 5.xlsx", "DataAgent"): ("A", "C"),
    ("Supplementary Data 5.xlsx", "GeneNetworkAgent"): ("A", "C"),
    ("Supplementary Data 6.xlsx", "AnalystAgent"): ("A", "B"),
    ("Supplementary Data 7.xlsx", "DeepGenomeAgent"): ("B", "D"),
    ("Supplementary Data 7.xlsx", "DigitalDesignAgent"): ("B", "D"),
    ("Supplementary Data 17.xlsx", "ReviewAgent"): ("A", "B"),
}
_REVIEW_TRANSLATIONS = {
    "test-review-006": (
        9,
        "S6",
        "What are the advantages of gene synthesis？",
        "基因合成有哪些优势？",
    ),
    "test-review-007": (
        10,
        "S7",
        "What are the applications of the epitranscriptome in breeding?",
        "表观转录组在育种中有哪些应用？",
    ),
    "test-review-008": (
        11,
        "S8",
        "What are the applications of the epigenome in breeding?",
        "表观基因组在育种中有哪些应用？",
    ),
    "test-review-009": (
        12,
        "S9",
        "How does the crosstalk between salicylic acid (SA) and jasmonic "
        "acid (JA) influence plant-virus interactions?",
        "水杨酸（SA）和茉莉酸（JA）之间的串扰如何影响植物-病毒相互作用？",
    ),
    "test-review-010": (
        13,
        "S10",
        "How does the transport and distribution of sucrose in the carbon "
        "cycle of plants affect crop yield?",
        "植物碳循环中蔗糖的运输和分配如何影响作物产量？",
    ),
}


def test_repository_routing_datasets_pass_all_integrity_rules() -> None:
    """Validate the versioned repository routing corpus."""
    dev_cases = load_dataset(DATASET_ROOT / "dev_v1.jsonl")
    test_cases = load_dataset(DATASET_ROOT / "test_v1.jsonl")
    validate_dataset(dev_cases, "dev")
    validate_dataset(test_cases, "test")
    validate_dataset_pair(dev_cases, test_cases)


def test_repository_workbook_contracts_are_column_aware() -> None:
    """Lock workbook columns and the corrected Data 7 identity boundary."""
    cases = (
        *load_dataset(DATASET_ROOT / "dev_v1.jsonl"),
        *load_dataset(DATASET_ROOT / "test_v1.jsonl"),
    )
    workbook_cases = 0
    for case in cases:
        if not isinstance(case.source, WorkbookSource):
            continue
        workbook_cases += 1
        source = case.source
        assert (
            source.source_id_column,
            source.source_text_column,
        ) == _WORKBOOK_COLUMNS[(source.workbook, case.expected_agent)]
        if source.workbook == "Supplementary Data 7.xlsx":
            assert case.expected_core_args["gene_id"] == source.source_id
            assert case.transformation.source_text != source.source_id
    assert workbook_cases == 135


def test_repository_review_translations_match_retained_topics() -> None:
    """Lock every retained Data 17 Chinese translation to its source topic."""
    cases = {
        case.case_id: case
        for case in load_dataset(DATASET_ROOT / "test_v1.jsonl")
    }
    for case_id, expected in _REVIEW_TRANSLATIONS.items():
        row, source_id, source_text, question = expected
        case = cases[case_id]
        assert isinstance(case.source, WorkbookSource)
        assert case.question == question
        assert case.transformation.kind == "faithful_translation"
        assert case.transformation.source_text == source_text
        assert case.source.row == row
        assert case.source.source_id == source_id
        assert case.source.source_id_column == "A"
        assert case.source.source_text_column == "B"


def test_data7_gene_identity_is_bound_to_column_b_source_id() -> None:
    """Keep Data 7 identity independent from the selected D-column text."""
    cases = list(load_dataset(DATASET_ROOT / "test_v1.jsonl"))
    index = next(
        index
        for index, case in enumerate(cases)
        if case.expected_agent == "DeepGenomeAgent"
    )
    case = cases[index]
    assert isinstance(case.source, WorkbookSource)
    assert case.source.workbook == "Supplementary Data 7.xlsx"
    assert case.expected_core_args["gene_id"] == case.source.source_id
    assert case.source.source_id not in (case.transformation.source_text or "")
    cases[index] = case.model_copy(
        update={
            "expected_core_args": {
                **case.expected_core_args,
                "gene_id": "not-the-column-b-identifier",
            }
        }
    )
    with pytest.raises(DatasetValidationError, match="Data 7 gene ID"):
        validate_dataset(cases, "test")


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
                "source_id_column": "A",
                "source_text_column": "B",
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


@pytest.mark.parametrize("field", ["source_id_column", "source_text_column"])
def test_workbook_source_requires_declared_columns(field: str) -> None:
    """Reject incomplete workbook provenance before source verification."""
    case = valid_case_payload()
    source = {
        "kind": "workbook",
        "workbook": "source.xlsx",
        "sheet": "Sheet1",
        "row": 1,
        "source_id": "Q_1",
        "source_id_column": "A",
        "source_text_column": "B",
    }
    source.pop(field)
    case.update(
        {
            "source": source,
            "transformation": {
                "kind": "verbatim",
                "source_text": "Plant height in rice",
            },
        }
    )
    with pytest.raises(ValidationError):
        AgentRoutingCase.model_validate(case)


def test_workbook_verifier_uses_only_declared_cells(
    tmp_path: Path,
) -> None:
    """Reject values that only occur in a non-declared column on the row."""
    case, root = _workbook_case(tmp_path)
    workbook: Any = __import__("openpyxl").load_workbook(root / "source.xlsx")
    sheet = workbook["Sheet1"]
    sheet.cell(1, 3, "Q_2")
    sheet.cell(1, 4, "Plant width in rice")
    workbook.save(root / "source.xlsx")
    workbook.close()

    source = case.source.model_copy(update={"source_id_column": "C"})
    wrong_id_column = case.model_copy(update={"source": source})
    with pytest.raises(DatasetValidationError, match="source ID"):
        verify_workbook_sources((wrong_id_column,), root)

    source = case.source.model_copy(update={"source_text_column": "D"})
    wrong_text_column = case.model_copy(update={"source": source})
    with pytest.raises(DatasetValidationError, match="source text"):
        verify_workbook_sources((wrong_text_column,), root)


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
                "source_id_column": "A",
                "source_text_column": "B",
            },
            "transformation": {"kind": "verbatim"},
        }
    )
    with pytest.raises(ValidationError, match="source_text"):
        AgentRoutingCase.model_validate(case)


def _synthetic_case(
    case_id: str,
    agent: str,
    language: str,
    overrides: dict[str, object] | None = None,
) -> AgentRoutingCase:
    """Build a deterministic synthetic case for validator tests."""
    overrides = overrides or {}
    question = overrides.get("question")
    expected_core_args = overrides.get("expected_core_args")
    source = overrides.get("source")
    transformation = overrides.get("transformation")
    if expected_core_args is None:
        model = next(
            model
            for name, _description, model in AGENT_TOOL_DEFINITIONS
            if name.value == agent
        )
        schema = model.model_json_schema()
        key = schema["required"][0]
        if key == "species_code":
            value = next(iter(supported_species_codes()))
        elif key == "to_id":
            value = next(
                entry.id
                for entry in load_to_ontology()
                if entry.status != DEPRECATED_UPSTREAM_STATUS
            )
        else:
            value = "AT1" if agent == "BriefGeneAgent" else "value"
        expected_core_args = {key: value}
    if source is None:
        if agent == "BriefGeneAgent":
            source = {
                "kind": "workbook",
                "workbook": f"{case_id}.xlsx",
                "sheet": "Sheet1",
                "row": 1,
                "source_id": case_id,
                "source_id_column": "A",
                "source_text_column": "B",
            }
            transformation = {
                "kind": "verbatim",
                "source_text": "AT1",
            }
        else:
            source = {
                "kind": "authored_chat",
                "category": "general_knowledge",
                "rationale": "Synthetic test case.",
            }
            transformation = {"kind": "authored_chat"}
    return AgentRoutingCase.model_validate(
        {
            "case_id": case_id,
            "question": question or f"Question {case_id}",
            "expected_agent": agent,
            "expected_core_args": expected_core_args,
            "language": language,
            "source": source,
            "transformation": transformation,
        }
    )


def _synthetic_split(split: str) -> list[AgentRoutingCase]:
    """Build a complete split with the required inventory and language mix."""
    english_agents = {
        name.value for name, _description, _model in AGENT_TOOL_DEFINITIONS[:5]
    }
    english_counts = {
        agent: 3 if agent in english_agents else 2
        for agent in {
            name.value for name, _description, _model in AGENT_TOOL_DEFINITIONS
        }
    }
    per_agent = 5 if split == "dev" else 10
    cases: list[AgentRoutingCase] = []
    number = 1
    for name, _description, _model in AGENT_TOOL_DEFINITIONS:
        english = english_counts[name.value] if split == "dev" else 5
        for index in range(per_agent):
            language = "en" if index < english else "zh"
            cases.append(
                _synthetic_case(f"{split}-{number:03d}", name.value, language)
            )
            number += 1
    return cases


def _replace_case(
    case: AgentRoutingCase, **updates: object
) -> AgentRoutingCase:
    """Apply updates through model validation for nested test fixtures."""
    payload = case.model_dump()
    payload.update(updates)
    return AgentRoutingCase.model_validate(payload)


def test_validate_dataset_rejects_duplicates_order_and_prefix() -> None:
    """Reject duplicate IDs, questions, ordering, and prefixes."""
    cases = _synthetic_split("dev")
    cases[1] = cases[1].model_copy(update={"case_id": cases[0].case_id})
    with pytest.raises(DatasetValidationError, match="duplicate case ID"):
        validate_dataset(cases, "dev")

    cases = _synthetic_split("dev")
    cases[1] = cases[1].model_copy(update={"question": cases[0].question})
    with pytest.raises(
        DatasetValidationError, match="duplicate exact question"
    ):
        validate_dataset(cases, "dev")

    cases = _synthetic_split("dev")
    cases[0], cases[1] = cases[1], cases[0]
    with pytest.raises(DatasetValidationError, match="not sorted"):
        validate_dataset(cases, "dev")

    cases = _synthetic_split("dev")
    cases[-1] = cases[-1].model_copy(update={"case_id": "test-999"})
    with pytest.raises(DatasetValidationError, match="wrong split prefix"):
        validate_dataset(cases, "dev")


def test_validate_dataset_rejects_inventory_language_agent_and_schema() -> (
    None
):
    """Reject split balance, unknown agents, and schema-incompatible keys."""
    cases = _synthetic_split("dev")[:-1]
    with pytest.raises(DatasetValidationError, match="expected 50"):
        validate_dataset(cases, "dev")

    cases = _synthetic_split("dev")
    cases[0] = cases[0].model_copy(update={"language": "zh"})
    with pytest.raises(DatasetValidationError, match="language count"):
        validate_dataset(cases, "dev")

    cases = _synthetic_split("dev")
    cases[0] = cases[0].model_copy(update={"expected_agent": "UnknownAgent"})
    with pytest.raises(
        DatasetValidationError, match="unknown canonical agent"
    ):
        validate_dataset(cases, "dev")

    cases = _synthetic_split("dev")
    args = {"user_query": "value", "not_a_schema_key": True}
    cases[0] = cases[0].model_copy(update={"expected_core_args": args})
    with pytest.raises(DatasetValidationError, match="not in .* schema"):
        validate_dataset(cases, "dev")


def test_validate_dataset_rejects_species_and_to_identifiers() -> None:
    """Reject unsupported species and unknown or deprecated TO identifiers."""
    cases = _synthetic_split("dev")
    network_index = next(
        i
        for i, case in enumerate(cases)
        if case.expected_agent == "GeneNetworkAgent"
    )
    cases[network_index] = cases[network_index].model_copy(
        update={"expected_core_args": {"species_code": "XXX"}}
    )
    with pytest.raises(
        DatasetValidationError, match="unsupported species_code"
    ):
        validate_dataset(cases, "dev")

    cases = _synthetic_split("dev")
    network_index = next(
        i
        for i, case in enumerate(cases)
        if case.expected_agent == "GeneNetworkAgent"
    )
    cases[network_index] = cases[network_index].model_copy(
        update={"expected_core_args": {"to_id": "TO:XXX"}}
    )
    with pytest.raises(DatasetValidationError, match="unknown or deprecated"):
        validate_dataset(cases, "dev")

    deprecated = next(
        entry.id
        for entry in load_to_ontology()
        if entry.status == DEPRECATED_UPSTREAM_STATUS
    )
    cases[network_index] = cases[network_index].model_copy(
        update={"expected_core_args": {"to_id": deprecated}}
    )
    with pytest.raises(DatasetValidationError, match="unknown or deprecated"):
        validate_dataset(cases, "dev")


def test_brief_gene_source_identity_is_required() -> None:
    """Reject a BriefGene ID absent from retained workbook source text."""
    cases = _synthetic_split("dev")
    brief_index = next(
        i
        for i, case in enumerate(cases)
        if case.expected_agent == "BriefGeneAgent"
    )
    source = cases[brief_index].source.model_copy(update={"row": 2})
    transformation = cases[brief_index].transformation.model_copy(
        update={"source_text": "unrelated-gene"}
    )
    cases[brief_index] = cases[brief_index].model_copy(
        update={"source": source, "transformation": transformation}
    )
    with pytest.raises(DatasetValidationError, match="BriefGene ID"):
        validate_dataset(cases, "dev")


def test_validate_dataset_pair_collision_policies() -> None:
    """Check cross-split question and source-row collision policies."""
    dev = _synthetic_split("dev")
    test = _synthetic_split("test")
    test[0] = test[0].model_copy(update={"question": dev[0].question})
    with pytest.raises(DatasetValidationError, match="identical question"):
        validate_dataset_pair(dev, test)

    test = _synthetic_split("test")
    shared_source = {
        "kind": "workbook",
        "workbook": "shared.xlsx",
        "sheet": "Sheet1",
        "row": 1,
        "source_id": "Q_SHARED",
        "source_id_column": "A",
        "source_text_column": "B",
    }
    shared_transformation = {
        "kind": "verbatim",
        "source_text": "shared source",
    }
    dev[0] = _replace_case(
        dev[0], source=shared_source, transformation=shared_transformation
    )
    test[0] = _replace_case(
        test[0], source=shared_source, transformation=shared_transformation
    )
    with pytest.raises(DatasetValidationError, match="source-row collision"):
        validate_dataset_pair(dev, test)

    dev = _synthetic_split("dev")
    test = _synthetic_split("test")
    analyst_dev = next(
        i
        for i, case in enumerate(dev)
        if case.expected_agent == "AnalystAgent"
    )
    analyst_test = next(
        i
        for i, case in enumerate(test)
        if case.expected_agent == "AnalystAgent"
    )
    analyst_source = {
        "kind": "workbook",
        "workbook": "analyst-shared.xlsx",
        "sheet": "Sheet1",
        "row": 1,
        "source_id": "Q_ANALYST",
        "source_id_column": "A",
        "source_text_column": "B",
    }
    analyst_transformation = {
        "kind": "verbatim",
        "source_text": "analyst source",
    }
    dev[analyst_dev] = _replace_case(
        dev[analyst_dev],
        source=analyst_source,
        transformation=analyst_transformation,
    )
    test[analyst_test] = _replace_case(
        test[analyst_test],
        source=analyst_source,
        transformation=analyst_transformation,
    )
    validate_dataset_pair(dev, test)

    dev = _synthetic_split("dev")
    test = _synthetic_split("test")
    brief_dev = next(
        i
        for i, case in enumerate(dev)
        if case.expected_agent == "BriefGeneAgent"
    )
    design_test = next(
        i
        for i, case in enumerate(test)
        if case.expected_agent == "DigitalDesignAgent"
    )
    cross_agent_source = {
        "kind": "workbook",
        "workbook": "cross-agent-shared.xlsx",
        "sheet": "Sheet1",
        "row": 1,
        "source_id": "Q_CROSS_AGENT",
        "source_id_column": "A",
        "source_text_column": "B",
    }
    cross_agent_transformation = {
        "kind": "verbatim",
        "source_text": "AT1 shared source",
    }
    dev[brief_dev] = _replace_case(
        dev[brief_dev],
        source=cross_agent_source,
        transformation=cross_agent_transformation,
    )
    test[design_test] = _replace_case(
        test[design_test],
        source=cross_agent_source,
        transformation=cross_agent_transformation,
    )
    validate_dataset_pair(dev, test)

    repeated_brief_test = next(
        i
        for i, case in enumerate(test)
        if case.expected_agent == "BriefGeneAgent"
    )
    test[repeated_brief_test] = _replace_case(
        test[repeated_brief_test],
        source=cross_agent_source,
        transformation=cross_agent_transformation,
    )
    with pytest.raises(DatasetValidationError, match="source-row collision"):
        validate_dataset_pair(dev, test)


def _workbook_case(tmp_path: Path) -> tuple[AgentRoutingCase, Path]:
    """Create a valid one-row workbook fixture and matching case."""
    openpyxl: Any = __import__("openpyxl")
    root = tmp_path / "sources"
    root.mkdir()
    workbook: Any = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(["Q_1", "Plant height in rice"])
    workbook.save(root / "source.xlsx")
    workbook.close()
    case = _synthetic_case(
        "dev-source",
        "ChatAgent",
        "en",
        {
            "source": {
                "kind": "workbook",
                "workbook": "source.xlsx",
                "sheet": "Sheet1",
                "row": 1,
                "source_id": "Q_1",
                "source_id_column": "A",
                "source_text_column": "B",
            },
            "transformation": {
                "kind": "verbatim",
                "source_text": "Plant height in rice",
            },
        },
    )
    return case, root


@pytest.mark.parametrize(
    "failure", ["workbook", "sheet", "row", "source_id", "source_text"]
)
def test_workbook_verifier_rejects_missing_physical_provenance(
    tmp_path: Path, failure: str
) -> None:
    """Reject missing workbook, sheet, row, and source ID evidence."""
    case, root = _workbook_case(tmp_path)
    if failure == "workbook":
        root.joinpath("source.xlsx").unlink()
    elif failure == "sheet":
        case = case.model_copy(
            update={
                "source": case.source.model_copy(update={"sheet": "Missing"})
            }
        )
    elif failure == "row":
        case = case.model_copy(
            update={"source": case.source.model_copy(update={"row": 2})}
        )
    elif failure == "source_id":
        case = case.model_copy(
            update={
                "source": case.source.model_copy(update={"source_id": "Q_2"})
            }
        )
    else:
        case = case.model_copy(
            update={
                "transformation": case.transformation.model_copy(
                    update={"source_text": "Missing source text"}
                )
            }
        )
    with pytest.raises(DatasetValidationError):
        verify_workbook_sources((case,), root)


def test_workbook_verifier_rejects_symlink_outside_root(
    tmp_path: Path,
) -> None:
    """Reject a basename symlink whose resolved target escapes source_root."""
    case, root = _workbook_case(tmp_path)
    outside = tmp_path / "outside.xlsx"
    outside.write_bytes(root.joinpath("source.xlsx").read_bytes())
    root.joinpath("source.xlsx").unlink()
    root.joinpath("source.xlsx").symlink_to(outside)
    with pytest.raises(DatasetValidationError, match="outside source root"):
        verify_workbook_sources((case,), root)


def test_workbook_verifier_reports_missing_openpyxl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Name the [demo] extra when openpyxl is unavailable."""
    case, root = _workbook_case(tmp_path)
    monkeypatch.setitem(sys.modules, "openpyxl", None)
    with pytest.raises(DatasetValidationError, match=r"\[demo\]"):
        verify_workbook_sources((case,), root)
