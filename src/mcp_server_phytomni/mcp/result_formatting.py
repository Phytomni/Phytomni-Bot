# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Format Phytomni MCP tool responses at the server boundary.

Exposes ``FormattedToolResult``, ``ToolResultEnvelope``,
``format_tool_result``, ``build_tool_result_envelope``, and
response-projection helpers ``resolve_debug`` / ``strip_agent_result``
(``PHYTOMNI_DEBUG=1`` forces full payloads). Private helpers normalize
citations and ``_sanitize_raw`` strips credential-pattern keys.
"""

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..common.reasoning_content import normalize_chat_completion_dict
from ..runtime.terminal_artifacts import collect_terminal_artifacts

_CITATION_PATTERN = re.compile(r"\[(?:[A-Za-z]+[: ]?)?(\d+(?:,\s*\d+)*)\]")

_PHYTOMNI_STATE_KEY = "phytomni_state"
_METADATA_TEXT_TRUNCATE_BYTES = 4096


@dataclass(frozen=True)
class FormattedToolResult:
    """Normalized client-facing representation of one tool response.

    Attributes:
        answer: Client-facing answer text or a human-readable summary
            for tabular responses.
        follow_up_questions: Suggested follow-up questions.
        metadata: Additional structured metadata for task-style
            responses.
        references: Normalized cited references for retrieval-style
            tools.
        tabular: Optional tabular payload with ``headers`` / ``rows``
            keys for DataAgent-style responses; ``None`` when the tool
            does not produce a table.
        output_dirs: Output directories for fan-out task agents (e.g.
            DigitalDesign protein / promoter / terminator). Empty
            tuple for single-task agents that surface one path via
            ``metadata["output_dir"]``.
    """

    answer: str
    follow_up_questions: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    references: tuple[Mapping[str, Any], ...] = ()
    tabular: Mapping[str, Any] | None = None
    output_dirs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolResultEnvelope:
    """Full tool response carrying display and raw payloads.

    The ``raw`` field receives the handler payload after
    ``_sanitize_raw`` recursively strips credential-pattern keys, so
    HTTP and MCP clients can inspect provider-returned fields
    (reasoning_content, usage, finish_reason, tool_calls, unknown
    extensions) without leaking secrets.

    Attributes:
        formatted: Normalized display-oriented result.
        raw: Sanitized handler payload returned by the agent path.
    """

    formatted: FormattedToolResult
    raw: Any


_SECRET_KEY_PATTERNS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "secret",
        "token",
        "bearer",
        "authorization",
        "session_id",
        "password",
        "credential",
    }
)
_NON_SECRET_OVERRIDES: frozenset[str] = frozenset(
    {
        "tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "max_tokens",
        "max_completion_tokens",
        "n_tokens",
    }
)


def _is_sensitive_key(key: Any) -> bool:
    """Return True when a mapping key looks like it carries a secret.

    Lowercased key names containing any pattern in
    ``_SECRET_KEY_PATTERNS`` are sensitive, except for explicit
    overrides in ``_NON_SECRET_OVERRIDES`` (e.g. tokenizer counts
    ``prompt_tokens`` / ``completion_tokens`` that share the word
    "token" with the credential pattern but are plain metrics).
    """
    if not isinstance(key, str) or not key:
        return False
    lowered = key.lower()
    if lowered in _NON_SECRET_OVERRIDES:
        return False
    return any(pattern in lowered for pattern in _SECRET_KEY_PATTERNS)


def _sanitize_raw(payload: Any) -> Any:
    """Return ``payload`` with secret-pattern keys recursively removed.

    Walks mappings and list / tuple sequences. Drops mapping entries
    whose key satisfies ``_is_sensitive_key``. Lists return as lists,
    tuples as tuples; scalars (including strings and bytes) pass
    through unchanged. The result is a fresh structure so callers can
    mutate it without affecting the original payload.
    """
    if isinstance(payload, Mapping):
        return {
            key: _sanitize_raw(value)
            for key, value in payload.items()
            if not _is_sensitive_key(key)
        }
    if isinstance(payload, list):
        return [_sanitize_raw(item) for item in payload]
    if isinstance(payload, tuple):
        return tuple(_sanitize_raw(item) for item in payload)
    return payload


def build_tool_result_envelope(
    tool_name: str,
    payload: Any,
    *,
    arguments: Mapping[str, Any] | None = None,
) -> ToolResultEnvelope:
    """Build a full result envelope for one tool response.

    The raw payload is recursively sanitized through ``_sanitize_raw``
    before being placed on the envelope, so credential-pattern keys
    never reach client-facing surfaces.

    Args:
        tool_name: Public MCP tool name or legacy alias.
        payload: Raw decoded handler payload to format and preserve.
        arguments: Optional original tool arguments used by some
            formatters.

    Returns:
        Envelope containing formatted and sanitized raw payload blocks.
    """
    payload = normalize_chat_completion_dict(payload)
    return ToolResultEnvelope(
        formatted=format_tool_result(tool_name, payload, arguments=arguments),
        raw=_sanitize_raw(payload),
    )


def format_tool_result(
    tool_name: str,
    payload: Any,
    *,
    arguments: Mapping[str, Any] | None = None,
) -> FormattedToolResult:
    """Format one MCP tool payload using the registered tool name.

    Args:
        tool_name: Public MCP tool name or legacy alias.
        payload: Raw decoded MCP payload to normalize.
        arguments: Optional original tool arguments used by some
            formatters.

    Returns:
        Normalized client-facing result for the selected tool.
    """
    normalized_name = _normalize_tool_name(tool_name)
    content = _payload_mapping(payload)
    if normalized_name == "ChatAgent":
        result = _format_message_result(content)
    elif normalized_name in {
        "KnowledgeAgent",
        "ReviewAgent",
        "BriefGeneAgent",
    }:
        result = _format_cited_message_result(content)
    elif normalized_name == "DataAgent":
        result = _format_data_result(content)
    elif normalized_name == "AnalystAgent":
        result = _format_analyst_task_result(content)
    elif normalized_name == "DeepGenomeAgent":
        result = _format_deep_genome_result(content, arguments)
    elif normalized_name == "GeneNetworkAgent":
        result = _format_network_task_result(content)
    elif normalized_name == "InSilicoResearchAgent":
        result = _format_in_silico_result(content)
    elif normalized_name == "DigitalDesignAgent":
        result = _format_design_result(content)
    elif normalized_name == "GetTaskStatus":
        result = _format_task_status_result(content)
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
    """Return a mapping payload or an empty mapping for non-objects."""
    return payload if isinstance(payload, Mapping) else {}


def _format_message_result(
    content: Mapping[str, Any],
) -> FormattedToolResult:
    """Format an OpenAI-style response with one assistant message."""
    message = _first_message(content)
    return FormattedToolResult(
        answer=str(message.get("content", "")),
        follow_up_questions=_follow_up_questions(message),
    )


def _format_cited_message_result(
    content: Mapping[str, Any],
) -> FormattedToolResult:
    """Format an OpenAI-style response and normalize cited documents.

    The answer text stays as plain markdown with inline ``[N]`` citation
    markers; deduplicated citation documents flow through the structured
    ``references`` field. The OpenAI HTTP surface and the MCP stdio
    surface both consume those two fields directly, so neither needs to
    ``json.loads`` a wrapped envelope out of ``message.content``.
    """
    message = _first_message(content)
    answer = str(message.get("content", ""))
    doc_list = _doc_list(message)
    if not doc_list:
        return FormattedToolResult(
            answer=answer,
            follow_up_questions=_follow_up_questions(message),
        )

    normalized_answer, references = _normalize_citations(answer, doc_list)
    return FormattedToolResult(
        answer=normalized_answer,
        follow_up_questions=_follow_up_questions(message),
        references=references,
    )


def _format_data_result(
    content: Mapping[str, Any],
) -> FormattedToolResult:
    """Format natural-language SQL table output.

    Tabular payload moves into the structured ``tabular`` field so HTTP
    clients no longer need to ``json.loads(answer)`` to read headers
    and rows; ``answer`` carries a human-readable shape summary. The
    ``user_query`` / ``rewrite_query`` / ``is_rewrite`` keys lifted
    from ``phytomni_state`` let default-mode clients read the actual
    NL2SQL rewrite without inspecting ``raw.phytomni_state``.
    """
    headers = [
        str(column.get("caption") or column.get("name") or "")
        for column in _mapping_sequence(content.get("header"))
    ]
    raw_rows = content.get("data", [])
    rows = list(raw_rows) if isinstance(raw_rows, Sequence) else []
    row_count = len(rows)
    column_count = len(headers)
    row_label = "row" if row_count == 1 else "rows"
    column_label = "column" if column_count == 1 else "columns"
    state = _phytomni_state(content)
    return FormattedToolResult(
        answer=(f"{row_count} {row_label} x {column_count} {column_label}"),
        metadata={
            "user_query": state.get("user_query"),
            "rewrite_query": state.get("rewrite_query"),
            "is_rewrite": state.get("is_rewrite"),
        },
        tabular={"headers": headers, "rows": rows},
    )


def _phytomni_state(content: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the ``phytomni_state`` mapping or an empty mapping."""
    state = content.get(_PHYTOMNI_STATE_KEY)
    return state if isinstance(state, Mapping) else {}


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


