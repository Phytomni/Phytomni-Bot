# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Typed records and integrity checks for agent-routing evaluation data."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
    model_validator,
)

from mcp_server_phytomni.agents.network.to_ontology import (
    DEPRECATED_UPSTREAM_STATUS,
    load_to_ontology,
)
from mcp_server_phytomni.agents.shared.species_catalog import (
    supported_species_codes,
)
from mcp_server_phytomni.mcp.schemas import AGENT_TOOL_DEFINITIONS

_MODEL_BY_AGENT = {
    name.value: model for name, _description, model in AGENT_TOOL_DEFINITIONS
}
_ACTIVE_TO_IDS = frozenset(
    entry.id
    for entry in load_to_ontology()
    if entry.status != DEPRECATED_UPSTREAM_STATUS
)
_EXPECTED_PER_AGENT = {"dev": 5, "test": 10}
_EXPECTED_TOTAL = {"dev": 50, "test": 100}
_DEV_ENGLISH_COUNT = {
    "ChatAgent": 3,
    "KnowledgeAgent": 3,
    "DataAgent": 3,
    "AnalystAgent": 3,
    "ReviewAgent": 3,
    "BriefGeneAgent": 2,
    "DeepGenomeAgent": 2,
    "InSilicoResearchAgent": 2,
    "DigitalDesignAgent": 2,
    "GeneNetworkAgent": 2,
}
_PLACEHOLDER_RE = re.compile(
    r"XXX|TO:XXX|<article title>|\{\{gene\}\}|\[article title\]",
    re.IGNORECASE,
)
_GENE_KEYS = frozenset({"gene", "gene_id"})


def _validate_string(value: object) -> object:
    if isinstance(value, str) and (not value or value != value.strip()):
        raise ValueError("text values must be nonblank and stripped")
    return value


def _validate_json_strings(value: object) -> object:
    if isinstance(value, str):
        return _validate_string(value)
    if isinstance(value, dict):
        for nested in value.values():
            _validate_json_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            _validate_json_strings(nested)
    return value


class DatasetValidationError(ValueError):
    """Raised when an evaluation dataset violates its integrity contract."""


class WorkbookSource(BaseModel):
    """Physical location of an authored case in a workbook."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["workbook"]
    workbook: str
    sheet: str
    row: int = Field(gt=0)
    source_id: str
    source_id_column: Literal["A", "B", "C", "D"]
    source_text_column: Literal["A", "B", "C", "D"]

    _strings = field_validator("*")(
        classmethod(lambda cls, value: _validate_string(value))
    )


class AuthoredChatSource(BaseModel):
    """Provenance metadata for a case authored directly as chat data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["authored_chat"]
    category: Literal[
        "calculation",
        "general_knowledge",
        "rewriting",
        "translation",
        "workplace_communication",
    ]
    rationale: str

    _strings = field_validator("*")(
        classmethod(lambda cls, value: _validate_string(value))
    )


class TransformationRecord(BaseModel):
    """How the retained question was produced from its source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[
        "verbatim",
        "faithful_translation",
        "template",
        "authored_chat",
    ]
    template_id: str | None = None
    source_text: str | None = None

    _strings = field_validator("*")(
        classmethod(lambda cls, value: _validate_string(value))
    )


class AgentRoutingCase(BaseModel):
    """One closed, provenance-aware agent-routing evaluation case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    question: str
    expected_agent: str
    expected_core_args: dict[str, JsonValue]
    language: Literal["en", "zh"]
    source: WorkbookSource | AuthoredChatSource = Field(discriminator="kind")
    transformation: TransformationRecord

    @field_validator(
        "case_id",
        "question",
        "expected_agent",
        "language",
        mode="before",
    )
    @classmethod
    def _validate_text(cls, value: object) -> object:
        return _validate_string(value)

    @field_validator("expected_core_args", mode="before")
    @classmethod
    def _validate_core_arg_strings(cls, value: object) -> object:
        return _validate_json_strings(value)

    @model_validator(mode="after")
    def _validate_provenance(self) -> AgentRoutingCase:
        if isinstance(self.source, WorkbookSource):
            if not self.transformation.source_text:
                raise ValueError("workbook cases require source_text")
        else:
            if self.transformation.kind != "authored_chat":
                raise ValueError(
                    "authored_chat cases require an authored_chat "
                    "transformation"
                )
            if (
                self.transformation.template_id is not None
                or self.transformation.source_text is not None
            ):
                raise ValueError(
                    "authored_chat cases cannot carry workbook fields"
                )
        return self


def load_dataset(path: Path) -> tuple[AgentRoutingCase, ...]:
    """Load strict JSONL records and report the physical failing line."""
    cases: list[AgentRoutingCase] = []
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            raise DatasetValidationError(
                f"{path.name}: blank line {line_number}"
            )
        try:
            cases.append(AgentRoutingCase.model_validate_json(raw))
        except (ValidationError, ValueError) as exc:
            raise DatasetValidationError(
                f"{path.name}: invalid line {line_number}"
            ) from exc
    return tuple(cases)


