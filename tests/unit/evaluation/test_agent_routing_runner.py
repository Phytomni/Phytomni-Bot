# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Focused tests for the selector-only routing evaluation runner."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from scripts.agent_routing_eval.dataset import AgentRoutingCase
from scripts.agent_routing_eval.runner import (
    PROVIDER_ERROR,
    ROUTING_ERROR,
    EvaluationIncompleteError,
    RunOutcome,
    RunnerOptions,
    run_evaluation,
)
from mcp_server_phytomni.agents.expert.router import (
    ExpertProviderError,
    ExpertProviderTimeoutError,
    ToolSelection,
    ToolSelectionError,
)
from mcp_server_phytomni.mcp.schemas import AGENT_TOOL_DEFINITIONS
from mcp_server_phytomni.runtime.locale import (
    bind_effective_locale,
    current_effective_locale,
)
from mcp_server_phytomni.runtime.request_context import reset_request_var

pytestmark = pytest.mark.unit


class RecordingSelector:
    """Typed fake selector that records the runner's complete call shape."""

    def __init__(
        self,
        results: list[ToolSelection | BaseException],
    ) -> None:
        self.results = list(results)
        self.calls: list[dict[str, object]] = []

    async def __call__(
        self,
        user_query: str,
        history: Sequence[Mapping[str, object]] = (),
        *,
        allowed_tools: Sequence[str] | None = None,
        forced_tool: str | None = None,
    ) -> ToolSelection | None:
        self.calls.append(
            {
                "user_query": user_query,
                "history": tuple(history),
                "allowed_tools": tuple(allowed_tools or ()),
                "forced_tool": forced_tool,
                "locale": current_effective_locale(),
            }
        )
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _case(
    case_id: str = "case-001",
    *,
    question: str = "What is plant height?",
    expected_agent: str = "ChatAgent",
    language: str = "en",
    expected_core_args: dict[str, Any] | None = None,
) -> AgentRoutingCase:
    if expected_core_args is None:
        expected_core_args = {"user_query": question}
    return AgentRoutingCase.model_validate(
        {
            "case_id": case_id,
            "question": question,
            "expected_agent": expected_agent,
            "expected_core_args": expected_core_args,
            "language": language,
            "source": {
                "kind": "authored_chat",
                "category": "general_knowledge",
                "rationale": "Synthetic selector contract fixture.",
            },
            "transformation": {"kind": "authored_chat"},
        }
    )


def _chat_selection(query: str = "What is plant height?") -> ToolSelection:
    return ToolSelection(
        "ChatAgent",
        {"user_query": query, "obs_file_list": []},
    )


def _data_selection(query: str = "count records") -> ToolSelection:
    return ToolSelection("DataAgent", {"user_query": query})


def test_selector_receives_canonical_surface_without_context() -> None:
    """The runner offers all ten tools with no history or forced choice."""
    selector = RecordingSelector([_chat_selection()])

    outcomes = asyncio.run(run_evaluation([_case()], selector))

    assert len(outcomes) == 1
    assert selector.calls == [
        {
            "user_query": "What is plant height?",
            "history": (),
            "allowed_tools": tuple(
                name.value
                for name, _description, _model in AGENT_TOOL_DEFINITIONS
            ),
            "forced_tool": None,
            "locale": "en-US",
        }
    ]


def test_valid_selection_is_schema_valid_and_dispatchable() -> None:
    """A valid top-1 choice and core arguments produce success."""
    selector = RecordingSelector([_chat_selection(" What is plant height? ")])

    outcome = asyncio.run(run_evaluation([_case()], selector))[0]

    assert outcome.agent_correct is True
    assert outcome.schema_valid is True
    assert outcome.dispatchable is True
    assert outcome.core_args_correct is True
    assert outcome.provider_completed is True
    assert outcome.error_code is None


