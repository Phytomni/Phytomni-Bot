# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Run the selector-only agent-routing evaluation from one command."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ValidationError

from mcp_server_phytomni.config.settings import get_sensitive_config

if TYPE_CHECKING:
    from scripts.agent_routing_eval.dataset import (
        AgentRoutingCase,
        DatasetValidationError,
        load_dataset,
        validate_dataset,
        validate_dataset_pair,
    )
    from scripts.agent_routing_eval.metrics import (
        compute_metrics,
        thresholds_pass,
    )
    from scripts.agent_routing_eval.reporting import (
        GitState,
        ReportContext,
        build_report,
        collect_git_state,
        provider_endpoint_sha256,
        write_report_pair,
    )
    from scripts.agent_routing_eval.runner import (
        EvaluationIncompleteError,
        RunnerOptions,
        RunOutcome,
        Selector,
        run_evaluation,
    )
else:
    _MODULE_PREFIX = (
        "scripts.agent_routing_eval" if __package__ else "agent_routing_eval"
    )
    _dataset = import_module(f"{_MODULE_PREFIX}.dataset")
    _metrics = import_module(f"{_MODULE_PREFIX}.metrics")
    _reporting = import_module(f"{_MODULE_PREFIX}.reporting")
    _runner = import_module(f"{_MODULE_PREFIX}.runner")
    AgentRoutingCase = _dataset.AgentRoutingCase
    DatasetValidationError = _dataset.DatasetValidationError
    load_dataset = _dataset.load_dataset
    validate_dataset = _dataset.validate_dataset
    validate_dataset_pair = _dataset.validate_dataset_pair
    compute_metrics = _metrics.compute_metrics
    thresholds_pass = _metrics.thresholds_pass
    GitState = _reporting.GitState
    ReportContext = _reporting.ReportContext
    build_report = _reporting.build_report
    collect_git_state = _reporting.collect_git_state
    provider_endpoint_sha256 = _reporting.provider_endpoint_sha256
    write_report_pair = _reporting.write_report_pair
    EvaluationIncompleteError = _runner.EvaluationIncompleteError
    RunnerOptions = _runner.RunnerOptions
    RunOutcome = _runner.RunOutcome
    Selector = _runner.Selector
    run_evaluation = _runner.run_evaluation

ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = ROOT / "evaluation" / "agent_routing" / "datasets"
DEFAULT_OUTPUT = ROOT / "evaluation" / "agent_routing" / "results"

_DEFAULT_DATASETS = {
    "dev": DATASET_ROOT / "dev_v1.jsonl",
    "test": DATASET_ROOT / "test_v1.jsonl",
}
_CONFIG_ERROR = "Evaluation configuration or dataset validation failed."
_INCOMPLETE_ERROR = "Evaluation incomplete; a partial report was written."
_THRESHOLD_ERROR = "Routing benchmark thresholds failed."
_DIRTY_ERROR = (
    "Benchmark requires a clean tree; use --allow-dirty for diagnostics."
)


@dataclass(frozen=True, slots=True)
class CliOptions:
    """Validated command-line options."""

    mode: Literal["quick", "benchmark"]
    dataset: Path
    repeat_count: Literal[1, 3]
    concurrency: int
    output_dir: Path
    allow_dirty: bool
    enforce_thresholds: bool


@dataclass(frozen=True, slots=True)
class _RunInputs:
    """Validated inputs shared by one CLI evaluation execution."""

    options: CliOptions
    cases: tuple[AgentRoutingCase, ...]
    git_state: GitState
    model_id: str
    endpoint_hash: str
    selector: Selector | None
    evaluator: Callable[..., Any]


def _dataset_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_file():
        raise argparse.ArgumentTypeError("dataset file does not exist")
    return path.resolve()


