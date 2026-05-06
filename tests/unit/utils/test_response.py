# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for OpenAI-style response helpers."""

# pylint: disable=missing-function-docstring

import pytest

from mcp_server_phytomni.utils import (
    attach_message_payload,
    message_content,
    parse_follow_up_questions,
    parse_json_list_fragment,
)

pytestmark = pytest.mark.unit


def test_message_content_reads_first_assistant_message():
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
    assert message_content({"choices": []}) == ""
    assert message_content(None) == ""


def test_parse_json_list_fragment_reads_embedded_list():
    assert parse_json_list_fragment('prefix ["a", "b"] suffix') == [
        "a",
        "b",
    ]


def test_parse_follow_up_questions_rejects_non_list():
    assert parse_follow_up_questions('{"question": "next"}') == []


def test_attach_message_payload_creates_missing_message_shape():
    response = attach_message_payload(
        {"choices": [{}]},
        {"follow_up_questions": ["next"], "total": 1},
    )

    assert response["choices"][0]["message"] == {
        "follow_up_questions": ["next"],
        "total": 1,
    }
