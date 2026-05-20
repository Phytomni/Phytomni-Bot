# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for server-side MCP result formatting.

Covers citation rewriting, document deduplication, follow-up
extraction, and DataAgent table serialization at the MCP boundary.
"""

import json

import pytest

from mcp_server_phytomni.mcp.result_formatting import format_tool_result

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


def test_data_result_serializes_headers_and_rows() -> None:
    """Verify DataAgent headers and rows serialize without a mapper."""
    payload = {
        "header": [{"caption": "gene_id"}, {"caption": "score"}],
        "data": [["Os01g01010", 0.8]],
    }

    result = format_tool_result("DataAgent", payload)

    assert json.loads(result.answer) == {
        "headers": ["gene_id", "score"],
        "rows": [["Os01g01010", 0.8]],
    }
