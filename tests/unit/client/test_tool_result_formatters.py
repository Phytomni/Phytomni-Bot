# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for MCP client result formatting helpers."""

import json

from mcp_client_phytomni.tool_result_formatters import format_tool_result


def test_knowledge_result_rewrites_citations_and_deduplicates_docs() -> None:
    """Verify cited documents are deduplicated in first-citation order."""
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
    answer = json.loads(result.answer)

    assert answer == {
        "content": "Evidence appears in [1] and [2,1].",
        "doc_list": [
            {"file_id": "doc-b", "title": "Paper B"},
            {"file_id": "doc-a", "title": "Paper A"},
        ],
    }
    assert result.follow_up_questions == ("Next question?",)


def test_data_result_allows_external_field_mapping() -> None:
    """Verify DataAgent headers can be mapped by caller-provided metadata."""
    payload = {
        "header": [{"caption": "gene_id"}, {"caption": "score"}],
        "data": [["Os01g01010", 0.8]],
    }

    result = format_tool_result(
        "DataAgent",
        payload,
        field_mapper=lambda headers: [
            "Gene ID" if h == "gene_id" else h for h in headers
        ],
    )

    assert json.loads(result.answer) == {
        "headers": ["Gene ID", "score"],
        "rows": [["Os01g01010", 0.8]],
    }