def _format_analyst_task_result(
    content: Mapping[str, Any],
) -> FormattedToolResult:
    """Format an AnalystAgent submit response with planning metadata.

    Wraps the generic ``_format_task_result`` and enriches the
    resulting metadata with the curated planning subset lifted from
    ``phytomni_state`` (``plan``, ``plan_retries``,
    ``extracted_tools``, ``method_context_keys``). ``plan`` text is
    capped at ``_METADATA_TEXT_TRUNCATE_BYTES`` with a marker pointing
    to ``raw.phytomni_state.plan`` for the full document. Missing
    intermediate state keeps the keys present with ``None`` or empty
    tuples so the contract is stable.
    """
    base = _format_task_result(content)
    state = _phytomni_state(content)
    plan_text = state.get("plan")
    truncated_plan = (
        _truncate_text(
            str(plan_text),
            _METADATA_TEXT_TRUNCATE_BYTES,
            "raw.phytomni_state.plan",
        )
        if isinstance(plan_text, str)
        else None
    )
    extracted_tools = state.get("extracted_tools")
    method_context = state.get("method_context")
    enriched_metadata = {
        **base.metadata,
        "plan": truncated_plan,
        "plan_retries": state.get("plan_retries"),
        "extracted_tools": (
            tuple(str(tool) for tool in extracted_tools)
            if isinstance(extracted_tools, Sequence)
            and not isinstance(extracted_tools, str)
            else ()
        ),
        "method_context_keys": (
            tuple(str(key) for key in method_context.keys())
            if isinstance(method_context, Mapping)
            else ()
        ),
    }
    return FormattedToolResult(
        answer=base.answer,
        follow_up_questions=base.follow_up_questions,
        metadata=enriched_metadata,
        references=base.references,
        tabular=base.tabular,
        output_dirs=base.output_dirs,
    )


