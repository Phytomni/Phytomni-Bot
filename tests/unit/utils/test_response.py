# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for OpenAI-style response helpers."""

import pytest

from mcp_server_phytomni.common.docs import (
    format_retrieved_doc_context,
    format_upload_context,
)
from mcp_server_phytomni.common.responses import (
    attach_message_payload,
    first_message,
    message_content,
    parse_follow_up_questions,
    parse_json_list_fragment,
    parse_json_object_fragment,
)

pytestmark = pytest.mark.unit


def test_message_content_reads_first_assistant_message():
    """Verify message content reads first assistant message."""
    response = {
        "choices": [
            {
                "message": {
                    "content": "answer",
                    "role": "assistant",
                }
            }
        ]
    }

    assert message_content(response) == "answer"


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"choices": []},
        {"choices": [{"message": "not-a-mapping"}]},
        {"choices": [{"message": {"content": ""}}]},
    ],
)
def test_message_content_returns_empty_string_for_invalid_shape(response):
    """Malformed OpenAI envelopes produce an empty content string."""
    assert message_content(response) == ""


def test_first_message_returns_only_a_mapping_message():
    """The canonical response walk rejects non-mapping choices/messages."""
    assert first_message({"choices": [{"message": "invalid"}]}) is None
    assert first_message({"choices": [{"message": {"content": "ok"}}]}) == {
        "content": "ok"
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('prefix ["a", "b"] suffix', ["a", "b"]),
        ('```json\n["fenced"]\n```', ["fenced"]),
        ("not-json", []),
        ('{"not": "a list"}', []),
        ('["unterminated"', []),
    ],
)
def test_parse_json_list_fragment_characterizes_response_text(
    text: str, expected: list[object]
) -> None:
    """List parsing accepts embedded/fenced JSON and fails closed."""
    assert parse_json_list_fragment(text) == expected


def test_parse_json_object_fragment_returns_empty_for_empty_message():
    """An empty model message has no JSON object to project."""
    assert parse_json_object_fragment("") == {}


def test_parse_json_object_fragment_reads_embedded_object():
    """An object embedded in prose is parsed without losing nesting."""
    assert parse_json_object_fragment(
        'prefix {"gene_id": "AT1G01010", "meta": {"source": "llm"}} suffix'
    ) == {
        "gene_id": "AT1G01010",
        "meta": {"source": "llm"},
    }


def test_parse_json_object_fragment_reads_fenced_object():
    """A fenced JSON object is parsed from the surrounding markdown."""
    assert parse_json_object_fragment(
        '\u0060\u0060\u0060json\n{"gene_id": "AT1G01010"}\n'
        "\u0060\u0060\u0060"
    ) == {"gene_id": "AT1G01010"}


@pytest.mark.parametrize("text", ["{not-json}", "[1, 2]"])
def test_parse_json_object_fragment_rejects_invalid_or_non_object(
    text: str,
) -> None:
    """Malformed and non-object JSON fragments return an empty mapping."""
    assert parse_json_object_fragment(text) == {}


def test_parse_follow_up_questions_rejects_non_list():
    """Verify parse follow up questions rejects non list."""
    assert parse_follow_up_questions('{"question": "next"}') == []


def test_attach_message_payload_creates_missing_message_shape():
    """Verify attach message payload creates missing message shape."""
    response = attach_message_payload(
        {"choices": [{}]},
        {"follow_up_questions": ["next"], "total": 1},
    )

    assert response["choices"][0]["message"] == {
        "follow_up_questions": ["next"],
        "total": 1,
    }


def test_format_upload_context_respects_length_limit():
    """Verify format upload context respects length limit."""
    context, total_length = format_upload_context(
        ["first", "second"],
        max_tokens=80,
    )

    assert (
        context
        == "[user upload file 1 begin]\nfirst\n[user upload file 1 end]"
    )
    assert total_length == len(context)


def test_format_retrieved_doc_context_preserves_existing_shape():
    """Verify format retrieved doc context preserves existing shape."""
    context, total_length = format_retrieved_doc_context(
        [
            {
                "title": "Paper",
                "subtitle": "Abstract",
                "big_content": "large text",
                "content": "short text",
            }
        ],
        max_tokens=200,
        initial_length=5,
    )

    assert context == (
        "[document 1 begin] Paper\n" "Abstract\nlarge text [document 1 end]"
    )
    assert total_length == 5 + len(context)
