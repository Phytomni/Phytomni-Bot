# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the shared Analyst/Review knowledge mapping factories."""

from __future__ import annotations

from typing import Any

import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.graphs.analyst_to_knowledge_adapters import (
    build_analyst_knowledge_input,
    extract_analyst_knowledge_response,
)
from mcp_server_phytomni.graphs.knowledge_adapters import (
    make_knowledge_input_adapter,
    make_knowledge_output_adapter,
)
from mcp_server_phytomni.graphs.review_to_knowledge_adapters import (
    build_review_knowledge_input,
    extract_review_knowledge_response,
)

pytestmark = pytest.mark.agent


def test_input_factory_preserves_the_shared_retrieve_shape() -> None:
    """Both domain adapters project the same retrieve-only payload."""
    repo_id_dict = {"repo-a": 4, "repo-b": 2}
    analyst_adapter = make_knowledge_input_adapter(
        query_key="user_query",
        output_key="repo_id_dict",
    )
    review_adapter = make_knowledge_input_adapter(
        query_key="user_query",
        output_key="repo_id_dict",
    )

    analyst_payload = analyst_adapter("PHYB", repo_id_dict)
    review_payload = review_adapter("PHYB", repo_id_dict)

    assert (
        analyst_payload
        == review_payload
        == {
            "user_query": "PHYB",
            "repo_id_dict": repo_id_dict,
            "is_generate": False,
            "is_follow_up": False,
            "locale": "en-US",
        }
    )
    assert analyst_payload.get("repo_id_dict") is not repo_id_dict


def test_output_factory_requires_a_consistent_knowledge_output() -> None:
    """Missing evidence metadata is unavailable retrieval, not no-match."""
    extract_docs = make_knowledge_output_adapter("retrieved_docs")
    docs = [
        {
            "chunk_id": "paper-1",
            "title": "Paper",
            "content": "Evidence",
        }
    ]
    complete = {
        "retrieved_docs": docs,
        "retrieval_outcome": "complete",
        "final_response": {"content": "answer"},
    }

    assert extract_docs(complete) == docs
    assert extract_docs(complete) is not docs

    no_match = {
        "retrieved_docs": [],
        "retrieval_outcome": "no_match",
        "final_response": {},
    }
    assert not extract_docs(no_match)

    for invalid in (
        {"retrieved_docs": docs},
        {"retrieved_docs": None},
        {},
    ):
        with pytest.raises(
            McpError, match="Knowledge retrieval temporarily unavailable"
        ):
            extract_docs(invalid)


def test_domain_adapters_keep_named_boundaries_and_local_queries() -> None:
    """Thin domain wrappers retain names while query wording stays local."""
    repo_id_dict = {"repo": 8}
    analyst_payload = build_analyst_knowledge_input(
        "goal description",
        repo_id_dict,
    )
    review_payload = build_review_knowledge_input(
        "research dimension",
        repo_id_dict,
    )

    assert build_analyst_knowledge_input.__name__ == (
        "build_analyst_knowledge_input"
    )
    assert build_review_knowledge_input.__name__ == (
        "build_review_knowledge_input"
    )
    assert analyst_payload["user_query"] == "goal description"
    assert review_payload["user_query"] == "research dimension"
    assert analyst_payload.get("repo_id_dict") == review_payload.get(
        "repo_id_dict"
    )

    response: dict[str, Any] = {
        "retrieved_docs": [
            {"chunk_id": "paper-1", "title": "Paper", "content": "Evidence"}
        ],
        "retrieval_outcome": "complete",
        "final_response": {"content": "answer"},
    }
    expected_docs = [
        {"chunk_id": "paper-1", "title": "Paper", "content": "Evidence"}
    ]
    assert extract_analyst_knowledge_response(response) == expected_docs
    assert extract_review_knowledge_response(response) == expected_docs
    assert extract_analyst_knowledge_response.__name__ == (
        "extract_analyst_knowledge_response"
    )
    assert extract_review_knowledge_response.__name__ == (
        "extract_review_knowledge_response"
    )