def _truncate_text(text: str, byte_limit: int, raw_pointer: str) -> str:
    """Return ``text`` truncated to ``byte_limit`` UTF-8 bytes.

    When the encoded length exceeds the cap, the trimmed text is
    suffixed with a marker pointing the caller at the raw state
    location for the full document. Returns the input unchanged when
    it already fits.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= byte_limit:
        return text
    marker = f"…[truncated, see {raw_pointer}]"
    trimmed = encoded[:byte_limit].decode("utf-8", errors="ignore")
    return f"{trimmed}{marker}"


def _format_network_task_result(
    content: Mapping[str, Any],
) -> FormattedToolResult:
    """Format a GeneNetworkAgent submit response with goal metadata.

    Wraps the generic ``_format_task_result`` and enriches the
    resulting metadata with ``goal_description`` lifted from
    ``phytomni_state``. The goal text is capped at 256 bytes with a
    marker pointing to ``raw.phytomni_state.goal_description`` for the
    full document. Missing intermediate state keeps the key present
    with ``None`` so the contract is stable.
    """
    base = _format_task_result(_network_task_payload(content))
    state = _phytomni_state(content)
    goal_text = state.get("goal_description")
    truncated_goal = (
        _truncate_text(
            str(goal_text),
            256,
            "raw.phytomni_state.goal_description",
        )
        if isinstance(goal_text, str)
        else None
    )
    enriched_metadata = {
        **base.metadata,
        "goal_description": truncated_goal,
    }
    return FormattedToolResult(
        answer=base.answer,
        follow_up_questions=base.follow_up_questions,
        metadata=enriched_metadata,
        references=base.references,
        tabular=base.tabular,
        output_dirs=base.output_dirs,
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


def _format_in_silico_result(
    content: Mapping[str, Any],
) -> FormattedToolResult:
    """Format an InSilicoResearchAgent submit response.

    The wrapper return places ``task_ids`` (goal-name → task-id dict),
    ``goals`` (list of goal dicts with ``goal`` / ``context`` keys),
    ``output_dir``, and ``error`` at the surface level. The formatter
    flattens ``task_ids`` to an ordered tuple, extracts goal
    descriptions, and exposes the shared ``output_dir`` so default-
    mode clients can read what sub-tasks were spawned without
    flipping ``debug=true``. The first task id mirrors the
    ``metadata.task_id`` slot for single-task consumers.
    """
    task_ids_mapping = content.get("task_ids")
    task_ids = (
        tuple(
            str(value)
            for value in task_ids_mapping.values()
            if isinstance(value, str) and value
        )
        if isinstance(task_ids_mapping, Mapping)
        else ()
    )
    goals = tuple(
        str(item.get("goal", ""))
        for item in _mapping_sequence(content.get("goals"))
    )
    output_dir = _string_or_none(content.get("output_dir"))
    error = _string_or_none(content.get("error"))
    primary_task_id = task_ids[0] if task_ids else None
    return FormattedToolResult(
        answer=f"Tasks created successfully: {','.join(task_ids)}",
        metadata={
            "task_id": primary_task_id,
            "task_ids": task_ids,
            "output_dir": output_dir,
            "goals": goals,
            "error": error,
            "status": "RUNNING",
            "log_status": "sync_running",
        },
    )


def _format_design_result(content: Mapping[str, Any]) -> FormattedToolResult:
    """Format task output from DigitalDesignAgent.

    The agent returns ``design_task_result`` as a list of AnalystAgent
    submission dicts (one per design kind: protein / promoter /
    terminator), accumulated via LangGraph's ``operator.add`` reducer.
    The formatter extracts task ids, output dirs, and compute
    resources from the list items rather than reading per-kind
    top-level keys.
    """
    design_results = content.get("design_task_result")
    results_list = (
        design_results if isinstance(design_results, list) else []
    )
    tasks = [
        task
        for task in results_list
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
    task_ids = tuple(
        str(task.get("task_id", ""))
        for task in tasks
        if task.get("task_id") is not None
    )
    output_dirs = tuple(
        str(task.get("output_dir"))
        for task in tasks
        if task.get("output_dir") is not None
    )
    goal_description = _truncate_text(
        str(_phytomni_state(content).get("goal_description") or ""),
        256,
        "raw.phytomni_state.goal_description",
    )
    metadata: dict[str, Any] = {
        "task_id": _string_or_none(primary_task.get("task_id")),
        "output_dir": output_dirs[0] if output_dirs else None,
        "compute_resource": _string_or_none(
            primary_task.get("compute_resource")
        ),
        "status": "RUNNING",
        "log_status": "sync_running",
        "task_ids": task_ids,
        "goal_description": goal_description or None,
    }
    return FormattedToolResult(
        answer=f"Tasks created successfully: {','.join(task_ids)}",
        metadata=metadata,
        output_dirs=output_dirs,
    )


def _format_task_status_result(
    content: Mapping[str, Any],
) -> FormattedToolResult:
    """Format a ``GetTaskStatus`` lookup result with artifacts summary.

    Mirrors the run-aggregate envelope's ``artifacts`` block at the
    single-task level: when the task is in a success terminal state and
    carries an ``output_dir``, surface one descriptor; otherwise expose
    an empty list. ``collect_terminal_artifacts`` owns the eligibility
    rule so success vocabulary stays consistent with the run-poll path.
    """
    task_id = _string_or_none(content.get("task_id"))
    status = _string_or_none(content.get("status")) or "unknown"
    output_dir = _string_or_none(content.get("output_dir"))
    artifacts = collect_terminal_artifacts([dict(content)])
    return FormattedToolResult(
        answer=f"Task {task_id or '?'}: {status}",
        metadata={
            "task_id": task_id,
            "status": status,
            "output_dir": output_dir,
            "analysis_id": _string_or_none(content.get("analysis_id")),
            "live_status": content.get("live_status"),
            "artifacts": artifacts,
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
        selected_docs.append(_reference_payload(doc))
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


def _reference_payload(doc: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the reference metadata exposed to clients."""
    file_id = doc.get("file_id")
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


