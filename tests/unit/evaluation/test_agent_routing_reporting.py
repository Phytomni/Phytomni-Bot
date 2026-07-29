# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Focused tests for safe and reproducible routing reports."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pytest
from scripts.agent_routing_eval.dataset import AgentRoutingCase
from scripts.agent_routing_eval.reporting import (
    GitState,
    ReportContext,
    build_report,
    collect_git_state,
    dataset_sha256,
    description_sha256,
    provider_endpoint_sha256,
    write_report_pair,
)
from scripts.agent_routing_eval.runner import RunOutcome

from mcp_server_phytomni.mcp.schemas import AGENT_TOOL_DEFINITIONS

pytestmark = pytest.mark.unit


def _case(case_id: str = "case-a") -> AgentRoutingCase:
    return AgentRoutingCase.model_validate(
        {
            "case_id": case_id,
            "question": f"Question text for {case_id}",
            "expected_agent": "ChatAgent",
            "expected_core_args": {},
            "language": "en",
            "source": {
                "kind": "authored_chat",
                "category": "general_knowledge",
                "rationale": "Safe report fixture.",
            },
            "transformation": {"kind": "authored_chat"},
        }
    )


def _outcome(
    case_id: str,
    repeat_index: int = 1,
    *,
    selected_arguments: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> RunOutcome:
    return RunOutcome(
        case_id=case_id,
        repeat_index=repeat_index,
        expected_agent="ChatAgent",
        predicted_agent="ChatAgent",
        language="en",
        agent_correct=True,
        schema_valid=True,
        dispatchable=True,
        core_args_correct=True,
        provider_completed=True,
        attempts=1,
        latency_ms=12.5,
        selected_arguments=selected_arguments or {"user_query": case_id},
        error_code=error_code,
        validation_codes=("missing",),
    )


def _context(
    dataset_path: Path,
    *,
    mode: Literal["quick", "benchmark"] = "benchmark",
    repeat_count: int = 3,
    dirty: bool = False,
    provider_endpoint_hash: str = "endpoint-digest",
    model_id: str = "routing-model",
) -> ReportContext:
    return ReportContext(
        mode=mode,
        dataset_path=dataset_path,
        repeat_count=repeat_count,
        concurrency=5,
        model_id=model_id,
        provider_endpoint_hash=provider_endpoint_hash,
        git=GitState("release/0.1.4", "abc123", dirty),
        started_at=datetime(2026, 7, 29, 1, 2, 3, tzinfo=UTC),
        elapsed_seconds=1.25,
        allow_dirty=dirty,
    )


def _metrics(
    *,
    provider_completion: float = 1.0,
    top1_accuracy: float = 1.0,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "case_count": 1,
        "planned_runs": 3,
        "completed_records": 3,
        "run_level": {
            "top1_accuracy": top1_accuracy,
            "dispatchable_accuracy": top1_accuracy,
        },
        "majority": {
            "top1_correct": 1,
            "top1_accuracy": top1_accuracy,
            "dispatchable_correct": 1,
            "dispatchable_accuracy": top1_accuracy,
            "wilson_95": [0.1, 1.0],
        },
        "per_agent": {
            "ChatAgent": {
                "support": 1,
                "predicted": 1,
                "true_positive": 1,
                "precision": 1.0,
                "recall": 1.0,
                "f1": 1.0,
            }
        },
        "by_language": {
            "en": {
                "case_count": 1,
                "top1_correct": 1,
                "top1_accuracy": top1_accuracy,
                "dispatchable_correct": 1,
                "dispatchable_accuracy": top1_accuracy,
            }
        },
        "errors": {"provider": 0, "routing": 0, "schema": 0},
        "provider_completion": provider_completion,
        "latency_ms": {"p50": 12.5, "p95": 12.5},
        "confusion_matrix": {"ChatAgent": {"ChatAgent": 1}},
        "stability": {"exact": 1.0, "modal_agreement": 1.0},
    }


def test_collect_git_state_does_not_persist_changed_paths() -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(command)
        if "status" in command:
            return SimpleNamespace(returncode=0, stdout=" M secret.txt\n")
        if "--abbrev-ref" in command:
            return SimpleNamespace(returncode=0, stdout="release/0.1.4\n")
        return SimpleNamespace(returncode=0, stdout="abc123\n")

    state = collect_git_state(run_command=fake_run)

    assert state.dirty is True
    assert "secret.txt" not in repr(state)
    assert calls == [
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        ["git", "rev-parse", "HEAD"],
        ["git", "status", "--porcelain", "--untracked-files=no"],
    ]


def test_hashes_bind_exact_bytes_descriptions_and_not_endpoint_text(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_bytes(b"first\nsecond\n")
    assert (
        dataset_sha256(dataset)
        == hashlib.sha256(b"first\nsecond\n").hexdigest()
    )

    changed: list[tuple[object, object, object]] = list(AGENT_TOOL_DEFINITIONS)
    name, description, model = changed[0]
    changed[0] = (
        name,
        SimpleNamespace(value="changed description"),
        model,
    )
    assert description_sha256() != description_sha256(changed)

    endpoint = "https://provider.internal/v1"
    digest = provider_endpoint_sha256(endpoint)
    assert digest == hashlib.sha256(endpoint.encode()).hexdigest()
    assert endpoint not in digest
    assert "provider.internal" not in digest


def test_build_report_redacts_sensitive_values_and_sorts_runs(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "test_v1.jsonl"
    dataset.write_bytes(b"dataset\n")
    report = build_report(
        _context(
            dataset,
            provider_endpoint_hash="https://provider.internal/v1",
            model_id="api-key-should-not-appear",
        ),
        [_case("case-a"), _case("case-b")],
        [
            _outcome(
                "case-b",
                2,
                selected_arguments={
                    "user_query": "Question text for case-b",
                    "api_key": "api-key-should-not-appear",
                    "detail": "provider raw exception detail",
                },
                error_code="provider raw exception detail",
            ),
            _outcome("case-a", 1),
        ],
        _metrics(),
        complete=True,
    )
    json_path, markdown_path = write_report_pair(
        report, tmp_path / "out", "run"
    )
    json_text = json_path.read_text(encoding="utf-8")
    markdown_text = markdown_path.read_text(encoding="utf-8")

    for secret in (
        "api-key-should-not-appear",
        "https://provider.internal/v1",
        "provider raw exception detail",
    ):
        assert secret not in json_text
        assert secret not in markdown_text
    assert (
        report["provenance"]["provider_endpoint_hash"]
        == hashlib.sha256(b"https://provider.internal/v1").hexdigest()
    )
    assert [run["case_id"] for run in report["runs"]] == [
        "case-a",
        "case-b",
    ]
    assert "Question text for case-b" not in markdown_text
    assert markdown_text.startswith("# Agent Routing Evaluation Report\n")
    assert "- majority.top1_accuracy: 1" in markdown_text
    assert '"top1_accuracy": 1.0' in json_text


@pytest.mark.parametrize(
    ("dirty", "provider_completion", "expected_headline"),
    [
        (False, 0.98, "Unknown"),
        (True, 1.0, "Diagnostic (dirty tree)"),
    ],
)
def test_report_status_does_not_overclaim_current_accuracy(
    tmp_path: Path,
    dirty: bool,
    provider_completion: float,
    expected_headline: str,
) -> None:
    dataset = tmp_path / "test_v1.jsonl"
    dataset.write_bytes(b"dataset\n")
    report = build_report(
        _context(dataset, dirty=dirty),
        [_case()],
        [_outcome("case-a", index) for index in (1, 2, 3)],
        _metrics(provider_completion=provider_completion),
        complete=True,
    )

    status = report["status"]
    assert status["headline"] == expected_headline
    if provider_completion < 0.99:
        assert status["current_accuracy"] == "Unknown"


def test_only_clean_test_benchmark_can_claim_stable_baseline(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "test_v1.jsonl"
    dataset.write_bytes(b"dataset\n")
    report = build_report(
        _context(dataset),
        [_case()],
        [_outcome("case-a", index) for index in (1, 2, 3)],
        _metrics(),
        complete=True,
    )
    assert report["status"]["headline"] == "Stable baseline"

    quick_report = build_report(
        _context(dataset, mode="quick", repeat_count=1),
        [_case()],
        [_outcome("case-a")],
        _metrics(),
        complete=True,
    )
    assert quick_report["status"]["headline"] == "Diagnostic"


def test_incomplete_report_is_bounded_and_cannot_claim_thresholds(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dev_v1.jsonl"
    dataset.write_bytes(b"dataset\n")
    report = build_report(
        _context(dataset, mode="quick", repeat_count=1),
        [_case()],
        [_outcome("case-a")],
        {
            "planned_runs": 3,
            "completed_records": 1,
            "majority": {"top1_accuracy": 1.0},
            "raw_exception": "provider raw exception detail",
        },
        complete=False,
    )

    assert report["metrics"] == {
        "planned_runs": 3,
        "completed_records": 1,
        "status": "incomplete",
    }
    assert report["status"]["state"] == "incomplete"
    assert report["status"]["thresholds_passed"] is False
    assert "majority" not in report["metrics"]
    assert "raw_exception" not in json.dumps(report)


def test_write_report_pair_cleans_only_its_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "out"
    report = {
        "schema_version": 1,
        "status": {"state": "complete"},
        "provenance": {},
        "metrics": {},
        "runs": [],
    }

    def fail_replace(_self: Path, _target: Path) -> Path:
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        write_report_pair(report, output_dir, "failed")

    assert not list(output_dir.glob("*.tmp"))
    assert not list(output_dir.glob(".*.tmp"))