def _concurrency(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("invalid concurrency") from exc
    if not 1 <= parsed <= 32:
        raise argparse.ArgumentTypeError(
            "concurrency must be between 1 and 32"
        )
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> CliOptions:
    """Parse and resolve the small, explicit operator interface."""
    parser = argparse.ArgumentParser(
        description="Run selector-only agent-routing evaluation."
    )
    parser.add_argument(
        "--mode", choices=("quick", "benchmark"), required=True
    )
    parser.add_argument("--dataset", type=_dataset_path)
    parser.add_argument("--concurrency", type=_concurrency, default=5)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--enforce-thresholds", action="store_true")
    namespace = parser.parse_args(argv)
    mode = namespace.mode
    if mode == "quick" and namespace.enforce_thresholds:
        parser.error(
            "--enforce-thresholds is only supported in benchmark mode"
        )
    default_path = _DEFAULT_DATASETS["dev" if mode == "quick" else "test"]
    dataset = (
        namespace.dataset.resolve()
        if namespace.dataset is not None
        else default_path.resolve()
    )
    repeat_count: Literal[1, 3] = 1 if mode == "quick" else 3
    return CliOptions(
        mode=mode,
        dataset=dataset,
        repeat_count=repeat_count,
        concurrency=namespace.concurrency,
        output_dir=namespace.output_dir.expanduser().resolve(),
        allow_dirty=namespace.allow_dirty,
        enforce_thresholds=namespace.enforce_thresholds,
    )


def _is_default(path: Path) -> bool:
    resolved = path.resolve()
    return resolved in {
        candidate.resolve() for candidate in _DEFAULT_DATASETS.values()
    }


def _load_cases(path: Path) -> tuple[AgentRoutingCase, ...]:
    """Load one split and validate the pair whenever a default is selected."""
    if _is_default(path):
        dev = load_dataset(_DEFAULT_DATASETS["dev"])
        test = load_dataset(_DEFAULT_DATASETS["test"])
        validate_dataset_pair(dev, test)
        return (
            dev
            if path.resolve() == _DEFAULT_DATASETS["dev"].resolve()
            else test
        )
    cases = load_dataset(path)
    if len(cases) == 50:
        validate_dataset(cases, "dev")
    elif len(cases) == 100:
        validate_dataset(cases, "test")
    else:
        raise DatasetValidationError("custom dataset has an unsupported size")
    return cases


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _safe_float(value: object) -> str:
    if value == "Unknown" or value is None:
        return "Unknown"
    if isinstance(value, (int, float)):
        return f"{value:.4f}"
    return "Unknown"


def _print_artifacts(paths: tuple[Path, Path], report: dict[str, Any]) -> None:
    status = report["status"]
    print(f"JSON: {paths[0]}")
    print(f"Markdown: {paths[1]}")
    print(f"Headline: {status['headline']}")
    print(f"Current accuracy: {_safe_float(status['current_accuracy'])}")


def _provider_metadata(config_loader: Callable[[], Any]) -> tuple[str, str]:
    """Resolve only the non-secret provider metadata needed by reports."""
    sensitive = config_loader()
    model_id = getattr(sensitive, "MODEL_ID", None)
    base_url = getattr(sensitive, "BASE_URL", None)
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("missing model configuration")
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("missing endpoint configuration")
    return model_id, provider_endpoint_sha256(base_url)


def _execute(inputs: _RunInputs) -> int:
    """Execute one already-validated CLI evaluation and publish its report."""
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    stem = f"{inputs.options.mode}-{_timestamp()}"
    partial_paths: list[Path] = []

    def make_context(elapsed: float) -> ReportContext:
        return ReportContext(
            mode=inputs.options.mode,
            dataset_path=inputs.options.dataset,
            repeat_count=inputs.options.repeat_count,
            concurrency=inputs.options.concurrency,
            model_id=inputs.model_id,
            provider_endpoint_hash=inputs.endpoint_hash,
            git=inputs.git_state,
            started_at=started_at,
            elapsed_seconds=elapsed,
            allow_dirty=inputs.options.allow_dirty,
        )

    def write_partial(outcomes: Sequence[RunOutcome]) -> None:
        report = build_report(
            make_context(time.perf_counter() - started),
            inputs.cases,
            outcomes,
            {
                "schema_version": 1,
                "status": "incomplete",
                "planned_runs": len(inputs.cases)
                * inputs.options.repeat_count,
                "completed_records": len(outcomes),
            },
            complete=False,
        )
        paths = write_report_pair(
            report, inputs.options.output_dir, f"{stem}-partial"
        )
        partial_paths[:] = list(paths)

    try:
        outcomes = asyncio.run(
            inputs.evaluator(
                inputs.cases,
                selector=inputs.selector,
                options=RunnerOptions(
                    repeat_count=inputs.options.repeat_count,
                    concurrency=inputs.options.concurrency,
                ),
                partial_sink=write_partial,
            )
        )
    except asyncio.CancelledError:
        if not partial_paths:
            write_partial(())
        print(_INCOMPLETE_ERROR, file=sys.stderr)
        return 3
    except EvaluationIncompleteError:
        if not partial_paths:
            write_partial(())
        print(_INCOMPLETE_ERROR, file=sys.stderr)
        return 3

    if not outcomes or not any(item.provider_completed for item in outcomes):
        if not partial_paths:
            write_partial(outcomes)
        print(_INCOMPLETE_ERROR, file=sys.stderr)
        return 3

    metrics = compute_metrics(
        inputs.cases, outcomes, inputs.options.repeat_count
    )
    report = build_report(
        make_context(time.perf_counter() - started),
        inputs.cases,
        outcomes,
        metrics,
        complete=True,
    )
    paths = write_report_pair(report, inputs.options.output_dir, stem)
    _print_artifacts(paths, report)
    if inputs.options.enforce_thresholds and not thresholds_pass(metrics):
        print(_THRESHOLD_ERROR, file=sys.stderr)
        return 1
    return 0


def _run(
    options: CliOptions,
    *,
    git_state: GitState,
    selector: Selector | None,
    config_loader: Callable[[], Any],
    evaluator: Callable[..., Any],
) -> int:
    """Validate execution inputs before starting provider work."""
    if (
        options.mode == "benchmark"
        and git_state.dirty
        and not options.allow_dirty
    ):
        print(_DIRTY_ERROR, file=sys.stderr)
        return 2

    cases = _load_cases(options.dataset)
    model_id, endpoint_hash = _provider_metadata(config_loader)
    return _execute(
        _RunInputs(
            options=options,
            cases=cases,
            git_state=git_state,
            model_id=model_id,
            endpoint_hash=endpoint_hash,
            selector=selector,
            evaluator=evaluator,
        )
    )


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    git_state: GitState | None = None,
    selector: Selector | None = None,
    config_loader: Callable[[], Any] = get_sensitive_config,
    evaluator: Callable[..., Any] = run_evaluation,
) -> int:
    """Run the CLI with injectable seams for offline tests."""
    try:
        options = parse_args(argv)
        state = git_state if git_state is not None else collect_git_state()
        return _run(
            options,
            git_state=state,
            selector=selector,
            config_loader=config_loader,
            evaluator=evaluator,
        )
    except (
        DatasetValidationError,
        ValidationError,
        ValueError,
        OSError,
        RuntimeError,
    ):
        print(_CONFIG_ERROR, file=sys.stderr)
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point."""
    return run_cli(argv)


if __name__ == "__main__":
    raise SystemExit(main())