# --- Response projection (debug / default mode) ---

_DEBUG_ENV = "PHYTOMNI_DEBUG"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def resolve_debug(per_request: bool | None) -> bool:
    """Return True when debug mode is active.

    PHYTOMNI_DEBUG=1 env var is a global operator override that
    forces debug mode regardless of the per-request flag.

    Args:
        per_request: Per-request debug flag from HTTP API payload.
            MCP stdio passes None since there is no per-request flag.

    Returns:
        True if either the env var or the per-request flag is truthy.
    """
    if _env_debug_enabled():
        return True
    return bool(per_request)


def strip_agent_result(result: dict) -> dict:
    """Remove 'raw' from a {formatted, raw} result dict.

    Used by MCP dispatch_tool and agent runs endpoints to hide the
    sanitized handler payload in default (non-debug) mode.

    Returns a new dict; the original is not mutated.
    """
    return {k: v for k, v in result.items() if k != "raw"}


def _env_debug_enabled() -> bool:
    """Check whether PHYTOMNI_DEBUG env var is set to a truthy value."""
    raw = os.getenv(_DEBUG_ENV, "").strip().lower()
    return raw in _TRUTHY


_CHAT_COMPLETION_KEEP = frozenset(
    {
        "id",
        "object",
        "created",
        "model",
        "choices",
        "usage",
        "formatted",
    }
)
_MESSAGE_KEEP = frozenset(
    {
        "role",
        "content",
        "reasoning_content",
        "tool_calls",
        "finish_reason",
        "index",
    }
)
_USAGE_KEEP = frozenset(
    {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    }
)


