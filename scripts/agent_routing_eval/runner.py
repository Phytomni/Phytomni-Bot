# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Run selector-only agent-routing evaluations."""

from __future__ import annotations

import asyncio
import inspect
import math
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, Protocol

from pydantic import JsonValue, ValidationError

from mcp_server_phytomni.agents.expert.router import (
    ExpertProviderError,
    ExpertProviderTimeoutError,
    ExpertRoutingContractError,
    ToolSelection,
    ToolSelectionError,
    select_agent_tool,
)
from mcp_server_phytomni.mcp.schemas import AGENT_TOOL_DEFINITIONS
from mcp_server_phytomni.runtime.locale import bind_effective_locale
from mcp_server_phytomni.runtime.request_context import reset_request_var

from .dataset import AgentRoutingCase

PROVIDER_ERROR = "__PROVIDER_ERROR__"
ROUTING_ERROR = "__ROUTING_ERROR__"
LANGUAGE_LOCALE: Final[
    dict[Literal["en", "zh"], Literal["en-US", "zh-CN"]]
] = {"en": "en-US", "zh": "zh-CN"}

_ALLOWED_TOOLS = tuple(
    name.value for name, _description, _model in AGENT_TOOL_DEFINITIONS
)
_MODEL_BY_AGENT = {
    name.value: model for name, _description, model in AGENT_TOOL_DEFINITIONS
}


class Selector(Protocol):
    """Callable contract for the injectable routing selector."""

    def __call__(
        self,
        user_query: str,
        history: Sequence[Mapping[str, object]] = (),
        *,
        allowed_tools: Sequence[str] | None = None,
        forced_tool: str | None = None,
    ) -> Awaitable[ToolSelection | None]: ...


PartialSink = Callable[[tuple["RunOutcome", ...]], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class RunnerOptions:
    """Bounded execution settings for a routing evaluation."""

    repeat_count: int
    concurrency: int = 5
    timeout_seconds: float = 120.0
    max_attempts: int = 3
    retry_delay_seconds: float = 1.0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.repeat_count, int)
            or isinstance(self.repeat_count, bool)
            or self.repeat_count not in {
            1,
            3,
            }
        ):
            raise ValueError("repeat_count must be 1 or 3")
        if (
            not isinstance(self.concurrency, int)
            or isinstance(self.concurrency, bool)
            or not 1 <= self.concurrency <= 32
        ):
            raise ValueError("concurrency must be between 1 and 32")
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        if (
            not isinstance(self.max_attempts, int)
            or isinstance(self.max_attempts, bool)
            or self.max_attempts != 3
        ):
            raise ValueError("max_attempts must be 3")
        if (
            not isinstance(self.retry_delay_seconds, (int, float))
            or not math.isfinite(self.retry_delay_seconds)
            or self.retry_delay_seconds < 0
        ):
            raise ValueError(
                "retry_delay_seconds must be finite and nonnegative"
            )


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """Immutable result for one case repetition."""

    case_id: str
    repeat_index: int
    expected_agent: str
    predicted_agent: str
    language: str
    agent_correct: bool
    schema_valid: bool
    dispatchable: bool
    core_args_correct: bool | None
    provider_completed: bool
    attempts: int
    latency_ms: float
    selected_arguments: dict[str, JsonValue]
    error_code: str | None
    validation_codes: tuple[str, ...]


class EvaluationIncompleteError(RuntimeError):
    """Raised when evaluation orchestration cannot produce a full result."""


async def _call_partial_sink(
    partial_sink: PartialSink | None,
    outcomes: Sequence[RunOutcome],
) -> None:
    if partial_sink is None:
        return
    result = partial_sink(tuple(_sort_outcomes(outcomes)))
    if inspect.isawaitable(result):
        await result


async def _call_partial_sink_best_effort(
    partial_sink: PartialSink | None,
    outcomes: Sequence[RunOutcome],
) -> None:
    """Attempt partial persistence without replacing the primary failure."""
    try:
        await _call_partial_sink(partial_sink, outcomes)
    except (RuntimeError, ValueError, TypeError, OSError):
        # Cancellation and orchestration errors must retain their original
        # identity even when a best-effort persistence callback fails.
        return