@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        (
            _data_selection(),
            {
                "agent_correct": False,
                "schema_valid": True,
                "dispatchable": False,
            },
        ),
        (
            ToolSelection("ChatAgent", {"obs_file_list": []}),
            {
                "agent_correct": True,
                "schema_valid": False,
                "dispatchable": False,
                "core_args_correct": None,
                "error_code": "schema_validation_error",
            },
        ),
        (
            _chat_selection("a different question"),
            {
                "agent_correct": True,
                "schema_valid": True,
                "dispatchable": True,
                "core_args_correct": False,
                "error_code": "core_argument_mismatch",
            },
        ),
    ],
)
def test_selection_outcome_distinguishes_top1_schema_and_core(
    selection: ToolSelection,
    expected: dict[str, object],
) -> None:
    """Wrong agents, invalid schemas, and core mismatches stay distinct."""
    selector = RecordingSelector([selection])

    outcome = asyncio.run(run_evaluation([_case()], selector))[0]

    for field, value in expected.items():
        assert getattr(outcome, field) == value


@pytest.mark.parametrize(
    ("result", "error_code"),
    [
        (None, "routing_missing_selection"),
        (ToolSelectionError("bad contract"), "routing_contract_error"),
    ],
)
def test_routing_contract_failures_are_bounded(
    result: ToolSelection | BaseException | None,
    error_code: str,
) -> None:
    """Strict missing and invalid selections do not retry or dispatch."""
    selector = RecordingSelector(
        cast(list[ToolSelection | BaseException], [result])
    )

    outcome = asyncio.run(run_evaluation([_case()], selector))[0]

    assert outcome.predicted_agent == ROUTING_ERROR
    assert outcome.error_code == error_code
    assert outcome.attempts == 1
    assert outcome.provider_completed is True


@pytest.mark.parametrize(
    ("failure", "error_code"),
    [
        (ExpertProviderTimeoutError(), "provider_timeout_exhausted"),
        (ExpertProviderError(), "provider_failure_exhausted"),
    ],
)
def test_provider_failures_retry_exactly_three_attempts(
    failure: BaseException,
    error_code: str,
) -> None:
    """Provider errors exhaust the fixed three-attempt budget."""
    selector = RecordingSelector([failure, failure, failure])
    options = RunnerOptions(repeat_count=1, retry_delay_seconds=0)

    outcome = asyncio.run(run_evaluation([_case()], selector, options))[0]

    assert len(selector.calls) == 3
    assert outcome.predicted_agent == PROVIDER_ERROR
    assert outcome.error_code == error_code
    assert outcome.attempts == 3
    assert outcome.provider_completed is False


def test_hanging_selector_times_out_and_retries_three_times() -> None:
    """The per-attempt asyncio timeout exhausts exactly three attempts."""
    attempts = 0

    async def hanging_selector(
        user_query: str,
        history: Sequence[Mapping[str, object]] = (),
        *,
        allowed_tools: Sequence[str] | None = None,
        forced_tool: str | None = None,
    ) -> ToolSelection:
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(3600)
        return _chat_selection(user_query)

    outcome = asyncio.run(
        run_evaluation(
            [_case()],
            hanging_selector,
            RunnerOptions(
                repeat_count=1,
                timeout_seconds=0.001,
                retry_delay_seconds=0,
            ),
        )
    )[0]

    assert attempts == 3
    assert outcome.attempts == 3
    assert outcome.predicted_agent == PROVIDER_ERROR
    assert outcome.error_code == "provider_timeout_exhausted"
    assert outcome.provider_completed is False


def test_success_on_second_attempt_records_retry_count() -> None:
    """A recovered provider call reports both attempts."""
    selector = RecordingSelector(
        [ExpertProviderError(), _chat_selection()]
    )
    options = RunnerOptions(repeat_count=1, retry_delay_seconds=0)

    outcome = asyncio.run(run_evaluation([_case()], selector, options))[0]

    assert len(selector.calls) == 2
    assert outcome.attempts == 2
    assert outcome.provider_completed is True
    assert outcome.dispatchable is True


def test_unexpected_exception_aborts_evaluation() -> None:
    """Unexpected selector exceptions do not become a synthetic success."""
    selector = RecordingSelector([ValueError("unexpected")])

    with pytest.raises(EvaluationIncompleteError) as exc_info:
        asyncio.run(run_evaluation([_case()], selector))

    assert isinstance(exc_info.value.__cause__, ValueError)