def strip_chat_completion(completion: dict) -> dict:
    """Remove debug-only fields from a to_chat_completion() result.

    Replaces ``choices[].message.content`` with ``formatted.answer``
    (normalized ``[N]`` citation format consistent with references),
    strips ``answer`` from ``formatted`` (already in content),
    trims ``usage`` to three token fields, and drops provider
    extensions (``raw``, ``phytomni_state``, ``system_fingerprint``,
    ``service_tier``, ``prompt_logprobs``).

    Returns a new dict; the original is not mutated.
    """
    normalized_answer = _extract_formatted_answer(completion)
    result = {
        k: v for k, v in completion.items() if k in _CHAT_COMPLETION_KEEP
    }
    if "choices" in result:
        result["choices"] = [
            _strip_choice(c, normalized_answer) for c in result["choices"]
        ]
    if "usage" in result and isinstance(result["usage"], dict):
        result["usage"] = {
            k: v for k, v in result["usage"].items() if k in _USAGE_KEEP
        }
    if "formatted" in result and isinstance(result["formatted"], dict):
        result["formatted"] = {
            k: v for k, v in result["formatted"].items() if k != "answer"
        }
    return result


def _extract_formatted_answer(
    completion: dict,
) -> str | None:
    """Read formatted.answer for content normalization."""
    formatted = completion.get("formatted")
    if isinstance(formatted, dict):
        answer = formatted.get("answer")
        if isinstance(answer, str):
            return answer
    return None


def _strip_choice(
    choice: dict,
    normalized_answer: str | None,
) -> dict:
    """Keep only standard fields in one choice dict.

    When normalized_answer is provided, it replaces the message
    content so the consumer sees the [N]-style citations that
    match formatted.references.
    """
    stripped = {k: v for k, v in choice.items() if k != "message"}
    message = choice.get("message")
    if isinstance(message, dict):
        clean_message = {
            k: v for k, v in message.items() if k in _MESSAGE_KEEP
        }
        if normalized_answer is not None:
            clean_message["content"] = normalized_answer
        stripped["message"] = clean_message
    return stripped