def _sort_outcomes(outcomes: Sequence[RunOutcome]) -> list[RunOutcome]:
    return sorted(outcomes, key=lambda item: (item.case_id, item.repeat_index))


async def _cancel_tasks(
    tasks: Sequence[asyncio.Task[RunOutcome]],
) -> list[object]:
    for task in tasks:
        if not task.done():
            task.cancel()
    return list(await asyncio.gather(*tasks, return_exceptions=True))


def _record_completed(
    completed: dict[tuple[str, int], RunOutcome],
    results: Sequence[object],
) -> None:
    for result in results:
        if isinstance(result, RunOutcome):
            completed[(result.case_id, result.repeat_index)] = result


async def run_evaluation(
    cases: Sequence[AgentRoutingCase],
    selector: Selector = select_agent_tool,
    options: RunnerOptions | None = None,
    *,
    partial_sink: PartialSink | None = None,
) -> tuple[RunOutcome, ...]:
    """Evaluate routing selections concurrently without dispatching tools.

    Args:
        cases: Immutable dataset records to evaluate.
        selector: Injectable selector compatible with ``select_agent_tool``.
        options: Execution bounds. Defaults to one repetition at concurrency
            five.
        partial_sink: Optional callback used once if orchestration is
            cancelled or otherwise incomplete.

    Returns:
        Outcomes sorted by case ID and repeat index.

    Raises:
        EvaluationIncompleteError: If an unexpected task exception aborts the
            evaluation.
        asyncio.CancelledError: If the caller cancels the evaluation.
    """
    run_options = options or RunnerOptions(repeat_count=1)
    semaphore = asyncio.Semaphore(run_options.concurrency)
    tasks = [
        asyncio.create_task(
            _run_with_limit(
                case, repeat_index, selector, run_options, semaphore
            )
        )
        for case in cases
        for repeat_index in range(1, run_options.repeat_count + 1)
    ]
    completed: dict[tuple[str, int], RunOutcome] = {}
    try:
        for awaitable in asyncio.as_completed(tasks):
            result = await awaitable
            completed[(result.case_id, result.repeat_index)] = result
    except asyncio.CancelledError:
        results = await _cancel_tasks(tasks)
        _record_completed(completed, results)
        await _call_partial_sink_best_effort(
            partial_sink, tuple(completed.values())
        )
        raise
    except Exception as exc:
        results = await _cancel_tasks(tasks)
        _record_completed(completed, results)
        await _call_partial_sink_best_effort(
            partial_sink, tuple(completed.values())
        )
        raise EvaluationIncompleteError("evaluation did not complete") from exc
    return tuple(_sort_outcomes(tuple(completed.values())))


async def _run_with_limit(
    case: AgentRoutingCase,
    repeat_index: int,
    selector: Selector,
    options: RunnerOptions,
    semaphore: asyncio.Semaphore,
) -> RunOutcome:
    async with semaphore:
        return await _run_case(case, repeat_index, selector, options)


async def _run_case(
    case: AgentRoutingCase,
    repeat_index: int,
    selector: Selector,
    options: RunnerOptions,
) -> RunOutcome:
    started = time.perf_counter()
    attempts = 0
    provider_error: BaseException | None = None
    for attempts in range(1, options.max_attempts + 1):
        token = bind_effective_locale(LANGUAGE_LOCALE[case.language])
        try:
            try:
                async with asyncio.timeout(options.timeout_seconds):
                    selection = await selector(
                        case.question,
                        history=(),
                        allowed_tools=_ALLOWED_TOOLS,
                        forced_tool=None,
                    )
            except (ExpertProviderTimeoutError, TimeoutError) as exc:
                provider_error = exc
            except ExpertProviderError as exc:
                provider_error = exc
            except (ToolSelectionError, ExpertRoutingContractError):
                return _routing_outcome(
                    case,
                    repeat_index,
                    attempts,
                    started,
                    "routing_contract_error",
                )
            else:
                if selection is None:
                    return _routing_outcome(
                        case,
                        repeat_index,
                        attempts,
                        started,
                        "routing_missing_selection",
                    )
                return _selection_outcome(
                    case, repeat_index, attempts, started, selection
                )
        finally:
            reset_request_var(token)
        if attempts < options.max_attempts:
            await asyncio.sleep(options.retry_delay_seconds)

    assert provider_error is not None
    error_code = (
        "provider_timeout_exhausted"
        if isinstance(
            provider_error, (ExpertProviderTimeoutError, TimeoutError)
        )
        else "provider_failure_exhausted"
    )
    return RunOutcome(
        case_id=case.case_id,
        repeat_index=repeat_index,
        expected_agent=case.expected_agent,
        predicted_agent=PROVIDER_ERROR,
        language=case.language,
        agent_correct=False,
        schema_valid=False,
        dispatchable=False,
        core_args_correct=None,
        provider_completed=False,
        attempts=attempts,
        latency_ms=_latency_ms(started),
        selected_arguments={},
        error_code=error_code,
        validation_codes=(),
    )


