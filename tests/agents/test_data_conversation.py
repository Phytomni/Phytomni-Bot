# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for DataAgent conversation-context intent preparation."""

from __future__ import annotations

import json
from uuid import UUID

import pytest

from mcp_server_phytomni.agents.data.conversation import (
    DataConversationAdapter,
)
from mcp_server_phytomni.runtime.conversation_context.models import (
    ContextDelta,
    ContextProjection,
)
from mcp_server_phytomni.runtime.conversation_context.projection import (
    agent_thread_id,
)

pytestmark = pytest.mark.agent

_CONVERSATION_KEY = UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7")
_OTHER_CONVERSATION_KEY = UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad8")


def _projection(
    query: str,
    *,
    conversation_key: UUID = _CONVERSATION_KEY,
    delta: ContextDelta | None = None,
) -> ContextProjection:
    """Build one bounded Data projection from a prior delta."""
    delta = delta or ContextDelta()
    return ContextProjection(
        current_query=query,
        relevant_recent_turns=[],
        active_entities=delta.entity_upserts,
        open_questions=[],
        artifact_refs=delta.artifact_upserts,
        task_summary=delta.summary_update or "",
        agent_thread_id=agent_thread_id(conversation_key, "DataAgent"),
        locale="en-US",
        token_budget=2048,
        context_truncated=False,
    )


def _result(
    *,
    headers: list[str],
    rows: list[list[object]],
    summary: str,
    table_id: str = "expression_table",
    artifact_id: str = "artifact-expression",
) -> dict[str, object]:
    """Build one native DataAgent run envelope for delta extraction."""
    return {
        "id": "run-data",
        "object": "agent.run",
        "agent": "data",
        "status": "succeeded",
        "task_ids": [],
        "result": {
            "formatted": {
                "answer": summary,
                "metadata": {
                    "user_query": "placeholder",
                    "rewrite_query": "placeholder",
                    "is_rewrite": True,
                },
            },
            "raw": {
                "header": [{"caption": header} for header in headers],
                "data": rows,
                "table_id": table_id,
                "artifact_id": artifact_id,
                "summary": summary,
                "sql": "SELECT * FROM secrets",
                "database_url": "postgresql://user:pass@example/db",
            },
        },
    }


def test_prepare_merges_follow_up_filters_and_grouping_without_losing_dataset() -> (
    None
):
    """Follow-up filters and regrouping keep the active dataset."""
    first = DataConversationAdapter()
    initial = first.prepare(_projection("Show expression by tissue"))
    assert initial == {
        "user_query": "Show expression by tissue",
        "rewrite_query": "Show expression by tissue",
        "dialog_id": (
            f"{agent_thread_id(_CONVERSATION_KEY, 'DataAgent')}-nl2sql"
        ),
        "thread_id": agent_thread_id(_CONVERSATION_KEY, "DataAgent"),
    }
    first_delta = first.delta(
        _result(
            headers=["tissue", "expression"],
            rows=[["leaf", 10], ["root", 5]],
            summary="Expression by tissue",
        )
    )

    second = DataConversationAdapter()
    follow_up = second.prepare(_projection("Only rice", delta=first_delta))
    assert follow_up["user_query"] == "Show expression by tissue for rice"
    second_delta = second.delta(
        _result(
            headers=["tissue", "expression"],
            rows=[["leaf", 8], ["root", 4]],
            summary="Expression by tissue for rice",
        )
    )

    third = DataConversationAdapter()
    regrouped = third.prepare(_projection("Group by year", delta=second_delta))
    assert regrouped["user_query"] == "Show expression by year for rice"
    third_delta = third.delta(
        _result(
            headers=["year", "expression"],
            rows=[[2024, 12], [2025, 9]],
            summary="Expression by year for rice",
        )
    )

    assert "data:dimension:tissue" in third_delta.entity_removals
    assert "data:dimension:year" in {
        entity.entity_id for entity in third_delta.entity_upserts
    }


def test_prepare_keeps_same_thread_and_dialog_for_same_conversation() -> None:
    """Same conversation keys stay stable; different keys diverge."""
    first = DataConversationAdapter().prepare(_projection("Show expression"))
    second = DataConversationAdapter().prepare(_projection("Only rice"))
    other = DataConversationAdapter().prepare(
        _projection(
            "Show expression",
            conversation_key=_OTHER_CONVERSATION_KEY,
        )
    )

    assert first["thread_id"] == second["thread_id"]
    assert first["dialog_id"] == second["dialog_id"]
    assert first["dialog_id"] == f"{first['thread_id']}-nl2sql"
    assert other["thread_id"] != first["thread_id"]
    assert other["dialog_id"] != first["dialog_id"]


def test_delta_stores_only_bounded_intent_metadata_without_rows_or_sql() -> (
    None
):
    """Context deltas keep semantic Data intent, never rows or credentials."""
    adapter = DataConversationAdapter()
    adapter.prepare(_projection("Show expression by tissue"))

    delta = adapter.delta(
        _result(
            headers=["tissue", "expression"],
            rows=[["leaf", 10], ["root", 5]],
            summary="Expression by tissue",
        )
    )

    payload = json.dumps(delta.model_dump(mode="json"), sort_keys=True)

    assert "expression_table" in payload
    assert "data:dataset:expression" in payload
    assert "dimension:tissue" in payload
    assert "filter:species=rice" not in payload
    assert "column:expression" in payload
    assert "row_count:2" in payload
    assert "Expression by tissue" in payload
    assert "artifact-expression" in payload
    assert "leaf" not in payload
    assert "SELECT * FROM secrets" not in payload
    assert "postgresql://user:pass@example/db" not in payload
