# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Format Phytomni MCP tool responses for client-facing consumers."""

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

ReferenceResolver = Callable[[str], Mapping[str, Any] | None]
FieldMapper = Callable[[Sequence[str]], Sequence[str]]

_CITATION_PATTERN = re.compile(r"\[(?:[A-Za-z]+[: ]?)?(\d+(?:,\s*\d+)*)\]")


@dataclass(frozen=True)
class FormattedToolResult:
    """Normalized client-facing representation of one tool response."""

    answer: str
    follow_up_questions: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    references: tuple[Mapping[str, Any], ...] = ()


def format_tool_result(
    tool_name: str,
    payload: Any,
    *,
    arguments: Mapping[str, Any] | None = None,
    field_mapper: FieldMapper | None = None,
    reference_resolver: ReferenceResolver | None = None,
) -> FormattedToolResult:
    """Format one MCP tool payload using the registered tool name."""
    normalized_name = _normalize_tool_name(tool_name)
    content = _payload_mapping(payload)
    if normalized_name == "ChatAgent":
        result = _format_message_result(content)
    elif normalized_name in {
        "KnowledgeAgent",
        "ReviewAgent",
        "BriefGeneAgent",
    }:
        result = _format_cited_message_result(content, reference_resolver)
    elif normalized_name == "DataAgent":
        result = _format_data_result(content, field_mapper)
    elif normalized_name == "AnalystAgent":
        result = _format_task_result(content)
    elif normalized_name == "DeepGenomeAgent":
        result = _format_deep_genome_result(content, arguments)
    elif normalized_name == "GeneNetworkAgent":
        result = _format_task_result(_network_task_payload(content))
    elif normalized_name == "DigitalDesignAgent":
        result = _format_design_result(content)
    else:
        result = FormattedToolResult(answer=_json_dumps(payload))
    return result


def _normalize_tool_name(tool_name: str) -> str:
    """Return the canonical public MCP tool name."""
    aliases = {
        "AnalysisAgents": "AnalystAgent",
        "ChatAgents": "ChatAgent",
        "DatabaseAgents": "DataAgent",
        "KnowledgeAgents": "KnowledgeAgent",
        "ReviewAgents": "ReviewAgent",
    }
    return aliases.get(tool_name, tool_name)


def _payload_mapping(payload: Any) -> Mapping[str, Any]:
    """Return a mapping payload or an empty mapping for non-object values."""
    return payload if isinstance(payload, Mapping) else {}


def _format_message_result(content: Mapping[str, Any]) -> FormattedToolResult:
    """Format an OpenAI-style response with one assistant message."""
    message = _first_message(content)
    return FormattedToolResult(
        answer=str(message.get("content", "")),
        follow_up_questions=_follow_up_questions(message),
    )


def _format_cited_message_result(
    content: Mapping[str, Any],
    reference_resolver: ReferenceResolver | None,
) -> FormattedToolResult:
    """Format an OpenAI-style response and normalize cited documents."""
    message = _first_message(content)
    answer = str(message.get("content", ""))
    doc_list = _doc_list(message)
    if not doc_list:
        return FormattedToolResult(
            answer=answer,
            follow_up_questions=_follow_up_questions(message),
        )

    normalized_answer, references = _normalize_citations(
        answer,
        doc_list,
        reference_resolver,
    )
    structured_answer = {
        "content": normalized_answer,
        "doc_list": list(references),
    }
    return FormattedToolResult(
        answer=_json_dumps(structured_answer),
        follow_up_questions=_follow_up_questions(message),
        references=references,
    )


def _format_data_result(
    content: Mapping[str, Any],
    field_mapper: FieldMapper | None,
) -> FormattedToolResult:
    """Format natural-language SQL table output."""
    headers = [
        str(column.get("caption") or column.get("name") or "")
        for column in _mapping_sequence(content.get("header"))
    ]
    mapped_headers = (
        list(field_mapper(headers)) if field_mapper is not None else headers
    )
    answer = _json_dumps(
        {
            "headers": mapped_headers,
            "rows": content.get("data", []),
        }
    )
    return FormattedToolResult(answer=answer)


def _format_task_result(content: Mapping[str, Any]) -> FormattedToolResult:
    """Format one async task submission response."""
    task_id = _string_or_none(content.get("task_id"))
    output_dir = _string_or_none(content.get("output_dir"))
    compute_resource = _normalize_compute_resource(
        _string_or_none(content.get("compute_resource"))
    )
    return FormattedToolResult(
        answer=f"Task created successfully:{task_id}",
        metadata={
            "task_id": task_id,
            "output_dir": output_dir,
            "compute_resource": compute_resource,
            "status": "RUNNING",
            "log_status": "sync_running",
        },
    )


def _format_deep_genome_result(
    content: Mapping[str, Any],
    arguments: Mapping[str, Any] | None,
) -> FormattedToolResult:
    """Format a DeepGenome task submission response."""
    server_id = _string_or_none(content.get("task_id"))
    arguments = arguments or {}
    return FormattedToolResult(
        answer=f"Server task created successfully:{server_id}",
        metadata={
            "server_id": server_id,
            "species_code": _string_or_none(arguments.get("species_code")),
            "gene_id": _string_or_none(arguments.get("gene_id")),
            "status": "RUNNING",
        },
    )