def test_outputs_are_sorted_by_case_and_repeat() -> None:
    """Completion order is independent from the stable report order."""
    release = asyncio.Event()

    async def selector(
        user_query: str,
        history: Sequence[Mapping[str, object]] = (),
        *,
        allowed_tools: Sequence[str] | None = None,
        forced_tool: str | None = None,
    ) -> ToolSelection:
        if user_query == "slow":
            await release.wait()
        return _chat_selection(user_query)

    async def execute() -> tuple[RunOutcome, ...]:
        task = asyncio.create_task(
            run_evaluation(
                [
                    _case("case-b", question="slow"),
                    _case("case-a", question="fast"),
                ],
                selector,
                RunnerOptions(repeat_count=3, retry_delay_seconds=0),
            )
        )
        await asyncio.sleep(0)
        release.set()
        return await task

    outcomes = asyncio.run(execute())

    assert [(item.case_id, item.repeat_index) for item in outcomes] == [
        ("case-a", 1),
        ("case-a", 2),
        ("case-a", 3),
        ("case-b", 1),
        ("case-b", 2),
        ("case-b", 3),
    ]


def test_locale_isolated_for_concurrent_cases_and_restored() -> None:
    """Concurrent selectors observe their own language locale."""
    release = asyncio.Event()
    observed: dict[str, str] = {}

    async def selector(
        user_query: str,
        history: Sequence[Mapping[str, object]] = (),
        *,
        allowed_tools: Sequence[str] | None = None,
        forced_tool: str | None = None,
    ) -> ToolSelection:
        await release.wait()
        observed[user_query] = current_effective_locale()
        return _chat_selection(user_query)

    async def execute() -> tuple[RunOutcome, ...]:
        task = asyncio.create_task(
            run_evaluation(
                [
                    _case("case-en", question="english", language="en"),
                    _case("case-zh", question="中文", language="zh"),
                ],
                selector,
            )
        )
        await asyncio.sleep(0)
        release.set()
        return await task

    prior = current_effective_locale()
    outer_token = bind_effective_locale("zh-CN")
    try:
        asyncio.run(execute())
    finally:
        reset_request_var(outer_token)

    assert observed == {"english": "en-US", "中文": "zh-CN"}
    assert current_effective_locale() == prior