def _source_key(case: AgentRoutingCase) -> tuple[str, str, int] | None:
    if not isinstance(case.source, WorkbookSource):
        return None
    return (
        case.source.workbook,
        case.source.sheet,
        case.source.row,
    )


def _validate_case_ids(
    records: tuple[AgentRoutingCase, ...], split: str
) -> None:
    case_ids = [case.case_id for case in records]
    if len(set(case_ids)) != len(case_ids):
        raise DatasetValidationError(f"{split}: duplicate case ID")
    if case_ids != sorted(case_ids):
        raise DatasetValidationError(f"{split}: case IDs are not sorted")
    if any(not case.case_id.startswith(f"{split}-") for case in records):
        raise DatasetValidationError(
            f"{split}: case ID has wrong split prefix"
        )


def _validate_agents(
    records: tuple[AgentRoutingCase, ...], split: Literal["dev", "test"]
) -> None:
    agent_counts = Counter(case.expected_agent for case in records)
    unknown_agents = set(agent_counts) - set(_MODEL_BY_AGENT)
    if unknown_agents:
        raise DatasetValidationError(f"{split}: unknown canonical agent")
    for agent in _MODEL_BY_AGENT:
        expected = _EXPECTED_PER_AGENT[split]
        if agent_counts[agent] != expected:
            raise DatasetValidationError(
                f"{split}: {agent} expected {expected} cases, "
                f"got {agent_counts[agent]}"
            )


def _validate_questions(
    records: tuple[AgentRoutingCase, ...], split: str
) -> None:
    questions = [case.question for case in records]
    if len(set(questions)) != len(questions):
        raise DatasetValidationError(f"{split}: duplicate exact question")
    if any(_PLACEHOLDER_RE.search(question) for question in questions):
        raise DatasetValidationError(
            f"{split}: unresolved question placeholder"
        )


def _validate_gene_identity(
    case: AgentRoutingCase,
    split: str,
) -> None:
    agent = case.expected_agent
    expected_core_args = case.expected_core_args
    if (
        isinstance(case.source, WorkbookSource)
        and case.source.workbook == "Supplementary Data 7.xlsx"
        and agent in {"DeepGenomeAgent", "DigitalDesignAgent"}
    ):
        gene_id = expected_core_args.get("gene_id")
        if gene_id != case.source.source_id:
            raise DatasetValidationError(
                f"{split}: Data 7 gene ID must equal workbook source ID"
            )
        return
    gene_values = [
        expected_core_args[key]
        for key in _GENE_KEYS
        if key in expected_core_args
    ]
    if agent == "BriefGeneAgent":
        gene_values.append(expected_core_args.get("user_query"))
    for gene_id in gene_values:
        source_text = case.transformation.source_text or ""
        if not isinstance(gene_id, str) or gene_id not in source_text:
            label = "BriefGene ID" if agent == "BriefGeneAgent" else "gene ID"
            raise DatasetValidationError(
                f"{split}: {label} absent from retained source text"
            )


def _validate_case_arguments(
    records: tuple[AgentRoutingCase, ...], split: Literal["dev", "test"]
) -> None:
    for agent, model in _MODEL_BY_AGENT.items():
        agent_cases = [
            case for case in records if case.expected_agent == agent
        ]
        language_counts = Counter(case.language for case in agent_cases)
        if language_counts != Counter(en=5, zh=5 if split == "test" else 0):
            if split == "dev":
                expected_en = _DEV_ENGLISH_COUNT[agent]
                expected_zh = _EXPECTED_PER_AGENT[split] - expected_en
            else:
                expected_en, expected_zh = 5, 5
            if (
                language_counts["en"] != expected_en
                or language_counts["zh"] != expected_zh
            ):
                raise DatasetValidationError(
                    f"{split}: incorrect language count for {agent}"
                )
        properties = model.model_json_schema().get("properties", {})
        for case in agent_cases:
            unknown_keys = set(case.expected_core_args) - set(properties)
            if unknown_keys:
                raise DatasetValidationError(
                    f"{split}: core argument is not in {agent} schema"
                )
            for key, value in case.expected_core_args.items():
                if (
                    key == "species_code"
                    and value not in supported_species_codes()
                ):
                    raise DatasetValidationError(
                        f"{split}: unsupported species_code"
                    )
                if key == "to_id" and value not in _ACTIVE_TO_IDS:
                    raise DatasetValidationError(
                        f"{split}: unknown or deprecated TO ID"
                    )
            _validate_gene_identity(case, split)


