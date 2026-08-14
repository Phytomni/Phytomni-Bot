# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""End-to-end graph proofs for required Knowledge evidence admission."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast
from unittest.mock import AsyncMock, Mock

import pytest
from langgraph.graph import END, START, StateGraph
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.analyst import core as analyst_core
from mcp_server_phytomni.agents.analyst import (
    graph_knowledge_subgraph as analyst_knowledge,
)
from mcp_server_phytomni.agents.analyst.core import AnalystAgent
from mcp_server_phytomni.agents.analyst.graph import AnalystGraphMixin
from mcp_server_phytomni.agents.analyst.state import AnalystInput, AnalystState
from mcp_server_phytomni.agents.data import agent as data_module
from mcp_server_phytomni.agents.data.agent import DataAgent
from mcp_server_phytomni.agents.data.state import DataInput
from mcp_server_phytomni.agents.knowledge.retrieval_result import (
    retrieval_unavailable_error,
)
from mcp_server_phytomni.agents.knowledge.state import (
    KnowledgeInput,
    KnowledgeOutput,
    KnowledgeState,
)
from mcp_server_phytomni.config.defaults import AnalystConfig, DataConfig
from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.runtime.task_manager import TaskManager
from tests.agents._subgraph_branch_fakes import install_chat_subgraph_mocks

pytestmark = pytest.mark.agent

RetrievalCase = Literal["partial", "no_match", "failure", "cancelled"]

_SAFE_ERROR = "Knowledge retrieval temporarily unavailable"
_ANALYST_BUILDER = (
    "mcp_server_phytomni.agents.analyst.core.build_knowledge_app"
)
_DATA_BUILDER = "mcp_server_phytomni.agents.data.agent.build_knowledge_app"


def _knowledge_result(case: RetrievalCase) -> dict[str, Any]:
    """Return one strict mounted-Knowledge result for an admissible case."""
    docs: list[dict[str, Any]] = []
    if case == "partial":
        docs.append(
            {
                "chunk_id": "reliable-doc",
                "title": "Reliable method",
                "content": "Use the bounded reliable method.",
            }
        )
    return {
        "retrieved_docs": docs,
        "retrieval_outcome": case,
        "final_response": {},
    }


def install_ordered_knowledge_boundary(
    monkeypatch: pytest.MonkeyPatch,
    *,
    builder_target: str,
    case: RetrievalCase,
    events: list[str],
) -> SimpleNamespace:
    """Install a real compiled Knowledge boundary with deterministic IO."""
    calls: list[dict[str, Any]] = []

    async def boundary(state: KnowledgeState) -> dict[str, Any]:
        events.append("knowledge")
        calls.append(dict(state))
        if case == "failure":
            raise retrieval_unavailable_error()
        if case == "cancelled":
            raise asyncio.CancelledError
        return _knowledge_result(case)

    workflow = StateGraph(
        KnowledgeState,
        input_schema=KnowledgeInput,
        output_schema=KnowledgeOutput,
    )
    workflow.add_node("retrieval_boundary", boundary)
    workflow.add_edge(START, "retrieval_boundary")
    workflow.add_edge("retrieval_boundary", END)
    compiled = workflow.compile()
    monkeypatch.setattr(builder_target, lambda **_kwargs: compiled)
    return SimpleNamespace(app=compiled, calls=calls)


@dataclass
class AnalystProofHarness:
    """Spies surrounding a real Analyst graph and mounted Knowledge app."""

    agent: AnalystAgent
    knowledge: SimpleNamespace
    output_dir: Path
    download_upload_context: AsyncMock
    submit_output_dir: AsyncMock
    upload_submit_meta: AsyncMock
    post_submit_job: AsyncMock