def test_cancellation_cleans_children_and_sinks_partial_results() -> None:
    """Cancellation cancels children, sinks once, and remains cancellable."""
    started = asyncio.Event()
    started_count = 0
    cancelled_count = 0
    partials: list[tuple[RunOutcome, ...]] = []
    blocked = asyncio.Event()

    async def selector(
        user_query: str,
        history: Sequence[Mapping[str, object]] = (),
        *,
        allowed_tools: Sequence[str] | None = None,
        forced_tool: str | None = None,
    ) -> ToolSelection:
        nonlocal cancelled_count, started_count
        started_count += 1
        if started_count == 2:
            started.set()
        try:
            await blocked.wait()
        except asyncio.CancelledError:
            cancelled_count += 1
            raise
        return _chat_selection(user_query)

    async def sink(outcomes: tuple[RunOutcome, ...]) -> None:
        partials.append(outcomes)

    async def execute() -> None:
        task = asyncio.create_task(
            run_evaluation(
                [_case("case-a", question="a"), _case("case-b", question="b")],
                selector,
                RunnerOptions(repeat_count=1, concurrency=2),
                partial_sink=sink,
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(execute())

    assert cancelled_count == 2
    assert len(partials) == 1
    assert partials[0] == ()


def test_cancellation_sinks_completed_outcomes() -> None:
    """A completed child is included even when cancellation wins the race."""
    blocked = asyncio.Event()
    block_started = asyncio.Event()
    partials: list[tuple[RunOutcome, ...]] = []

    async def selector(
        user_query: str,
        history: Sequence[Mapping[str, object]] = (),
        *,
        allowed_tools: Sequence[str] | None = None,
        forced_tool: str | None = None,
    ) -> ToolSelection:
        if user_query == "blocked":
            block_started.set()
            await blocked.wait()
        return _chat_selection(user_query)

    async def sink(outcomes: tuple[RunOutcome, ...]) -> None:
        partials.append(outcomes)

    async def execute() -> None:
        task = asyncio.create_task(
            run_evaluation(
                [
                    _case("case-done", question="done"),
                    _case("case-blocked", question="blocked"),
                ],
                selector,
                RunnerOptions(repeat_count=1, concurrency=2),
                partial_sink=sink,
            )
        )
        await block_started.wait()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(execute())

    assert len(partials) == 1
    assert [item.case_id for item in partials[0]] == ["case-done"]


def test_failing_partial_sink_does_not_replace_cancellation() -> None:
    """A sink failure cannot replace the caller's cancellation."""
    started = asyncio.Event()
    blocked = asyncio.Event()

    async def selector(
        user_query: str,
        history: Sequence[Mapping[str, object]] = (),
        *,
        allowed_tools: Sequence[str] | None = None,
        forced_tool: str | None = None,
    ) -> ToolSelection:
        started.set()
        await blocked.wait()
        return _chat_selection(user_query)

    async def failing_sink(_outcomes: tuple[RunOutcome, ...]) -> None:
        raise RuntimeError("sink failed during cancellation")

    async def execute() -> None:
        task = asyncio.create_task(
            run_evaluation(
                [_case()],
                selector,
                RunnerOptions(repeat_count=1),
                partial_sink=failing_sink,
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(execute())


def test_failing_partial_sink_does_not_replace_unexpected_failure() -> None:
    """A sink failure cannot replace the original unexpected exception."""
    selector = RecordingSelector([ValueError("selector failed")])

    async def failing_sink(_outcomes: tuple[RunOutcome, ...]) -> None:
        raise RuntimeError("sink failed during abort")

    with pytest.raises(EvaluationIncompleteError) as exc_info:
        asyncio.run(
            run_evaluation(
                [_case()], selector, partial_sink=failing_sink
            )
        )

    assert isinstance(exc_info.value.__cause__, ValueError)
    assert str(exc_info.value.__cause__) == "selector failed"


def test_selector_concurrency_never_exceeds_configured_ceiling() -> None:
    """The semaphore covers every case repetition and logical run."""
    active = 0
    max_active = 0

    async def selector(
        user_query: str,
        history: Sequence[Mapping[str, object]] = (),
        *,
        allowed_tools: Sequence[str] | None = None,
        forced_tool: str | None = None,
    ) -> ToolSelection:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(0.001)
            return _chat_selection(user_query)
        finally:
            active -= 1

    outcomes = asyncio.run(
        run_evaluation(
            [
                _case("case-a", question="a"),
                _case("case-b", question="b"),
                _case("case-c", question="c"),
                _case("case-d", question="d"),
            ],
            selector,
            RunnerOptions(
                repeat_count=3,
                concurrency=2,
                retry_delay_seconds=0,
            ),
        )
    )

    assert len(outcomes) == 12
    assert max_active <= 2
    assert max_active > 1


def test_runner_cannot_reach_dispatch_seams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replacing MCP dispatch functions cannot affect selector evaluation."""
    from mcp_server_phytomni.mcp import app

    def fail_dispatch(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("dispatch seam reached")

    monkeypatch.setattr(app, "invoke_tool_raw", fail_dispatch)
    monkeypatch.setattr(app, "invoke_tool_enveloped", fail_dispatch)
    monkeypatch.setattr(app, "dispatch_tool", fail_dispatch)

    outcome = asyncio.run(
        run_evaluation([_case()], RecordingSelector([_chat_selection()]))
    )[0]

    assert outcome.dispatchable is True
    source = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "agent_routing_eval"
        / "runner.py"
    )
    text = source.read_text(encoding="utf-8")
    assert "mcp_server_phytomni.mcp.app" not in text
    assert "mcp_server_phytomni.api" not in text


@pytest.mark.parametrize(
    "kwargs",
    [
        {"repeat_count": 2},
        {"repeat_count": 1, "concurrency": 0},
        {"repeat_count": 1, "concurrency": 33},
        {"repeat_count": 1, "timeout_seconds": 0},
        {"repeat_count": 1, "max_attempts": 2},
        {"repeat_count": 1, "retry_delay_seconds": -1},
    ],
)
def test_runner_options_reject_out_of_contract_values(
    kwargs: dict[str, object],
) -> None:
    """Runner bounds are fixed to the evaluation protocol."""
    with pytest.raises(ValueError):
        RunnerOptions(**kwargs)  # type: ignore[arg-type]


def test_runner_module_has_no_api_or_dispatch_imports() -> None:
    """The implementation has no import path into execution surfaces."""
    import scripts.agent_routing_eval.runner as runner

    source = inspect.getsource(runner)
    assert "mcp_server_phytomni.api" not in source
    assert "mcp_server_phytomni.mcp.app" not in source
