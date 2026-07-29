# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline contract tests for the agent-routing evaluation CLI."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from scripts import evaluate_agent_routing as cli
from scripts.agent_routing_eval.dataset import AgentRoutingCase
from scripts.agent_routing_eval.reporting import GitState
from scripts.agent_routing_eval.runner import RunOutcome

pytestmark = pytest.mark.unit


def _case() -> AgentRoutingCase:
    """Return one valid authored Chat case for CLI orchestration tests."""
    return AgentRoutingCase.model_validate(
        {
            "case_id": "dev-chat-001",
            "question": "Explain photosynthesis in one sentence.",
            "expected_agent": "ChatAgent",
            "expected_core_args": {},
            "language": "en",
            "source": {
                "kind": "authored_chat",
                "category": "general_knowledge",
                "rationale": "CLI test fixture.",
            },
            "transformation": {"kind": "authored_chat"},
        }
    )


def _outcome(
    case: AgentRoutingCase,
    repeat_index: int,
    *,
    correct: bool = True,
) -> RunOutcome:
    """Return one complete provider outcome for the fixture case."""
    predicted = case.expected_agent if correct else "KnowledgeAgent"
    return RunOutcome(
        case_id=case.case_id,
        repeat_index=repeat_index,
        expected_agent=case.expected_agent,
        predicted_agent=predicted,
        language=case.language,
        agent_correct=correct,
        schema_valid=True,
        dispatchable=correct,
        core_args_correct=None,
        provider_completed=True,
        attempts=1,
        latency_ms=10.0,
        selected_arguments={},
        error_code=None,
        validation_codes=(),
    )


def _config() -> SimpleNamespace:
    """Return non-secret provider metadata accepted by the CLI."""
    return SimpleNamespace(
        MODEL_ID="test-model",
        BASE_URL="https://provider.invalid/v1",
    )


def _patch_cases(monkeypatch: pytest.MonkeyPatch) -> AgentRoutingCase:
    """Replace the locked corpus with one deterministic offline case."""
    case = _case()
    monkeypatch.setattr(cli, "_load_cases", lambda _path: (case,))
    return case


def test_quick_defaults_to_dev_and_one_repeat() -> None:
    """Quick mode selects the development split and one repeat."""
    options = cli.parse_args(["--mode", "quick"])

    assert options.dataset.name == "dev_v1.jsonl"
    assert options.repeat_count == 1
    assert options.concurrency == 5


def test_benchmark_defaults_to_test_and_three_repeats() -> None:
    """Benchmark mode selects the test split and three repeats."""
    options = cli.parse_args(["--mode", "benchmark"])

    assert options.dataset.name == "test_v1.jsonl"
    assert options.repeat_count == 3


@pytest.mark.parametrize("concurrency", ["0", "33"])
def test_parse_args_rejects_concurrency_outside_closed_range(
    concurrency: str,
) -> None:
    """The CLI rejects concurrency outside the one-to-32 contract."""
    with pytest.raises(SystemExit):
        cli.parse_args(["--mode", "quick", "--concurrency", concurrency])


def test_parse_args_rejects_quick_threshold_enforcement() -> None:
    """Threshold enforcement is available only for benchmark mode."""
    with pytest.raises(SystemExit):
        cli.parse_args(["--mode", "quick", "--enforce-thresholds"])


def test_benchmark_accepts_development_dataset_override() -> None:
    """Benchmark mode can repeat the reviewed development split."""
    options = cli.parse_args(
        [
            "--mode",
            "benchmark",
            "--dataset",
            str(cli.DATASET_ROOT / "dev_v1.jsonl"),
        ]
    )

    assert options.dataset.name == "dev_v1.jsonl"
    assert options.repeat_count == 3


def test_dirty_benchmark_refuses_provider_calls(
    tmp_path: Path,
) -> None:
    """A dirty benchmark exits before loading data or calling a selector."""
    calls = 0

    async def evaluator(*_args: Any, **_kwargs: Any) -> tuple[RunOutcome, ...]:
        nonlocal calls
        calls += 1
        return ()

    result = cli.run_cli(
        ["--mode", "benchmark", "--output-dir", str(tmp_path)],
        git_state=GitState(branch="release", head="a" * 40, dirty=True),
        config_loader=_config,
        evaluator=evaluator,
    )

    assert result == 2
    assert calls == 0


def test_dirty_quick_run_writes_diagnostic_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Quick mode records dirty provenance while still completing."""
    case = _patch_cases(monkeypatch)

    async def evaluator(
        cases: tuple[AgentRoutingCase, ...], **_kwargs: Any
    ) -> tuple[RunOutcome, ...]:
        return (_outcome(cases[0], 1),)

    result = cli.run_cli(
        ["--mode", "quick", "--output-dir", str(tmp_path)],
        git_state=GitState(branch="release", head="b" * 40, dirty=True),
        config_loader=_config,
        evaluator=evaluator,
    )
    captured = capsys.readouterr()

    report_path = next(tmp_path.glob("*.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert result == 0
    assert report["status"]["headline"] == "Diagnostic (dirty tree)"
    assert report["provenance"]["dirty"] is True
    assert case.question not in captured.out
    assert "provider.invalid" not in captured.out


def test_enforced_threshold_failure_returns_one(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A completed inaccurate run fails when thresholds are enforced."""
    _patch_cases(monkeypatch)

    async def evaluator(
        cases: tuple[AgentRoutingCase, ...], **_kwargs: Any
    ) -> tuple[RunOutcome, ...]:
        return tuple(
            _outcome(cases[0], index, correct=False) for index in (1, 2, 3)
        )

    result = cli.run_cli(
        [
            "--mode",
            "benchmark",
            "--enforce-thresholds",
            "--output-dir",
            str(tmp_path),
        ],
        git_state=GitState(branch="release", head="c" * 40, dirty=False),
        config_loader=_config,
        evaluator=evaluator,
    )

    assert result == 1


def test_incomplete_run_writes_partial_report_and_returns_three(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cancellation persists a bounded partial report and returns exit 3."""
    _patch_cases(monkeypatch)

    async def evaluator(*_args: Any, **_kwargs: Any) -> tuple[RunOutcome, ...]:
        raise asyncio.CancelledError

    result = cli.run_cli(
        ["--mode", "quick", "--output-dir", str(tmp_path)],
        git_state=GitState(branch="release", head="d" * 40, dirty=False),
        config_loader=_config,
        evaluator=evaluator,
    )

    partial = next(tmp_path.glob("*-partial.json"))
    report = json.loads(partial.read_text(encoding="utf-8"))
    assert result == 3
    assert report["status"]["state"] == "incomplete"
    assert report["metrics"]["completed_records"] == 0


def test_invalid_configuration_returns_two_without_secret_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Configuration validation returns a fixed safe error message."""
    _patch_cases(monkeypatch)

    def invalid_config() -> SimpleNamespace:
        return SimpleNamespace(MODEL_ID="", BASE_URL="https://secret.invalid")

    result = cli.run_cli(
        ["--mode", "quick", "--output-dir", str(tmp_path)],
        git_state=GitState(branch="release", head="e" * 40, dirty=False),
        config_loader=invalid_config,
    )
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert (
        captured.err.strip()
        == "Evaluation configuration or dataset validation failed."
    )
    assert "secret.invalid" not in captured.err