def validate_dataset(
    cases: tuple[AgentRoutingCase, ...] | list[AgentRoutingCase],
    split: Literal["dev", "test"],
) -> None:
    """Validate counts, schemas, placeholders, and canonical identifiers."""
    records = tuple(cases)
    expected_count = _EXPECTED_TOTAL[split]
    if len(records) != expected_count:
        raise DatasetValidationError(
            f"{split}: expected {expected_count} cases, got {len(records)}"
        )
    _validate_case_ids(records, split)
    _validate_agents(records, split)
    _validate_questions(records, split)
    _validate_case_arguments(records, split)


def validate_dataset_pair(
    dev_cases: tuple[AgentRoutingCase, ...] | list[AgentRoutingCase],
    test_cases: tuple[AgentRoutingCase, ...] | list[AgentRoutingCase],
) -> None:
    """Validate both splits and their cross-split uniqueness boundaries."""
    validate_dataset(dev_cases, "dev")
    validate_dataset(test_cases, "test")
    dev = tuple(dev_cases)
    test = tuple(test_cases)
    if set(case.question for case in dev) & set(
        case.question for case in test
    ):
        raise DatasetValidationError("dev/test: identical question text")
    seen: dict[tuple[str, str, int], set[str]] = {}
    for case in (*dev, *test):
        key = _source_key(case)
        if key is None:
            continue
        agents = seen.setdefault(key, set())
        if case.expected_agent != "AnalystAgent" and (
            case.expected_agent in agents
        ):
            raise DatasetValidationError("dev/test: source-row collision")
        agents.add(case.expected_agent)


def _workbook_path(
    source: WorkbookSource, source_root: Path, resolved_root: Path
) -> Path:
    if Path(source.workbook).name != source.workbook:
        raise DatasetValidationError("workbook source must be a basename")
    workbook_path = source_root / source.workbook
    try:
        workbook_path.resolve().relative_to(resolved_root)
    except ValueError as exc:
        raise DatasetValidationError(
            "workbook source is outside source root"
        ) from exc
    if not workbook_path.is_file():
        raise DatasetValidationError("workbook source is missing")
    return workbook_path


def _workbook_cell_value(
    source: WorkbookSource, workbook: Any, column: str
) -> str:
    if source.sheet not in workbook.sheetnames:
        raise DatasetValidationError("workbook sheet is missing")
    sheet = workbook[source.sheet]
    if source.row > sheet.max_row:
        raise DatasetValidationError("workbook physical row is missing")
    try:
        value = next(
            sheet.iter_rows(
                min_row=source.row,
                max_row=source.row,
                min_col=ord(column) - ord("A") + 1,
                max_col=ord(column) - ord("A") + 1,
                values_only=True,
            )
        )[0]
    except StopIteration as exc:
        raise DatasetValidationError(
            "workbook physical row is missing"
        ) from exc
    if value is None:
        column_index = ord(column) - ord("A") + 1
        for merged_range in sheet.merged_cells.ranges:
            if (
                merged_range.min_row <= source.row <= merged_range.max_row
                and merged_range.min_col
                <= column_index
                <= merged_range.max_col
            ):
                value = sheet.cell(
                    merged_range.min_row, merged_range.min_col
                ).value
                break
    return "" if value is None else str(value).strip()


def _verify_workbook_case(
    case: AgentRoutingCase,
    source_root: Path,
    resolved_root: Path,
    load_workbook: Any,
    workbooks: dict[str, Any],
) -> None:
    if not isinstance(case.source, WorkbookSource):
        return
    source = case.source
    workbook_path = _workbook_path(source, source_root, resolved_root)
    if source.workbook not in workbooks:
        with workbook_path.open("rb") as source_file:
            workbooks[source.workbook] = load_workbook(
                source_file, data_only=True
            )
    workbook = workbooks[source.workbook]
    source_id = _workbook_cell_value(source, workbook, source.source_id_column)
    if source.source_id != source_id:
        raise DatasetValidationError("workbook source ID is absent")
    source_text = case.transformation.source_text
    workbook_text = _workbook_cell_value(
        source, workbook, source.source_text_column
    )
    if source_text is None or source_text != workbook_text:
        raise DatasetValidationError("workbook source text is absent")


def verify_workbook_sources(
    cases: tuple[AgentRoutingCase, ...] | list[AgentRoutingCase],
    source_root: Path,
) -> None:
    """Verify IDs and retained text using selected workbook rows only."""
    try:
        openpyxl: Any = __import__("openpyxl")
        load_workbook = getattr(openpyxl, "load_workbook")
    except ImportError as exc:
        raise DatasetValidationError(
            "workbook verification requires the repository [demo] extra"
        ) from exc

    workbooks: dict[str, Any] = {}
    resolved_root = source_root.resolve()
    try:
        for case in cases:
            _verify_workbook_case(
                case,
                source_root,
                resolved_root,
                load_workbook,
                workbooks,
            )
    finally:
        for workbook in workbooks.values():
            workbook.close()