def _format_design_result(content: Mapping[str, Any]) -> FormattedToolResult:
    """Format task output from DigitalDesignAgent."""
    tasks = [
        task
        for task in (
            content.get("protein_design_task"),
            content.get("promoter_design_task"),
            content.get("terminator_design_task"),
        )
        if isinstance(task, Mapping)
    ]
    if not tasks:
        return FormattedToolResult(
            answer="No tasks found",
            metadata={
                "status": "FAILED",
                "log_status": "sync_failed",
            },
        )

    primary_task = tasks[0]
    task_ids = [
        str(task.get("task_id", ""))
        for task in tasks
        if task.get("task_id") is not None
    ]
    output_dirs = [
        str(task.get("output_dir"))
        for task in tasks
        if task.get("output_dir") is not None
    ]
    return FormattedToolResult(
        answer=f"Tasks created successfully: {','.join(task_ids)}",
        metadata={
            "task_id": _string_or_none(primary_task.get("task_id")),
            "output_dir": _json_dumps(output_dirs) if output_dirs else None,
            "compute_resource": _string_or_none(
                primary_task.get("compute_resource")
            ),
            "status": "RUNNING",
            "log_status": "sync_running",
        },
    )


def _network_task_payload(content: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the nested GeneNetwork task payload when present."""
    network_task = content.get("network_task")
    return network_task if isinstance(network_task, Mapping) else content


def _first_message(content: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the first OpenAI-style message from a response payload."""
    choices = content.get("choices")
    if not isinstance(choices, Sequence) or isinstance(choices, str):
        return {}
    if not choices:
        return {}
    first_choice = choices[0]
    if not isinstance(first_choice, Mapping):
        return {}
    message = first_choice.get("message")
    return message if isinstance(message, Mapping) else {}


def _follow_up_questions(message: Mapping[str, Any]) -> tuple[str, ...]:
    """Return normalized follow-up questions from a message payload."""
    questions = message.get("follow_up_questions")
    if not isinstance(questions, Sequence) or isinstance(questions, str):
        return ()
    return tuple(str(question) for question in questions)


def _doc_list(message: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """Return document dictionaries from a message payload."""
    return tuple(_mapping_sequence(message.get("doc_list")))


def _mapping_sequence(value: Any) -> tuple[Mapping[str, Any], ...]:
    """Return a sequence containing only mapping items."""
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _normalize_citations(
    answer: str,
    doc_list: Sequence[Mapping[str, Any]],
    reference_resolver: ReferenceResolver | None,
) -> tuple[str, tuple[Mapping[str, Any], ...]]:
    """Deduplicate cited documents and rewrite citation indices."""
    citation_order = _citation_order(answer)
    selected_docs: list[Mapping[str, Any]] = []
    old_to_new: dict[int, int] = {}
    seen_keys: set[str] = set()

    for old_index in citation_order:
        if old_index < 1 or old_index > len(doc_list):
            continue
        doc = doc_list[old_index - 1]
        doc_key = _document_key(doc, old_index)
        if doc_key in seen_keys:
            continue
        seen_keys.add(doc_key)
        selected_docs.append(_reference_payload(doc, reference_resolver))
        old_to_new[old_index] = len(selected_docs)

    def replace_citation(match: re.Match[str]) -> str:
        new_numbers = [
            str(old_to_new[old_index])
            for old_index in _numbers_from_match(match)
            if old_index in old_to_new
        ]
        return f"[{','.join(new_numbers)}]" if new_numbers else ""

    return (
        _CITATION_PATTERN.sub(replace_citation, answer),
        tuple(selected_docs),
    )


def _citation_order(answer: str) -> tuple[int, ...]:
    """Return cited document indices in first-appearance order."""
    seen_indices: set[int] = set()
    ordered_indices: list[int] = []
    for match in _CITATION_PATTERN.finditer(answer):
        for number in _numbers_from_match(match):
            if number not in seen_indices:
                seen_indices.add(number)
                ordered_indices.append(number)
    return tuple(ordered_indices)


def _numbers_from_match(match: re.Match[str]) -> tuple[int, ...]:
    """Return numeric citation values from a regex match."""
    numbers: list[int] = []
    for raw_number in match.group(1).split(","):
        try:
            numbers.append(int(raw_number.strip()))
        except ValueError:
            continue
    return tuple(numbers)


def _document_key(doc: Mapping[str, Any], index: int) -> str:
    """Return a stable deduplication key for a cited document."""
    return str(doc.get("file_id") or doc.get("title") or index)


def _reference_payload(
    doc: Mapping[str, Any],
    reference_resolver: ReferenceResolver | None,
) -> Mapping[str, Any]:
    """Return the reference metadata exposed to clients."""
    file_id = doc.get("file_id")
    if file_id is not None and reference_resolver is not None:
        resolved = reference_resolver(str(file_id))
        if resolved is not None:
            return dict(resolved)

    title = str(doc.get("title", ""))
    if title.endswith(".pdf"):
        title = title[:-4]
    return {"file_id": file_id, "title": title}


def _normalize_compute_resource(value: str | None) -> str | None:
    """Return the persisted compute resource name."""
    resource_names = {
        "small": "analyst-agents-small",
        "medium": "analyst-agents-medium",
        "large": "analyst-agents-large",
    }
    if value is None:
        return None
    return resource_names.get(value, value)


def _string_or_none(value: Any) -> str | None:
    """Return a string value or None."""
    return None if value is None else str(value)


def _json_dumps(value: Any) -> str:
    """Serialize a value using the repository JSON conventions."""
    return json.dumps(value, ensure_ascii=False)
