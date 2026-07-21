# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for response projection helpers.

Covers ``resolve_debug`` (env override + per-request flag),
``strip_agent_result`` (raw removal without mutation), and
``strip_chat_completion`` (OpenAI-shaped response projection).
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import make_dataclass

import pytest

from mcp_server_phytomni.mcp.formatting import models as formatting_models
from mcp_server_phytomni.mcp.formatting import (
    redaction as formatting_redaction,
)
from mcp_server_phytomni.mcp.result_formatting import (
    FormattedToolResult,
    ToolResultEnvelope,
    resolve_debug,
    strip_agent_result,
    strip_chat_completion,
)

pytestmark = pytest.mark.unit


def test_projection_facade_reexports_redaction_helpers() -> None:
    """Debug and default projection helpers remain leaf identities."""
    assert resolve_debug is formatting_redaction.resolve_debug
    assert strip_agent_result is formatting_redaction.strip_agent_result
    assert strip_chat_completion is formatting_redaction.strip_chat_completion


def test_formatting_facade_reexports_leaf_models() -> None:
    """Legacy model imports remain the exact leaf class objects."""
    assert FormattedToolResult is formatting_models.FormattedToolResult
    assert ToolResultEnvelope is formatting_models.ToolResultEnvelope


def test_leaf_redaction_handles_dataclasses_sequences_and_unknown_values() -> (
    None
):
    """Redaction drops credential fields without touching unknown objects."""
    fixture_type = make_dataclass("RedactionFixture", ["token", "value"])
    unknown = object()

    sanitized = formatting_redaction.sanitize_raw(
        {
            "fixture": fixture_type("secret", "kept"),
            "items": ("plain", {"api_key": "drop", "value": 2}),
            "unknown": unknown,
        }
    )

    assert sanitized["fixture"] == {"value": "kept"}
    assert sanitized["items"] == ("plain", {"value": 2})
    assert sanitized["unknown"] is unknown


# --- resolve_debug ---


def test_resolve_debug_returns_false_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_debug(None) returns False when env var is not set."""
    monkeypatch.delenv("PHYTOMNI_DEBUG", raising=False)
    assert resolve_debug(None) is False


def test_resolve_debug_returns_false_for_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_debug(False) returns False when env var is not set."""
    monkeypatch.delenv("PHYTOMNI_DEBUG", raising=False)
    assert resolve_debug(False) is False


def test_resolve_debug_per_request_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_debug(True) returns True even without env var."""
    monkeypatch.delenv("PHYTOMNI_DEBUG", raising=False)
    assert resolve_debug(True) is True


def test_resolve_debug_env_override_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PHYTOMNI_DEBUG=1 overrides per_request=False."""
    monkeypatch.setenv("PHYTOMNI_DEBUG", "1")
    assert resolve_debug(False) is True


def test_resolve_debug_env_override_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PHYTOMNI_DEBUG=true overrides per_request=None."""
    monkeypatch.setenv("PHYTOMNI_DEBUG", "true")
    assert resolve_debug(None) is True


def test_resolve_debug_env_override_yes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PHYTOMNI_DEBUG=yes is also truthy."""
    monkeypatch.setenv("PHYTOMNI_DEBUG", "yes")
    assert resolve_debug(None) is True


def test_resolve_debug_env_override_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PHYTOMNI_DEBUG=on is also truthy."""
    monkeypatch.setenv("PHYTOMNI_DEBUG", "on")
    assert resolve_debug(None) is True


def test_resolve_debug_env_zero_is_falsy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PHYTOMNI_DEBUG=0 does not enable debug mode."""
    monkeypatch.setenv("PHYTOMNI_DEBUG", "0")
    assert resolve_debug(None) is False


