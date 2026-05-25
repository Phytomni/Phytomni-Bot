# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for server-side MCP result formatting.

Covers citation rewriting, document deduplication, follow-up
extraction, DataAgent tabular field shape, envelope construction, and
credential-pattern sanitization at the MCP boundary.
"""

import pytest

from mcp_server_phytomni.mcp.result_formatting import (
    build_tool_result_envelope,
    format_tool_result,
)

pytestmark = pytest.mark.server


def test_knowledge_result_rewrites_citations_and_deduplicates_docs() -> None:
    """Verify cited documents are deduplicated in first-citation order.

    The answer text stays as plain markdown with inline ``[N]`` citation
    markers; deduplicated documents move to the structured ``references``
    field rather than being wrapped into a JSON envelope string.
    """
    payload = {
        "choices": [
            {
                "message": {
                    "content": "Evidence appears in [2] and [1, 2].",
                    "follow_up_questions": ["Next question?"],
                    "doc_list": [
                        {"file_id": "doc-a", "title": "Paper A.pdf"},
                        {"file_id": "doc-b", "title": "Paper B.pdf"},
                    ],
                }
            }
        ]
    }

    result = format_tool_result("KnowledgeAgent", payload)

    assert result.answer == "Evidence appears in [1] and [2,1]."
    assert result.references == (
        {"file_id": "doc-b", "title": "Paper B"},
        {"file_id": "doc-a", "title": "Paper A"},
    )
    assert result.follow_up_questions == ("Next question?",)


def test_data_result_returns_tabular_field_and_summary_answer() -> None:
    """Verify DataAgent surfaces headers and rows as a structured field.

    The ``tabular`` field carries the typed payload so HTTP clients no
    longer need to ``json.loads`` the answer string; ``answer`` is a
    human-readable shape summary that singular vs plural correctly.
    """
    payload = {
        "header": [{"caption": "gene_id"}, {"caption": "score"}],
        "data": [["Os01g01010", 0.8]],
    }

    result = format_tool_result("DataAgent", payload)

    assert result.tabular == {
        "headers": ["gene_id", "score"],
        "rows": [["Os01g01010", 0.8]],
    }
    assert result.answer == "1 row x 2 columns"


def test_get_task_status_terminal_success_emits_artifacts_descriptor() -> None:
    """``GetTaskStatus`` surfaces an artifacts entry on success terminal.

    The MCP tool's raw payload is the per-task row ``reconcile_task``
    returns; the formatter mirrors the run-aggregate envelope by
    advertising one artifact descriptor when the task is in a success
    terminal state with a non-empty output_dir. Non-terminal and
    failed branches expose an empty artifacts list.
    """
    raw = {
        "task_id": "T-1",
        "status": "succeeded",
        "output_dir": "/obs/run/output",
        "analysis_id": "an-1",
        "live_status": {"phase": "completed"},
    }

    result = format_tool_result("GetTaskStatus", raw)

    assert result.answer == "Task T-1: succeeded"
    assert result.metadata["task_id"] == "T-1"
    assert result.metadata["status"] == "succeeded"
    assert result.metadata["output_dir"] == "/obs/run/output"
    assert result.metadata["analysis_id"] == "an-1"
    assert result.metadata["live_status"] == {"phase": "completed"}
    assert result.metadata["artifacts"] == [
        {"task_id": "T-1", "output_dir": "/obs/run/output", "paths": []},
    ]


def test_get_task_status_running_emits_empty_artifacts() -> None:
    """Non-terminal status keeps artifacts empty (no products to advertise)."""
    raw = {
        "task_id": "T-2",
        "status": "running",
        "output_dir": "/obs/run/wip",
        "analysis_id": "an-2",
        "live_status": None,
    }

    result = format_tool_result("GetTaskStatus", raw)

    assert result.answer == "Task T-2: running"
    assert not result.metadata["artifacts"]


def test_get_task_status_unknown_id_returns_unknown_status() -> None:
    """An unrecorded task id surfaces status=unknown with no artifacts."""
    raw = {
        "task_id": "T-nope",
        "status": "unknown",
        "output_dir": "",
        "analysis_id": "",
        "live_status": None,
    }

    result = format_tool_result("GetTaskStatus", raw)

    assert result.answer == "Task T-nope: unknown"
    assert result.metadata["status"] == "unknown"
    assert not result.metadata["artifacts"]


def test_envelope_preserves_raw_provider_fields() -> None:
    """Verify envelope keeps reasoning_content, usage, and unknown keys.

    The display answer still flows through the formatter, but the
    sanitized raw block must keep provider-returned reasoning,
    tool_calls, usage, system_fingerprint, and any forward-compatible
    extensions intact so callers can opt into them.
    """
    payload = {
        "choices": [
            {
                "message": {
                    "content": "final answer",
                    "reasoning_content": "internal thinking trace",
                    "tool_calls": [{"id": "t1", "type": "function"}],
                    "refusal": None,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
        },
        "system_fingerprint": "fp_abc",
        "unknown_provider_field": {"version": 2},
    }

    envelope = build_tool_result_envelope("ChatAgent", payload)

    assert envelope.formatted.answer == "final answer"
    raw_message = envelope.raw["choices"][0]["message"]
    assert raw_message["reasoning_content"] == "internal thinking trace"
    assert raw_message["tool_calls"] == [{"id": "t1", "type": "function"}]
    assert raw_message["refusal"] is None
    assert envelope.raw["choices"][0]["finish_reason"] == "stop"
    assert envelope.raw["usage"]["prompt_tokens"] == 10
    assert envelope.raw["usage"]["total_tokens"] == 30
    assert envelope.raw["system_fingerprint"] == "fp_abc"
    assert envelope.raw["unknown_provider_field"] == {"version": 2}


def test_envelope_repairs_reasoning_content_answer_tail() -> None:
    """Envelope raw and formatted views agree after reasoning repair."""
    payload = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "reasoning_content": (
                        "<think>identify chlorophyll</think>"
                        "Leaves capture light."
                    ),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"total_tokens": 12},
    }

    envelope = build_tool_result_envelope("ChatAgent", payload)

    assert envelope.formatted.answer == "Leaves capture light."
    raw_message = envelope.raw["choices"][0]["message"]
    assert raw_message["content"] == "Leaves capture light."
    assert raw_message["reasoning_content"] == "identify chlorophyll"
    assert envelope.raw["usage"]["total_tokens"] == 12


def test_envelope_sanitizes_credential_pattern_keys() -> None:
    """Verify _sanitize_raw recursively drops secret-pattern keys.

    Mapping keys whose lowercased name contains any pattern in
    ``_SECRET_KEY_PATTERNS`` are removed at every nesting level,
    inside lists, and across the whole payload graph.
    """
    payload = {
        "choices": [{"message": {"content": "ok"}}],
        "api_key": "sk-secret",
        "Authorization": "Bearer xxx",
        "session_id": "s-123",
        "nested": {
            "password": "p",
            "bearer_token": "bt",
            "kept": 1,
        },
        "items": [
            {"secret": "x", "name": "ok"},
            "plain_string",
        ],
    }

    envelope = build_tool_result_envelope("ChatAgent", payload)

    assert "api_key" not in envelope.raw
    assert "Authorization" not in envelope.raw
    assert "session_id" not in envelope.raw
    assert "password" not in envelope.raw["nested"]
    assert "bearer_token" not in envelope.raw["nested"]
    assert envelope.raw["nested"]["kept"] == 1
    assert "secret" not in envelope.raw["items"][0]
    assert envelope.raw["items"][0]["name"] == "ok"
    assert envelope.raw["items"][1] == "plain_string"


def test_sanitize_raw_preserves_token_count_overrides() -> None:
    """Verify the override allow-list keeps OpenAI usage token counts.

    The ``token`` substring would otherwise misclassify ``prompt_tokens``,
    ``completion_tokens``, ``total_tokens``, ``max_tokens``, and
    ``tokens`` as credentials; the explicit allow-list keeps them so
    clients can render usage tables from the raw envelope block.
    """
    payload = {
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
            "max_tokens": 4096,
            "max_completion_tokens": 1024,
            "tokens": 5,
            "bearer_token": "leak-me",
        },
    }

    envelope = build_tool_result_envelope("ChatAgent", payload)

    assert envelope.raw["usage"]["prompt_tokens"] == 10
    assert envelope.raw["usage"]["completion_tokens"] == 20
    assert envelope.raw["usage"]["total_tokens"] == 30
    assert envelope.raw["usage"]["max_tokens"] == 4096
    assert envelope.raw["usage"]["max_completion_tokens"] == 1024
    assert envelope.raw["usage"]["tokens"] == 5
    assert "bearer_token" not in envelope.raw["usage"]


def test_envelope_returns_fresh_structure_for_safe_mutation() -> None:
    """Verify the raw block is a fresh structure independent of input."""
    payload = {"choices": [{"message": {"content": "x"}}], "items": [1, 2]}

    envelope = build_tool_result_envelope("ChatAgent", payload)

    envelope.raw["items"].append(3)
    assert payload["items"] == [1, 2]


def test_data_result_lifts_rewrite_metadata_from_phytomni_state() -> None:
    """DataAgent metadata surfaces NL2SQL rewrite context.

    The handler payload carries the NL2SQL final response at the top
    level and the LangGraph intermediate fields under
    ``phytomni_state`` (cited mode of ``merge_intermediate_state``).
    The formatter lifts ``user_query`` / ``rewrite_query`` /
    ``is_rewrite`` into ``formatted.metadata`` so default-mode HTTP /
    MCP clients can read the actually-executed query without
    requesting ``debug=true`` to inspect ``raw.phytomni_state``.
    """
    payload = {
        "header": [{"caption": "gene_id"}],
        "data": [["Os01g01010"]],
        "phytomni_state": {
            "user_query": "Show me rice genes on chromosome 1.",
            "rewrite_query": "SELECT gene_id FROM rice WHERE chr = 1;",
            "is_rewrite": True,
            "retrieve_prompt": "irrelevant intermediate scratch",
        },
    }

    result = format_tool_result("DataAgent", payload)

    assert result.tabular == {
        "headers": ["gene_id"],
        "rows": [["Os01g01010"]],
    }
    assert result.metadata == {
        "user_query": "Show me rice genes on chromosome 1.",
        "rewrite_query": "SELECT gene_id FROM rice WHERE chr = 1;",
        "is_rewrite": True,
    }


def test_data_result_handles_missing_phytomni_state() -> None:
    """Absent intermediate state keeps the contract keys with None values.

    A stable key set lets the cross-cutting metadata contract test
    assert presence regardless of whether the agent populated the
    LangGraph fields.
    """
    payload = {
        "header": [{"caption": "gene_id"}],
        "data": [["Os01g01010"]],
    }

    result = format_tool_result("DataAgent", payload)

    assert result.metadata == {
        "user_query": None,
        "rewrite_query": None,
        "is_rewrite": None,
    }


def test_analyst_result_lifts_plan_metadata_from_phytomni_state() -> None:
    """AnalystAgent submit metadata surfaces plan / tools / context keys.

    The wrapper return places ``task_id`` / ``output_dir`` /
    ``job_name`` / ``compute_resource`` at the top level (task-style
    ``merge_intermediate_state`` surface) and pushes the rest of the
    LangGraph state under ``phytomni_state``. The formatter lifts the
    curated subset ``plan`` / ``extracted_tools`` /
    ``method_context_keys`` / ``plan_retries`` so default-mode
    clients can read what the analyst actually planned without
    flipping ``debug=true``.
    """
    payload = {
        "task_id": "task-1",
        "output_dir": "/obs/phytomni/run/out",
        "compute_resource": "medium",
        "phytomni_state": {
            "plan": "1. retrieve data\n2. analyze\n3. report",
            "plan_feedback": None,
            "plan_retries": 1,
            "extracted_tools": ["pyfasta", "pandas"],
            "tool_usages": "pyfasta -i ...",
            "method_context": {
                "upload": {"path": "sop.pdf"},
                "literature": {"hits": []},
            },
        },
    }

    result = format_tool_result("AnalystAgent", payload)

    assert result.answer == "Task created successfully:task-1"
    assert result.metadata["task_id"] == "task-1"
    assert result.metadata["output_dir"] == "/obs/phytomni/run/out"
    assert result.metadata["compute_resource"] == "analyst-agents-medium"
    assert result.metadata["status"] == "RUNNING"
    assert result.metadata["log_status"] == "sync_running"
    assert result.metadata["plan"] == "1. retrieve data\n2. analyze\n3. report"
    assert result.metadata["plan_retries"] == 1
    assert result.metadata["extracted_tools"] == ("pyfasta", "pandas")
    assert result.metadata["method_context_keys"] == ("upload", "literature")


def test_analyst_result_truncates_long_plan_with_marker() -> None:
    """Plan text exceeding the cap is truncated with a pointer to raw.

    Free-form plan markdown can run to many KB; the formatter caps
    at ``_METADATA_TEXT_TRUNCATE_BYTES`` and appends a marker that
    directs clients to ``raw.phytomni_state.plan`` (visible in debug
    mode) for the full text.
    """
    long_plan = "x" * 5000
    payload = {
        "task_id": "task-2",
        "output_dir": "/obs/out",
        "compute_resource": "small",
        "phytomni_state": {
            "plan": long_plan,
            "plan_retries": 0,
            "extracted_tools": [],
            "method_context": {},
        },
    }

    result = format_tool_result("AnalystAgent", payload)

    plan_field = result.metadata["plan"]
    assert plan_field is not None
    assert plan_field.endswith("…[truncated, see raw.phytomni_state.plan]")
    assert len(plan_field.encode("utf-8")) <= 4096 + 64


def test_analyst_result_handles_missing_phytomni_state() -> None:
    """Absent intermediate state preserves the new metadata key set as None."""
    payload = {
        "task_id": "task-3",
        "output_dir": "/obs/out",
        "compute_resource": "small",
    }

    result = format_tool_result("AnalystAgent", payload)

    assert result.metadata["plan"] is None
    assert result.metadata["plan_retries"] is None
    assert result.metadata["extracted_tools"] == ()
    assert result.metadata["method_context_keys"] == ()
