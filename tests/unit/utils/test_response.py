# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for OpenAI-style response helpers."""

import pytest

from mcp_server_phytomni.utils import (
    attach_message_payload,
    format_retrieved_doc_context,
    format_upload_context,
    message_content,
    parse_follow_up_questions,
    parse_json_list_fragment,
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


def test_message_content_returns_empty_string_for_invalid_shape():
    """Verify message content returns empty string for invalid shape."""
    assert message_content({"choices": []}) == ""
    assert message_content(None) == ""


def test_parse_json_list_fragment_reads_embedded_list():
    """Verify parse json list fragment reads embedded list."""
    assert parse_json_list_fragment('prefix ["a", "b"] suffix') == [
        "a",
        "b",
    ]


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