def _routing_outcome(
    case: AgentRoutingCase,
    repeat_index: int,
    attempts: int,
    started: float,
    error_code: str,
) -> RunOutcome:
    return RunOutcome(
        case_id=case.case_id,
        repeat_index=repeat_index,
        expected_agent=case.expected_agent,
        predicted_agent=ROUTING_ERROR,
        language=case.language,
        agent_correct=False,
        schema_valid=False,
        dispatchable=False,
        core_args_correct=None,
        provider_completed=True,
        attempts=attempts,
        latency_ms=_latency_ms(started),
        selected_arguments={},
        error_code=error_code,
        validation_codes=(),
    )


def _selection_outcome(
    case: AgentRoutingCase,
    repeat_index: int,
    attempts: int,
    started: float,
    selection: ToolSelection,
) -> RunOutcome:
    agent_correct = selection.tool_name == case.expected_agent
    model = _MODEL_BY_AGENT.get(selection.tool_name)
    validation_codes: tuple[str, ...] = ()
    if model is None:
        schema_valid = False
        normalized_arguments: dict[str, JsonValue] = {}
        error_code = "routing_contract_error"
        validation_codes = ("unknown_agent",)
    else:
        try:
            validated = model.model_validate(selection.arguments)
        except ValidationError as exc:
            schema_valid = False
            normalized_arguments = {}
            validation_codes = tuple(
                sorted(
                    {
                        str(item["type"])
                        for item in exc.errors(include_url=False)
                    }
                )
            )
            error_code = "schema_validation_error"
        else:
            schema_valid = True
            normalized_arguments = validated.model_dump(mode="json")
            error_code = None
    dispatchable = agent_correct and schema_valid
    core_args_correct: bool | None = None
    if dispatchable and case.expected_core_args:
        core_args_correct = _core_arguments_match(
            case.expected_core_args, normalized_arguments
        )
        if not core_args_correct:
            error_code = "core_argument_mismatch"
    return RunOutcome(
        case_id=case.case_id,
        repeat_index=repeat_index,
        expected_agent=case.expected_agent,
        predicted_agent=selection.tool_name,
        language=case.language,
        agent_correct=agent_correct,
        schema_valid=schema_valid,
        dispatchable=dispatchable,
        core_args_correct=core_args_correct,
        provider_completed=True,
        attempts=attempts,
        latency_ms=_latency_ms(started),
        selected_arguments=normalized_arguments,
        error_code=error_code,
        validation_codes=validation_codes,
    )


def _core_arguments_match(
    expected: Mapping[str, JsonValue], selected: Mapping[str, JsonValue]
) -> bool:
    return all(
        _normalize_strings(selected.get(key)) == _normalize_strings(value)
        for key, value in expected.items()
    )


def _normalize_strings(value: JsonValue | None) -> JsonValue | None:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return [_normalize_strings(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _normalize_strings(item) for key, item in value.items()
        }
    return value


def _latency_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


__all__ = [
    "LANGUAGE_LOCALE",
    "PROVIDER_ERROR",
    "ROUTING_ERROR",
    "EvaluationIncompleteError",
    "RunOutcome",
    "RunnerOptions",
    "run_evaluation",
]
