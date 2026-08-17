# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Preset-plan architecture proofs for scientific Analyst dispatchers.

Research, Network, and Design intentionally enter Analyst with a preset plan.
That route bypasses mounted method Knowledge, extracts tools, retrieves direct
tool usage, and submits remote work. These tests preserve that architecture
while proving failures and cancellation cannot be projected as acceptance.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, NoReturn
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from mcp_server_phytomni.agents.analyst import core as analyst_core
from mcp_server_phytomni.agents.analyst import graph as analyst_graph
from mcp_server_phytomni.agents.analyst.core import AnalystAgent
from mcp_server_phytomni.agents.design import agent as design_agent
from mcp_server_phytomni.agents.design.agent import (
    DigitalDesignAgents,
    _DispatchOptions,
)
from mcp_server_phytomni.agents.network import agent as network_agent
from mcp_server_phytomni.agents.network.agent import GeneNetworkAgents
from mcp_server_phytomni.agents.research import agent as research_agent
from mcp_server_phytomni.agents.research.agent import (
    InSilicoResearchAgents,
    ResearchTaskContext,
)
from mcp_server_phytomni.agents.shared import remote_analysis
from mcp_server_phytomni.config.defaults import AnalystConfig
from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.mcp.formatting.dispatch import format_tool_result
from mcp_server_phytomni.runtime.request_context import (
    current_accepted_task_ids,
    request_context,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry
from mcp_server_phytomni.runtime.submit_recorder import records_submission
from tests.agents._subgraph_branch_fakes import install_chat_subgraph_mocks
from tests.agents.test_knowledge_failure_consumers import (
    assert_no_task_rows,
    install_ordered_knowledge_boundary,
    isolate_task_store,
)
from tests.support.asyncio_helpers import GRAPH_CANCELLATION

pytestmark = pytest.mark.agent

Consumer = Literal["research", "network", "design"]
FailureBoundary = Literal["extraction", "retrieval", "submission"]

_CONSUMERS: tuple[Consumer, ...] = ("research", "network", "design")
_FAILURE_BOUNDARIES: tuple[FailureBoundary, ...] = (
    "extraction",
    "retrieval",
    "submission",
)
_SAFE_ERRORS = {
    "extraction": "Tool extraction failed",
    "retrieval": "Knowledge retrieval temporarily unavailable",
    "submission": "Submission failed after retries",
}
_SENSITIVE_DETAIL = "provider credential must remain private"
_ANALYST_BUILDER = (
    "mcp_server_phytomni.agents.analyst.core.build_knowledge_app"
)
_ANALYST_RECORDER_AGENT = "analyst"
_ANALYST_TOOL_NAME = "AnalystAgent"


@dataclass
class PresetPlanHarness:
    """External boundary spies around one real compiled Analyst graph."""

    agent: AnalystAgent
    knowledge: SimpleNamespace
    chat_app: SimpleNamespace
    tool_retrieve: AsyncMock
    upload_submit_meta: AsyncMock
    post_submit_job: AsyncMock
    events: list[str]


def _tool_extract_response() -> dict[str, Any]:
    """Return one valid tool-extraction chat response."""
    return {
        "choices": [
            {"message": {"content": json.dumps({"tools": ["bounded-tool"]})}}
        ]
    }


def _raise_safe_boundary_error(boundary: FailureBoundary) -> NoReturn:
    """Raise a stable public error while retaining a private cause."""
    raise McpError(
        ErrorData(code=INTERNAL_ERROR, message=_SAFE_ERRORS[boundary])
    ) from RuntimeError(_SENSITIVE_DETAIL)


def _build_preset_plan_harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    failure_boundary: FailureBoundary | None = None,
    cancellation: bool = False,
) -> PresetPlanHarness:
    """Build a real preset-plan Analyst graph with external IO seams."""
    events: list[str] = []
    knowledge = install_ordered_knowledge_boundary(
        monkeypatch,
        builder_target=_ANALYST_BUILDER,
        case="failure",
        events=events,
    )
    _legacy_chat, chat_app = install_chat_subgraph_mocks(
        monkeypatch,
        analyst_core.__name__,
        legacy_response=None,
        subgraph_response=_tool_extract_response(),
    )

    async def chat_boundary(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        events.append("tool_extract_chat")
        if failure_boundary == "extraction":
            if cancellation:
                raise asyncio.CancelledError
            _raise_safe_boundary_error("extraction")
        return {"response": _tool_extract_response()}

    chat_app.ainvoke.side_effect = chat_boundary

    async def retrieve_tool_usage(**_kwargs: Any) -> dict[str, Any]:
        events.append("tool_usage_retrieval")
        if failure_boundary == "retrieval":
            if cancellation:
                raise asyncio.CancelledError
            _raise_safe_boundary_error("retrieval")
        return {
            "doc_list": [
                {
                    "chunk_id": "bounded-tool-doc",
                    "title": "Bounded tool documentation",
                    "content": "Use the bounded tool safely.",
                }
            ],
            "total": 1,
            "outcome": "partial",
            "failures": [
                {
                    "source": "secondary-tool-source",
                    "kind": "timeout",
                    "retryable": True,
                }
            ],
        }

    tool_retrieve = AsyncMock(side_effect=retrieve_tool_usage)
    monkeypatch.setattr(analyst_graph, "retrieve", tool_retrieve)
    monkeypatch.setattr(analyst_graph, "relay_mode_enabled", lambda: True)

    agent = AnalystAgent(
        analyst_config=AnalystConfig(CREATE_DIR=False),
        sensitive_config=SensitiveConfig.load(),
    )

    async def upload_submit_meta(
        *_args: Any, **_kwargs: Any
    ) -> tuple[str, str]:
        events.append("upload_boundary")
        return ("obs://test/task.yaml", "obs://test/model.yaml")

    upload_submit_meta_spy = AsyncMock(side_effect=upload_submit_meta)

    async def post_submit_job(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        events.append("remote_submit")
        if failure_boundary == "submission":
            if cancellation:
                raise asyncio.CancelledError
            _raise_safe_boundary_error("submission")
        return {
            "task_id": "accepted-preset-task",
            "task_status": "PENDING",
            "job_name": "preset-proof-job",
            "output_dir": str(_args[3]),
        }

    post_submit_job_spy = AsyncMock(side_effect=post_submit_job)
    monkeypatch.setattr(agent, "_upload_submit_meta", upload_submit_meta_spy)
    monkeypatch.setattr(agent, "_post_submit_job", post_submit_job_spy)
    return PresetPlanHarness(
        agent=agent,
        knowledge=knowledge,
        chat_app=chat_app,
        tool_retrieve=tool_retrieve,
        upload_submit_meta=upload_submit_meta_spy,
        post_submit_job=post_submit_job_spy,
        events=events,
    )


def _patch_acceptance(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Spy on every acceptance projection without replacing validation."""
    acceptance_spy = Mock(wraps=remote_analysis.accepted_submission)
    for module in (
        design_agent,
        network_agent,
        research_agent,
        remote_analysis,
    ):
        monkeypatch.setattr(module, "accepted_submission", acceptance_spy)
    return acceptance_spy


def _build_consumer(
    consumer: Consumer,
    analyst_agent: AnalystAgent,
    output_dir: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], Awaitable[dict[str, Any]]]:
    """Build a real scientific wrapper around the real Analyst graph."""
    sensitive = SensitiveConfig.load()
    if consumer == "research":
        research = InSilicoResearchAgents(
            sensitive_config=sensitive,
            analyst_agent=analyst_agent,
        )
        monkeypatch.setattr(
            research_agent,
            "collect_research_evidence",
            AsyncMock(return_value=None),
        )
        task = ResearchTaskContext(
            goal_description="Investigate a bounded synthetic target.",
            context="synthetic research context",
            data_list={"obs://synthetic/data.tsv": "synthetic data"},
            output_dir=output_dir,
            task_name="research_goal_synthetic",
            thread_id="thread-research-synthetic",
        )

        async def invoke_research() -> dict[str, Any]:
            return await getattr(research, "_submit_research_task")(task)

        return invoke_research

    if consumer == "network":
        network = GeneNetworkAgents(
            sensitive_config=sensitive,
            analyst_agent=analyst_agent,
        )
        monkeypatch.setattr(
            network,
            "_analysis_prompt_parts",
            lambda *_args, **_kwargs: (
                "synthetic network goal",
                "synthetic network preset plan",
                {"obs://synthetic/network.tsv": "synthetic data"},
            ),
        )

        async def invoke_network() -> dict[str, Any]:
            return await getattr(network, "_dispatch_and_wait_analysis")(
                analysis_type="gene_network_analysis",
                species_code="ath",
                to_id="TO:synthetic-network",
                output_dir=output_dir,
            )

        return invoke_network

    design = DigitalDesignAgents(
        sensitive_config=sensitive,
        analyst_agent=analyst_agent,
    )
    monkeypatch.setattr(
        design,
        "_analysis_prompt_parts",
        lambda *_args, **_kwargs: (
            "synthetic design goal",
            "synthetic design preset plan",
            {"obs://synthetic/design.tsv": "synthetic data"},
        ),
    )

    async def invoke_design() -> dict[str, Any]:
        return await getattr(design, "_dispatch_and_wait_analysis")(
            analysis_type="protein_design_analysis",
            species_code="ath",
            gene_id="AT:synthetic-design",
            options=_DispatchOptions(output_dir=output_dir),
        )

    return invoke_design


def _assert_no_dispatch_side_effects(db_path: Path, output_dir: str) -> None:
    """Assert one rejected dispatch created no task row or local output."""
    assert_no_task_rows(db_path)
    assert not Path(output_dir).exists()


async def _record_and_format_consumer(
    invoke: Callable[[], Awaitable[dict[str, Any]]],
    projection: Mock,
) -> Any:
    """Run one real consumer through the recorder then final formatter."""

    async def adapter(_args: Any) -> dict[str, Any]:
        """Adapt the focused consumer seam to a submit-handler shape."""
        return await invoke()

    raw = await records_submission(_ANALYST_RECORDER_AGENT)(adapter)(
        SimpleNamespace()
    )
    return projection(_ANALYST_TOOL_NAME, raw)


def _assert_no_recorded_terminal_projection(
    db_path: Path,
    projection: Mock,
) -> None:
    """Assert rejected work never reaches acceptance or terminal projection."""
    assert current_accepted_task_ids() == ()
    runs = RunRegistry(str(db_path)).list_runs(owner="anonymous")
    for run in runs:
        assert run.status not in {"running", "succeeded"}
        assert not run.task_ids
        assert run.result is None
    projection.assert_not_called()


@pytest.fixture(autouse=True)
def _isolate_dispatch_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep task persistence and output resolution inside each test."""
    isolate_task_store(tmp_path, monkeypatch)


@pytest.mark.parametrize("consumer", _CONSUMERS)
async def test_preset_plan_bypasses_mounted_knowledge_and_submits(
    consumer: Consumer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scientific preset plans use extraction, not mounted Knowledge."""
    harness = _build_preset_plan_harness(monkeypatch)
    acceptance_spy = _patch_acceptance(monkeypatch)
    output_dir = str(tmp_path / consumer / "run" / "children" / "part-001")
    invoke = _build_consumer(consumer, harness.agent, output_dir, monkeypatch)

    result = await invoke()

    assert harness.events == [
        "tool_extract_chat",
        "tool_usage_retrieval",
        "upload_boundary",
        "remote_submit",
    ]
    assert harness.knowledge.calls == []
    harness.chat_app.ainvoke.assert_awaited_once()
    harness.tool_retrieve.assert_awaited_once()
    harness.upload_submit_meta.assert_awaited_once()
    harness.post_submit_job.assert_awaited_once()
    assert result["task_id"] == "accepted-preset-task"
    assert acceptance_spy.call_count >= 1
    assert "No Data" not in str(result)


@pytest.mark.parametrize("consumer", _CONSUMERS)
@pytest.mark.parametrize("failure_boundary", _FAILURE_BOUNDARIES)
async def test_safe_boundary_failure_cannot_become_scientific_success(
    consumer: Consumer,
    failure_boundary: FailureBoundary,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanitized evidence failure never projects acceptance."""
    harness = _build_preset_plan_harness(
        monkeypatch,
        failure_boundary=failure_boundary,
    )
    acceptance_spy = _patch_acceptance(monkeypatch)
    output_dir = str(tmp_path / consumer / "run" / "children" / "part-001")
    invoke = _build_consumer(consumer, harness.agent, output_dir, monkeypatch)

    with pytest.raises(McpError) as exc_info:
        await invoke()

    assert str(exc_info.value) == _SAFE_ERRORS[failure_boundary]
    assert _SENSITIVE_DETAIL not in str(exc_info.value)
    assert "No Data" not in str(exc_info.value)
    assert harness.knowledge.calls == []
    acceptance_spy.assert_not_called()
    _assert_no_dispatch_side_effects(tmp_path / "tasks.sqlite", output_dir)
    if failure_boundary == "extraction":
        assert harness.events == ["tool_extract_chat"]
        harness.tool_retrieve.assert_not_awaited()
        harness.upload_submit_meta.assert_not_awaited()
        harness.post_submit_job.assert_not_awaited()
    elif failure_boundary == "retrieval":
        assert harness.events == [
            "tool_extract_chat",
            "tool_usage_retrieval",
        ]
        harness.tool_retrieve.assert_awaited_once()
        harness.upload_submit_meta.assert_not_awaited()
        harness.post_submit_job.assert_not_awaited()
    else:
        assert harness.events == [
            "tool_extract_chat",
            "tool_usage_retrieval",
            "upload_boundary",
            "remote_submit",
        ]
        harness.tool_retrieve.assert_awaited_once()
        harness.upload_submit_meta.assert_awaited_once()
        harness.post_submit_job.assert_awaited_once()


@pytest.mark.parametrize("consumer", _CONSUMERS)
@pytest.mark.parametrize("failure_boundary", _FAILURE_BOUNDARIES)
async def test_boundary_cancellation_propagates_without_side_effects(
    consumer: Consumer,
    failure_boundary: FailureBoundary,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Evidence-boundary cancellation propagates unchanged."""
    harness = _build_preset_plan_harness(
        monkeypatch,
        failure_boundary=failure_boundary,
        cancellation=True,
    )
    acceptance_spy = _patch_acceptance(monkeypatch)
    output_dir = str(tmp_path / consumer / "run" / "children" / "part-001")
    invoke = _build_consumer(consumer, harness.agent, output_dir, monkeypatch)

    with pytest.raises(GRAPH_CANCELLATION):
        await invoke()

    assert harness.knowledge.calls == []
    acceptance_spy.assert_not_called()
    _assert_no_dispatch_side_effects(tmp_path / "tasks.sqlite", output_dir)
    if failure_boundary == "extraction":
        assert harness.events == ["tool_extract_chat"]
        harness.tool_retrieve.assert_not_awaited()
        harness.upload_submit_meta.assert_not_awaited()
        harness.post_submit_job.assert_not_awaited()
    elif failure_boundary == "retrieval":
        assert harness.events == [
            "tool_extract_chat",
            "tool_usage_retrieval",
        ]
        harness.tool_retrieve.assert_awaited_once()
        harness.upload_submit_meta.assert_not_awaited()
        harness.post_submit_job.assert_not_awaited()
    else:
        assert harness.events == [
            "tool_extract_chat",
            "tool_usage_retrieval",
            "upload_boundary",
            "remote_submit",
        ]
        harness.tool_retrieve.assert_awaited_once()
        harness.upload_submit_meta.assert_awaited_once()
        harness.post_submit_job.assert_awaited_once()


@pytest.mark.parametrize("consumer", _CONSUMERS)
async def test_retrieval_rejection_skips_recorder_and_final_projection(
    consumer: Consumer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retrieval failure cannot mint an accepted run or terminal output."""
    harness = _build_preset_plan_harness(
        monkeypatch,
        failure_boundary="retrieval",
    )
    output_dir = str(tmp_path / consumer / "run" / "children" / "part-001")
    invoke = _build_consumer(consumer, harness.agent, output_dir, monkeypatch)
    projection = Mock(wraps=format_tool_result)

    with request_context("anonymous", f"request-{consumer}-failure"):
        with pytest.raises(McpError, match=_SAFE_ERRORS["retrieval"]):
            await _record_and_format_consumer(invoke, projection)
        _assert_no_recorded_terminal_projection(
            tmp_path / "tasks.sqlite", projection
        )

    assert harness.events == ["tool_extract_chat", "tool_usage_retrieval"]
    harness.upload_submit_meta.assert_not_awaited()
    harness.post_submit_job.assert_not_awaited()
    _assert_no_dispatch_side_effects(tmp_path / "tasks.sqlite", output_dir)


@pytest.mark.parametrize("consumer", _CONSUMERS)
async def test_retrieval_cancellation_skips_recorder_and_final_projection(
    consumer: Consumer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation cannot mint an accepted run or terminal output."""
    harness = _build_preset_plan_harness(
        monkeypatch,
        failure_boundary="retrieval",
        cancellation=True,
    )
    output_dir = str(tmp_path / consumer / "run" / "children" / "part-001")
    invoke = _build_consumer(consumer, harness.agent, output_dir, monkeypatch)
    projection = Mock(wraps=format_tool_result)

    with request_context("anonymous", f"request-{consumer}-cancel"):
        with pytest.raises(GRAPH_CANCELLATION):
            await _record_and_format_consumer(invoke, projection)
        _assert_no_recorded_terminal_projection(
            tmp_path / "tasks.sqlite", projection
        )

    assert harness.events == ["tool_extract_chat", "tool_usage_retrieval"]
    harness.upload_submit_meta.assert_not_awaited()
    harness.post_submit_job.assert_not_awaited()
    _assert_no_dispatch_side_effects(tmp_path / "tasks.sqlite", output_dir)


@pytest.mark.parametrize("consumer", _CONSUMERS)
async def test_partial_retrieval_records_once_before_final_projection(
    consumer: Consumer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Partial evidence records one accepted submission before formatting."""
    harness = _build_preset_plan_harness(monkeypatch)
    output_dir = str(tmp_path / consumer / "run" / "children" / "part-001")
    invoke = _build_consumer(consumer, harness.agent, output_dir, monkeypatch)
    projection = Mock(wraps=format_tool_result)

    with request_context("anonymous", f"request-{consumer}-partial"):
        formatted = await _record_and_format_consumer(invoke, projection)
        accepted_task_ids = current_accepted_task_ids()

    assert harness.events == [
        "tool_extract_chat",
        "tool_usage_retrieval",
        "upload_boundary",
        "remote_submit",
    ]
    harness.upload_submit_meta.assert_awaited_once()
    harness.post_submit_job.assert_awaited_once()
    assert accepted_task_ids == ("accepted-preset-task",)
    projection.assert_called_once()
    assert "No Data" not in formatted.answer
    runs = RunRegistry(str(tmp_path / "tasks.sqlite")).list_runs(
        owner="anonymous"
    )
    assert len(runs) == 1
    assert runs[0].status == "running"
    assert runs[0].task_ids == ("accepted-preset-task",)