def test_resolve_debug_env_empty_is_falsy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PHYTOMNI_DEBUG= (empty) does not enable debug mode."""
    monkeypatch.setenv("PHYTOMNI_DEBUG", "")
    assert resolve_debug(None) is False


# --- strip_agent_result ---


def test_strip_agent_result_removes_raw() -> None:
    """strip_agent_result removes the 'raw' key."""
    result = {
        "formatted": {"answer": "hello", "references": []},
        "raw": {"some": "data"},
    }
    stripped = strip_agent_result(result)
    assert "raw" not in stripped
    assert "formatted" in stripped
    assert stripped["formatted"]["answer"] == "hello"


def test_strip_agent_result_keeps_formatted_only() -> None:
    """strip_agent_result keeps formatted when raw is absent."""
    result = {"formatted": {"answer": "x"}}
    stripped = strip_agent_result(result)
    assert stripped == {"formatted": {"answer": "x"}}


def test_strip_agent_result_does_not_mutate_input() -> None:
    """strip_agent_result returns a new dict without modifying input."""
    original = {
        "formatted": {"answer": "keep"},
        "raw": {"data": "remove"},
    }
    copy = {
        "formatted": {"answer": "keep"},
        "raw": {"data": "remove"},
    }
    strip_agent_result(original)
    assert original == copy


def test_strip_agent_result_preserves_extra_keys() -> None:
    """strip_agent_result only removes 'raw', keeps other keys."""
    result = {
        "formatted": {"answer": "a"},
        "raw": {"data": "b"},
        "extra": "kept",
    }
    stripped = strip_agent_result(result)
    assert stripped == {"formatted": {"answer": "a"}, "extra": "kept"}


# --- strip_chat_completion ---


def _build_full_completion() -> dict:
    """Build a realistic full completion matching to_chat_completion output."""
    return {
        "id": "chatcmpl-abc123",
        "object": "chat.completion",
        "created": 1779625485,
        "model": "phyto-brief-gene",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Gene report [document:1] text.",
                    "refusal": None,
                    "annotations": None,
                    "audio": None,
                    "function_call": None,
                    "tool_calls": None,
                    "reasoning_content": "Let me analyze...",
                    "doc_list": [
                        {
                            "file_id": "doc-1",
                            "title": "Paper 1",
                            "semantic_vector": [0.1] * 768,
                        },
                    ],
                    "total": 32,
                    "follow_up_questions": ["Next?"],
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 5000,
            "completion_tokens": 800,
            "total_tokens": 5800,
            "prompt_tokens_details": {"cached_tokens": 0},
        },
        "system_fingerprint": "fp_abc",
        "service_tier": "default",
        "prompt_logprobs": None,
        "phytomni_state": {"gene_id": "Os01g0177400"},
        "raw": {"choices": [{"message": {"content": "raw"}}]},
        "formatted": {
            "answer": "Gene report [1] text.",
            "references": [
                {"file_id": "doc-1", "title": "Paper 1"},
            ],
            "follow_up_questions": ["Next?"],
            "metadata": {},
            "tabular": None,
            "output_dirs": [],
        },
    }


def test_strip_chat_completion_removes_raw_and_provider_fields() -> None:
    """raw, phytomni_state, system_fingerprint, service_tier removed."""
    completion = _build_full_completion()
    stripped = strip_chat_completion(completion)
    assert "raw" not in stripped
    assert "phytomni_state" not in stripped
    assert "system_fingerprint" not in stripped
    assert "service_tier" not in stripped
    assert "prompt_logprobs" not in stripped


def test_strip_chat_completion_keeps_standard_top_level() -> None:
    """id, object, created, model, choices, usage, formatted kept."""
    completion = _build_full_completion()
    stripped = strip_chat_completion(completion)
    assert set(stripped.keys()) == {
        "id",
        "object",
        "created",
        "model",
        "choices",
        "usage",
        "formatted",
    }


def test_strip_chat_completion_cleans_message_keeps_reasoning() -> None:
    """Message keeps role, content, reasoning_content; drops doc_list."""
    completion = _build_full_completion()
    stripped = strip_chat_completion(completion)
    msg = stripped["choices"][0]["message"]
    assert "role" in msg
    assert "content" in msg
    assert "reasoning_content" in msg
    assert msg["reasoning_content"] == "Let me analyze..."
    assert "doc_list" not in msg
    assert "total" not in msg
    assert "follow_up_questions" not in msg
    assert "refusal" not in msg
    assert "annotations" not in msg
    assert "audio" not in msg
    assert "function_call" not in msg


def test_strip_chat_completion_replaces_content_with_normalized_answer() -> (
    None
):
    """choices[].message.content gets the normalized [N] answer."""
    completion = _build_full_completion()
    stripped = strip_chat_completion(completion)
    msg = stripped["choices"][0]["message"]
    assert msg["content"] == "Gene report [1] text."


def test_strip_chat_completion_removes_answer_from_formatted() -> None:
    """formatted.answer is removed (already in content)."""
    completion = _build_full_completion()
    stripped = strip_chat_completion(completion)
    assert "answer" not in stripped["formatted"]
    assert "references" in stripped["formatted"]
    assert "follow_up_questions" in stripped["formatted"]
    assert "metadata" in stripped["formatted"]


def test_strip_chat_completion_trims_usage() -> None:
    """usage keeps only 3 token fields."""
    completion = _build_full_completion()
    stripped = strip_chat_completion(completion)
    assert set(stripped["usage"].keys()) == {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    }


def test_strip_chat_completion_does_not_mutate_input() -> None:
    """Original dict is not modified by strip_chat_completion."""
    completion = _build_full_completion()
    original = deepcopy(completion)
    strip_chat_completion(completion)
    assert completion == original


def test_strip_chat_completion_no_formatted_keeps_content() -> None:
    """Without formatted.answer, original content is preserved."""
    completion = {
        "id": "chatcmpl-x",
        "object": "chat.completion",
        "created": 1,
        "model": "test",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "original content",
                    "reasoning_content": "thinking",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        },
    }
    stripped = strip_chat_completion(completion)
    assert stripped["choices"][0]["message"]["content"] == ("original content")


def test_strip_chat_completion_preserves_choice_index_and_finish() -> None:
    """choice-level index and finish_reason survive projection."""
    completion = _build_full_completion()
    stripped = strip_chat_completion(completion)
    choice = stripped["choices"][0]
    assert choice["index"] == 0
    assert choice["finish_reason"] == "stop"