def _install_deterministic_analyst_nodes(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
) -> None:
    """Keep the real Analyst topology while replacing external chat IO."""

    async def plan_prep(
        _self: AnalystAgent, _state: Mapping[str, Any]
    ) -> dict[str, Any]:
        events.append("planning")
        return {
            "chat_payload": {"stage": "plan"},
            "pending_post": "plan_post_node",
        }

    async def plan_post(
        _self: AnalystAgent, _state: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {
            "plan": "Use the validated evidence.",
            "plan_retries": 1,
            "plan_feedback": None,
        }

    async def check_prep(
        _self: AnalystAgent, _state: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {
            "plan_feedback": "APPROVED",
            "chat_payload": None,
            "pending_post": "check_post_node",
        }

    async def check_post(
        _self: AnalystAgent, _state: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {}

    async def tool_extract_prep(
        _self: AnalystAgent, _state: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {
            "chat_payload": {"stage": "tool_extract"},
            "pending_post": "tool_extract_post_node",
        }

    async def tool_extract_post(
        _self: AnalystAgent, _state: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {"extracted_tools": []}

    original_submit = AnalystGraphMixin.submit_node

    async def ordered_submit(
        self: AnalystAgent, state: Mapping[str, Any]
    ) -> dict[str, Any]:
        events.append("submit")
        return await original_submit(self, cast(AnalystState, state))

    monkeypatch.setattr(AnalystAgent, "plan_prep_node", plan_prep)
    monkeypatch.setattr(AnalystAgent, "plan_post_node", plan_post)
    monkeypatch.setattr(AnalystAgent, "check_prep_node", check_prep)
    monkeypatch.setattr(AnalystAgent, "check_post_node", check_post)
    monkeypatch.setattr(
        AnalystAgent, "tool_extract_prep_node", tool_extract_prep
    )
    monkeypatch.setattr(
        AnalystAgent, "tool_extract_post_node", tool_extract_post
    )
    monkeypatch.setattr(AnalystAgent, "submit_node", ordered_submit)


def build_analyst_proof_harness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: RetrievalCase,
    events: list[str],
) -> AnalystProofHarness:
    """Build one real Analyst graph with deterministic external seams."""
    knowledge = install_ordered_knowledge_boundary(
        monkeypatch,
        builder_target=_ANALYST_BUILDER,
        case=case,
        events=events,
    )
    original_validate = analyst_knowledge.extract_analyst_knowledge_response

    def validate(response: Mapping[str, Any]) -> list[dict[str, Any]]:
        events.append("validation")
        return original_validate(response)

    monkeypatch.setattr(
        analyst_knowledge,
        "extract_analyst_knowledge_response",
        validate,
    )
    _install_deterministic_analyst_nodes(monkeypatch, events)
    install_chat_subgraph_mocks(
        monkeypatch,
        analyst_core.__name__,
        legacy_response=None,
        subgraph_response={},
    )
    download_upload_context = AsyncMock(return_value=("", 0))
    monkeypatch.setattr(
        analyst_knowledge,
        "download_upload_context",
        download_upload_context,
    )
    output_dir = tmp_path / "analyst-output"
    agent = AnalystAgent(
        analyst_config=AnalystConfig(CREATE_DIR=False),
        sensitive_config=SensitiveConfig.load(),
    )
    submit_output_dir = AsyncMock(return_value=str(output_dir))
    upload_submit_meta = AsyncMock(
        return_value=("obs://test/task.yaml", "obs://test/model.yaml")
    )
    submit_headers = AsyncMock(
        return_value={"Content-Type": "application/json"}
    )
    submit_job_data = Mock(return_value=("proof-job", {"job": "proof"}))
    post_submit_job = AsyncMock(
        return_value={
            "task_id": "accepted-proof-task",
            "task_status": "SUBMITTED",
            "job_name": "proof-job",
            "output_dir": str(output_dir),
        }
    )
    monkeypatch.setattr(agent, "_submit_output_dir", submit_output_dir)
    monkeypatch.setattr(agent, "_upload_submit_meta", upload_submit_meta)
    monkeypatch.setattr(agent, "_submit_headers", submit_headers)
    monkeypatch.setattr(agent, "_submit_job_data", submit_job_data)
    monkeypatch.setattr(agent, "_post_submit_job", post_submit_job)
    return AnalystProofHarness(
        agent=agent,
        knowledge=knowledge,
        output_dir=output_dir,
        download_upload_context=download_upload_context,
        submit_output_dir=submit_output_dir,
        upload_submit_meta=upload_submit_meta,
        post_submit_job=post_submit_job,
    )


def analyst_graph_input(output_dir: Path) -> AnalystInput:
    """Return an input that traverses mounted Knowledge before planning."""
    return AnalystInput(
        query="",
        goal_description="Investigate a bounded synthetic target.",
        data_list={"obs://test/input.tsv": "synthetic input"},
        obs_file_list=[],
        output_dir=str(output_dir),
        compute_resource="small",
        is_polling=False,
        is_auto_select=False,
        is_preset_plan=False,
    )


def assert_no_task_rows(db_path: Path) -> None:
    """Assert no submitted or running task was persisted."""
    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    assert count == 0


def isolate_task_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Isolate task persistence and output resolution for one test."""
    db_path = tmp_path / "tasks.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))
    TaskManager(str(db_path))
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.shared.analysis_storage."
        "relay_mode_enabled",
        lambda: True,
    )
    return db_path


@pytest.fixture(autouse=True)
def _isolate_task_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep task persistence and output resolution inside each test."""
    isolate_task_store(tmp_path, monkeypatch)


@pytest.mark.parametrize("case", ["partial", "no_match"])
async def test_data_graph_admits_valid_retrieval_outcomes_in_order(
    case: RetrievalCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Data validates admissible Knowledge output before the SQL action."""
    events: list[str] = []
    knowledge = install_ordered_knowledge_boundary(
        monkeypatch,
        builder_target=_DATA_BUILDER,
        case=case,
        events=events,
    )
    original_validate = data_module.extract_data_knowledge_response

    def validate(response: Mapping[str, Any]) -> list[dict[str, Any]]:
        events.append("validation")
        return original_validate(response)

    monkeypatch.setattr(
        data_module, "extract_data_knowledge_response", validate
    )
    install_chat_subgraph_mocks(
        monkeypatch,
        data_module.__name__,
        legacy_response=None,
        subgraph_response={
            "choices": [{"message": {"content": "rewritten query"}}]
        },
    )

    async def execute(_request: Any) -> dict[str, Any]:
        events.append("data_action")
        return {"rows": []}

    execute_spy = AsyncMock(side_effect=execute)
    monkeypatch.setattr(data_module, "execute_nl2sql_request", execute_spy)
    agent = DataAgent(
        data_config=DataConfig(),
        sensitive_config=SensitiveConfig.load(),
    )
    result = await agent.app.ainvoke(
        cast(
            DataInput,
            {
                "user_query": "List the synthetic rows.",
                "is_rewrite": True,
                "dialog_id": "data-proof",
            },
        ),
        config={"configurable": {"thread_id": f"data-{case}"}},
    )

    assert events == ["knowledge", "validation", "data_action"]
    assert len(knowledge.calls) == 1
    execute_spy.assert_awaited_once()
    assert result["final_response"] == {"rows": []}
    assert "No Data" not in str(result)
    assert_no_task_rows(tmp_path / "tasks.sqlite")


@pytest.mark.parametrize("case", ["failure", "cancelled"])
async def test_data_graph_rejects_unusable_retrieval_without_side_effects(
    case: RetrievalCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Data propagates retrieval failure/cancellation before SQL action."""
    events: list[str] = []
    knowledge = install_ordered_knowledge_boundary(
        monkeypatch,
        builder_target=_DATA_BUILDER,
        case=case,
        events=events,
    )
    execute_spy = AsyncMock(return_value={"rows": []})
    monkeypatch.setattr(data_module, "execute_nl2sql_request", execute_spy)
    agent = DataAgent(
        data_config=DataConfig(),
        sensitive_config=SensitiveConfig.load(),
    )
    expected_error = McpError if case == "failure" else asyncio.CancelledError

    with pytest.raises(expected_error) as exc_info:
        await agent.app.ainvoke(
            cast(
                DataInput,
                {
                    "user_query": "List the synthetic rows.",
                    "is_rewrite": True,
                    "dialog_id": "data-proof",
                },
            ),
            config={"configurable": {"thread_id": f"data-{case}"}},
        )

    assert events == ["knowledge"]
    assert len(knowledge.calls) == 1
    execute_spy.assert_not_awaited()
    assert_no_task_rows(tmp_path / "tasks.sqlite")
    if case == "failure":
        assert str(exc_info.value) == _SAFE_ERROR
        assert "No Data" not in str(exc_info.value)


@pytest.mark.parametrize("case", ["partial", "no_match"])
async def test_analyst_graph_orders_knowledge_validation_plan_and_submit(
    case: RetrievalCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admissible evidence traverses the real mounted Analyst graph once."""
    events: list[str] = []
    harness = build_analyst_proof_harness(monkeypatch, tmp_path, case, events)

    result = await harness.agent.app.ainvoke(
        analyst_graph_input(harness.output_dir),
        config={"configurable": {"thread_id": f"analyst-{case}"}},
    )

    assert events == ["knowledge", "validation", "planning", "submit"]
    assert len(harness.knowledge.calls) == 1
    assert result["task_id"] == "accepted-proof-task"
    harness.download_upload_context.assert_awaited_once()
    harness.submit_output_dir.assert_awaited_once()
    harness.upload_submit_meta.assert_awaited_once()
    harness.post_submit_job.assert_awaited_once()
    assert_no_task_rows(tmp_path / "tasks.sqlite")
    assert not harness.output_dir.exists()
    assert "No Data" not in str(result)


@pytest.mark.parametrize("case", ["failure", "cancelled"])
async def test_analyst_graph_stops_at_unusable_knowledge_without_side_effects(
    case: RetrievalCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure/cancellation stops before validation, planning, or submit."""
    events: list[str] = []
    harness = build_analyst_proof_harness(monkeypatch, tmp_path, case, events)
    expected_error = McpError if case == "failure" else asyncio.CancelledError

    with pytest.raises(expected_error) as exc_info:
        await harness.agent.app.ainvoke(
            analyst_graph_input(harness.output_dir),
            config={"configurable": {"thread_id": f"analyst-{case}"}},
        )

    assert events == ["knowledge"]
    assert len(harness.knowledge.calls) == 1
    harness.download_upload_context.assert_not_awaited()
    harness.submit_output_dir.assert_not_awaited()
    harness.upload_submit_meta.assert_not_awaited()
    harness.post_submit_job.assert_not_awaited()
    assert_no_task_rows(tmp_path / "tasks.sqlite")
    assert not harness.output_dir.exists()
    if case == "failure":
        assert str(exc_info.value) == _SAFE_ERROR
        assert "No Data" not in str(exc_info.value)
