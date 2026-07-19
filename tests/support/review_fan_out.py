# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Data-only helpers for Review fan-out contract tests."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, NotRequired, TypedDict
from unittest.mock import AsyncMock

import pytest
from langgraph.types import Send

from mcp_server_phytomni.agents.review.agent import DeepResearchAgent
from mcp_server_phytomni.config.defaults import ReviewConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

__all__ = [
    "FailureContract",
    "assert_chat_worker_success",
    "assert_failure_delta",
    "assert_send_common",
    "build_review_agent",
    "chat_response",
    "make_chat_app",
    "REVIEW_CHAT_APP_PATH",
    "run_chat_worker_failure",
    "review_results_prepare_state",
    "review_results_route_state",
    "review_results_worker_state",
    "review_summary_state",
    "revised_prepare_state",
    "revised_worker_state",
    "draft_route_state",
    "draft_worker_state",
    "follow_up_state",
    "plan_query_state",
]


class FailureContract(TypedDict):
    """Data-only expected delta for one failed Review worker."""

    result_key: str
    sentinel: str
    task_index: int
    task_label: str
    message: str
    extra_keys: NotRequired[Mapping[str, Any]]


REVIEW_CHAT_APP_PATH = "mcp_server_phytomni.agents.review.agent.CHAT_APP"


def build_review_agent() -> DeepResearchAgent:
    """Build a real Review agent with the test environment configuration."""
    return DeepResearchAgent(
        review_config=ReviewConfig(),
        sensitive_config=SensitiveConfig.load(),
    )


def chat_response(text: str) -> dict[str, Any]:
    """Build the minimal chat response consumed by fan-out workers."""
    return {"choices": [{"message": {"content": text}}]}


def make_chat_app(
    *, response: dict[str, Any] | None = None, error: Exception | None = None
) -> AsyncMock:
    """Build a deterministic ``CHAT_APP`` double for success or failure."""
    invoke = AsyncMock(
        side_effect=error,
        return_value={"response": response} if response is not None else None,
    )
    return AsyncMock(ainvoke=invoke)


def assert_send_common(sends: Iterable[object], *, node: str) -> None:
    """Assert the invariant portion of a three-way Send fan-out."""
    send_list = list(sends)
    assert len(send_list) == 3
    for index, send in enumerate(send_list):
        assert isinstance(send, Send)
        assert send.node == node
        assert send.arg["task_index"] == index


def assert_chat_worker_success(
    result: Mapping[str, Any],
    *,
    result_key: str,
    task_index: int,
    content: str,
    app: AsyncMock,
) -> None:
    """Assert the invariant success delta emitted by a chat worker."""
    assert result[result_key] == [(task_index, content)]
    assert "failures" not in result
    assert app.ainvoke.await_count == 1


async def run_chat_worker_failure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    worker_name: str,
    state: Mapping[str, Any],
    contract: FailureContract,
) -> None:
    """Run and assert one ChatApp worker's failure contract."""
    fake_app = make_chat_app(error=RuntimeError(contract["message"]))
    monkeypatch.setattr(REVIEW_CHAT_APP_PATH, fake_app)
    agent = build_review_agent()
    result = await getattr(agent, worker_name)(state)
    assert_failure_delta(result, contract)


def assert_failure_delta(
    result: Mapping[str, Any], contract: FailureContract
) -> None:
    """Assert a failed worker's sentinel and structured failure record."""
    assert result[contract["result_key"]] == [
        (contract["task_index"], contract["sentinel"])
    ]
    for key, value in contract.get("extra_keys", {}).items():
        assert result[key] == value
    failures = result.get("failures", [])
    assert len(failures) == 1
    record = failures[0]
    assert record["kind"] == "execute"
    assert contract["message"] in record["message"]
    assert record["task_label"] == contract["task_label"]
    assert record["traceback_digest"] is not None


def plan_query_state(
    query: str = "How does photosynthesis work?",
) -> dict[str, Any]:
    """Build the minimal ``plan_query_prep_node`` input state."""
    return {"original_user_query": query, "obs_file_list": []}


def review_summary_state() -> dict[str, Any]:
    """Build the minimal summary-prep state shared by Review tests."""
    return {
        "original_user_query": "Photosynthesis",
        "revised_reports": [
            {"subtopic": "dim1", "revised_report": "Content 1"},
            {"subtopic": "dim2", "revised_report": "Content 2"},
        ],
        "research_dimensions": ["dim1", "dim2"],
    }


def follow_up_state() -> dict[str, Any]:
    """Build the minimal follow-up-prep state shared by Review tests."""
    return {
        "original_user_query": "Photosynthesis",
        "summary_content": "A review of photosynthesis.",
        "all_raw_doc_list": [],
        "add_doc_list": [],
    }


def draft_worker_state(
    *, task_index: int, subtopic: str, knowledge: str
) -> dict[str, Any]:
    """Build the data-only state for a draft chat worker."""
    return {
        "task_index": task_index,
        "subtopic": subtopic,
        "knowledge": knowledge,
        "chat_payload": {"user_query": "prompt", "chat_kwargs": {}},
    }


def review_results_worker_state(
    *, task_index: int, subtopic: str
) -> dict[str, Any]:
    """Build the data-only state for a review-results chat worker."""
    return {
        "task_index": task_index,
        "subtopic": subtopic,
        "chat_payload": {"user_query": "prompt", "chat_kwargs": {}},
    }


def revised_worker_state(
    *,
    task_index: int,
    subtopic: str,
    draft_content: str,
    review_content: str,
    raw_doc_list: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the data-only state for a revised worker."""
    return {
        "task_index": task_index,
        "subtopic": subtopic,
        "draft_content": draft_content,
        "review_content": review_content,
        "raw_doc_list": raw_doc_list,
    }


def review_results_prepare_state() -> dict[str, Any]:
    """Build the minimal review-results prepare state."""
    return {
        "research_dimensions": ["photosynthesis"],
        "draft_contents": ["draft-A"],
    }


def revised_prepare_state() -> dict[str, Any]:
    """Build the minimal revised prepare state."""
    return {
        "research_dimensions": ["photosynthesis"],
        "draft_contents": ["draft-A"],
        "review_contents": ["{}"],
        "all_raw_doc_list": [],
    }


def draft_route_state() -> dict[str, Any]:
    """Build the deterministic draft route input rows."""
    return {
        "dimension_params": [
            {"subtopic": "photosynthesis", "knowledge": "snippet-0"},
            {"subtopic": "chlorophyll", "knowledge": "snippet-1"},
            {"subtopic": "stomatal", "knowledge": "snippet-2"},
        ]
    }


def review_results_route_state() -> dict[str, Any]:
    """Build the deterministic review-results route input rows."""
    return {
        "research_dimensions": [
            "photosynthesis",
            "chlorophyll",
            "stomatal",
        ],
        "draft_contents": ["draft-A", "draft-B", "draft-C"],
    }
