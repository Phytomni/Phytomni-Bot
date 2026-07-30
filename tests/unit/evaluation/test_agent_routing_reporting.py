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
from typing import Any

import pytest
from scripts.agent_routing_eval.dataset import AgentRoutingCase
from scripts.agent_routing_eval.metrics import compute_metrics
from scripts.agent_routing_eval.reporting import (
    GitState,
    ReportContext,
    _format_markdown_value,
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
    **options: Any,
) -> RunOutcome:
    """Build a bounded routing outcome for report fixtures."""
    options = {
        "selected_arguments": options.get("selected_arguments"),
        "error_code": options.get("error_code"),
        "predicted_agent": options.get("predicted_agent", "ChatAgent"),
        "agent_correct": options.get("agent_correct", True),
        "schema_valid": options.get("schema_valid", True),
        "dispatchable": options.get("dispatchable", True),
        "core_args_correct": options.get("core_args_correct", True),
        "provider_completed": options.get("provider_completed", True),
    }
    return RunOutcome(
        case_id=case_id,
        repeat_index=repeat_index,
        expected_agent="ChatAgent",
        predicted_agent=options["predicted_agent"],
        language="en",
        agent_correct=options["agent_correct"],
        schema_valid=options["schema_valid"],
        dispatchable=options["dispatchable"],
        core_args_correct=options["core_args_correct"],
        provider_completed=options["provider_completed"],
        attempts=1,
        latency_ms=12.5,
        selected_arguments=options["selected_arguments"]
        or {"user_query": case_id},
        error_code=options["error_code"],
        validation_codes=("missing",),
    )


def _context(
    dataset_path: Path,
    **options: Any,
) -> ReportContext:
    """Build a deterministic report context with bounded overrides."""
    mode = options.get("mode", "benchmark")
    repeat_count = options.get("repeat_count", 3)
    dirty = options.get("dirty", False)
    provider_endpoint_hash = options.get(
        "provider_endpoint_hash", "endpoint-digest"
    )
    model_id = options.get("model_id", "routing-model")
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


def _complete_report(tmp_path: Path) -> dict[str, Any]:
    dataset = tmp_path / "test_v1.jsonl"
    dataset.write_bytes(b"dataset\n")
    cases = [_case()]
    outcomes = [_outcome("case-a", index) for index in (1, 2, 3)]
    return build_report(
        _context(dataset),
        cases,
        outcomes,
        compute_metrics(cases, outcomes, 3),
        complete=True,
    )


def test_collect_git_state_does_not_persist_changed_paths() -> None:
    """Keep changed path contents out of persisted Git state."""
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
    """Bind report hashes to exact bytes and not endpoint text."""
    dataset = tmp_path / "cases.jsonl"
    dataset.write_bytes(b"first\nsecond\n")
    assert (
        dataset_sha256(dataset)
        == hashlib.sha256(b"first\nsecond\n").hexdigest()
    )

    changed: list[tuple[object, object, object]] = list(AGENT_TOOL_DEFINITIONS)
    name, _description, model = changed[0]
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
    """Redact sensitive values and sort report runs deterministically."""
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
                case_id,
                index,
                selected_arguments=(
                    {
                        "user_query": "Question text for case-b",
                        "api_key": "api-key-should-not-appear",
                        "detail": "provider raw exception detail",
                    }
                    if case_id == "case-b" and index == 2
                    else None
                ),
                error_code=(
                    "provider raw exception detail"
                    if case_id == "case-b" and index == 2
                    else None
                ),
            )
            for case_id in ("case-b", "case-a")
            for index in (3, 2, 1)
        ],
        compute_metrics(
            [_case("case-a"), _case("case-b")],
            [
                _outcome(case_id, index)
                for case_id in ("case-a", "case-b")
                for index in (1, 2, 3)
            ],
            3,
        ),
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
        "case-a",
        "case-a",
        "case-b",
        "case-b",
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
    """Avoid overclaiming current accuracy in report status."""
    dataset = tmp_path / "test_v1.jsonl"
    dataset.write_bytes(b"dataset\n")
    report = build_report(
        _context(dataset, dirty=dirty),
        [_case()],
        (
            [_outcome("case-a", index) for index in (1, 2, 3)]
            if provider_completion == 1.0
            else [
                _outcome(
                    "case-a",
                    1,
                    predicted_agent="__PROVIDER_ERROR__",
                    agent_correct=False,
                    schema_valid=False,
                    dispatchable=False,
                    core_args_correct=None,
                    provider_completed=False,
                    error_code="provider_timeout_exhausted",
                    selected_arguments={},
                ),
                _outcome("case-a", 2),
                _outcome("case-a", 3),
            ]
        ),
        compute_metrics(
            [_case()],
            (
                [_outcome("case-a", index) for index in (1, 2, 3)]
                if provider_completion == 1.0
                else [
                    _outcome(
                        "case-a",
                        1,
                        predicted_agent="__PROVIDER_ERROR__",
                        agent_correct=False,
                        schema_valid=False,
                        dispatchable=False,
                        core_args_correct=None,
                        provider_completed=False,
                        error_code="provider_timeout_exhausted",
                        selected_arguments={},
                    ),
                    _outcome("case-a", 2),
                    _outcome("case-a", 3),
                ]
            ),
            3,
        ),
        complete=True,
    )

    status = report["status"]
    assert status["headline"] == expected_headline
    if provider_completion < 0.99:
        assert status["current_accuracy"] == "Unknown"


