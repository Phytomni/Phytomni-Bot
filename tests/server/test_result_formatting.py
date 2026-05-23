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