def test_only_clean_test_benchmark_can_claim_stable_baseline(
    tmp_path: Path,
) -> None:
    """Allow a stable baseline only for a clean benchmark report."""
    dataset = tmp_path / "test_v1.jsonl"
    dataset.write_bytes(b"dataset\n")
    report = build_report(
        _context(dataset),
        [_case()],
        [_outcome("case-a", index) for index in (1, 2, 3)],
        compute_metrics(
            [_case()],
            [_outcome("case-a", index) for index in (1, 2, 3)],
            3,
        ),
        complete=True,
    )
    assert report["status"]["headline"] == "Stable baseline"

    quick_report = build_report(
        _context(dataset, mode="quick", repeat_count=1),
        [_case()],
        [_outcome("case-a")],
        compute_metrics([_case()], [_outcome("case-a")], 1),
        complete=True,
    )
    assert quick_report["status"]["headline"] == "Diagnostic"


def test_incomplete_report_is_bounded_and_cannot_claim_thresholds(
    tmp_path: Path,
) -> None:
    """Bound incomplete reports and prevent threshold claims."""
    dataset = tmp_path / "dev_v1.jsonl"
    dataset.write_bytes(b"dataset\n")
    report = build_report(
        _context(dataset, mode="quick", repeat_count=1),
        [_case()],
        [_outcome("case-a")],
        {
            "planned_runs": 1,
            "completed_records": 1,
            "majority": {"top1_accuracy": 1.0},
            "raw_exception": "provider raw exception detail",
        },
        complete=False,
    )

    assert report["metrics"] == {
        "planned_runs": 1,
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
    """Clean only the temporary artifact owned by one report write."""
    output_dir = tmp_path / "out"
    report = _complete_report(tmp_path)

    def fail_replace(_self: Path, _target: Path) -> Path:
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        write_report_pair(report, output_dir, "failed")

    assert not list(output_dir.glob("*.tmp"))
    assert not list(output_dir.glob(".*.tmp"))


def test_complete_report_rejects_empty_or_forged_metric_inventory(
    tmp_path: Path,
) -> None:
    """Reject empty or forged metric inventories in complete reports."""
    dataset = tmp_path / "test_v1.jsonl"
    dataset.write_bytes(b"dataset\n")
    cases = [_case()]
    outcomes = [_outcome("case-a", index) for index in (1, 2, 3)]
    forged = compute_metrics(cases, outcomes, 3)
    forged["case_count"] = 0

    with pytest.raises(ValueError, match="complete report inventory"):
        build_report(_context(dataset), cases, [], forged, complete=True)
    with pytest.raises(ValueError, match="supplied metrics"):
        build_report(_context(dataset), cases, outcomes, forged, complete=True)


def test_writer_boundary_drops_sensitive_arguments_and_rejects_unknown_fields(
    tmp_path: Path,
) -> None:
    """Drop sensitive arguments and reject unknown report fields."""
    report = _complete_report(tmp_path)
    raw = json.loads(json.dumps(report))
    raw["provenance"]["model_id"] = "provider.internal/v1"
    raw["runs"][0]["selected_arguments"] = {
        "user_query": "token-should-not-appear connection reset by backend-42",
        "token": "api-key-should-not-appear",
        "raw_exception": "provider raw exception detail",
    }
    json_path, markdown_path = write_report_pair(
        raw, tmp_path / "sanitized", "run"
    )
    emitted = json_path.read_text(encoding="utf-8")
    emitted += markdown_path.read_text(encoding="utf-8")
    for secret in (
        "provider.internal/v1",
        "token-should-not-appear",
        "api-key-should-not-appear",
        "provider raw exception detail",
    ):
        assert secret not in emitted

    raw["unknown_field"] = "must-not-be-emitted"
    with pytest.raises(ValueError, match="unexpected fields"):
        write_report_pair(raw, tmp_path / "rejected", "run")
    assert not (tmp_path / "rejected").exists()


def test_writer_rejects_forged_complete_run_inventory(tmp_path: Path) -> None:
    """Reject forged run counts and incomplete run records."""
    report = _complete_report(tmp_path)
    raw = json.loads(json.dumps(report))
    raw["metrics"]["completed_records"] = 2

    with pytest.raises(ValueError, match="complete report counts"):
        write_report_pair(raw, tmp_path / "rejected-counts", "run")

    raw = json.loads(json.dumps(report))
    raw["runs"][0].pop("repeat")
    with pytest.raises(ValueError, match="complete report run is incomplete"):
        write_report_pair(raw, tmp_path / "rejected-run", "run")


@pytest.mark.parametrize(
    "stem",
    ["../escaped", "nested/name", "nested\\name", "..", "a..b"],
)
def test_writer_rejects_unsafe_stems(tmp_path: Path, stem: str) -> None:
    """Reject path-traversal and non-basename report stems."""
    report = _complete_report(tmp_path)
    with pytest.raises(ValueError, match="direct ASCII basename"):
        write_report_pair(report, tmp_path / "out", stem)
    assert not (tmp_path / "out").exists()


def test_writer_rejects_absolute_stem(tmp_path: Path) -> None:
    """Reject absolute report output stems."""
    report = _complete_report(tmp_path)
    with pytest.raises(ValueError, match="direct ASCII basename"):
        write_report_pair(report, tmp_path / "out", str(tmp_path / "escape"))


def test_markdown_escapes_pipe_backslash_and_newline() -> None:
    """Escape Markdown control characters in rendered values."""
    assert _format_markdown_value("a|b\\c\r\nd") == "a\\|b\\\\c\\r\\nd"


def _fail_second_replace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remove both artifacts when a new pair publish fails."""
    original_replace = Path.replace
    calls = 0

    def fail_second(self: Path, target: Path) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("second artifact failed")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_second)


def test_pair_publish_rolls_back_without_existing_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restore the old pair when the second replacement fails."""
    report = _complete_report(tmp_path)
    output_dir = tmp_path / "new-pair"
    _fail_second_replace(monkeypatch)

    with pytest.raises(OSError, match="second artifact failed"):
        write_report_pair(report, output_dir, "run")
    assert not (output_dir / "run.json").exists()
    assert not (output_dir / "run.md").exists()
    assert not list(output_dir.glob(".*.tmp"))


def test_pair_publish_restores_existing_pair_on_second_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restore both existing artifacts after a failed replacement."""
    report = _complete_report(tmp_path)
    output_dir = tmp_path / "existing-pair"
    json_path, markdown_path = write_report_pair(report, output_dir, "run")
    old_json = json_path.read_bytes()
    old_markdown = markdown_path.read_bytes()
    _fail_second_replace(monkeypatch)

    with pytest.raises(OSError, match="second artifact failed"):
        write_report_pair(report, output_dir, "run")
    assert json_path.read_bytes() == old_json
    assert markdown_path.read_bytes() == old_markdown
    assert not list(output_dir.glob(".*.tmp"))
