# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Offline tests for the eighteen-query comparison evidence runner."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


def _load_runner() -> Any:
    """Load the standalone runner without packaging ``scripts/``."""
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts/compare_gauss_queries.py"
    )
    spec = importlib.util.spec_from_file_location(
        "compare_gauss_queries", path
    )
    if spec is None or spec.loader is None:
        raise AssertionError("comparison runner spec is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compare_gauss_queries = _load_runner()


def _baseline_for(
    runner: Any,
    rows_by_label: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Build an owner-manifest-shaped baseline from deterministic fake rows."""
    cases = runner.load_corpus()
    return {
        "version": 1,
        "queries": [
            {
                "label": case["label"],
                **runner.fingerprint_rows(
                    rows_by_label[case["label"]],
                    fallback_columns=case["expected_columns"],
                ),
            }
            for case in cases
        ],
    }


def _invoke_main(
    runner: Any,
    args: list[str],
    capsys: pytest.CaptureFixture[str],
) -> SimpleNamespace:
    """Invoke the CLI seam and retain its fixed stdout/stderr text."""
    exit_code = runner.main(args)
    captured = capsys.readouterr()
    return SimpleNamespace(
        exit_code=exit_code,
        stdout=captured.out,
        stderr=captured.err,
    )


def test_missing_baseline_is_external_pending(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing owner manifest is an external evidence gap, not success."""
    result = _invoke_main(
        compare_gauss_queries,
        [
            "--baseline",
            str(tmp_path / "missing.json"),
            "--output",
            str(tmp_path / "evidence.json"),
        ],
        capsys,
    )
    assert result.exit_code == 2
    assert "External Pending" in result.stderr


def test_evidence_contains_fingerprints_not_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Matching evidence records counts/columns/hashes, never rows or SQL."""
    monkeypatch.setenv("PHYTOMNI_RUN_INTEGRATION", "1")
    monkeypatch.setenv("PHYTOMNI_ALLOW_NETWORK", "1")
    cases = compare_gauss_queries.load_corpus()
    rows_by_label = {
        case["label"]: [
            {"gene_id": "secret-customer-row", "value": case["label"]}
        ]
        for case in cases
    }
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps(_baseline_for(compare_gauss_queries, rows_by_label)),
        encoding="utf-8",
    )
    output_path = tmp_path / "evidence.json"

    async def fake_gauss_query(sql: str) -> dict[str, Any]:
        label = cases[len(seen)]["label"]
        seen.append(sql)
        return {"message": "ok", "data": rows_by_label[label]}

    seen: list[str] = []
    monkeypatch.setattr(compare_gauss_queries, "gauss_query", fake_gauss_query)
    monkeypatch.setattr(
        compare_gauss_queries,
        "git_commit",
        lambda: "abc1234",
    )

    result = _invoke_main(
        compare_gauss_queries,
        [
            "--baseline",
            str(baseline_path),
            "--environment-class",
            "test",
            "--output",
            str(output_path),
        ],
        capsys,
    )

    assert result.exit_code == 0
    evidence = json.loads(output_path.read_text(encoding="utf-8"))
    rendered = json.dumps(evidence).lower()
    assert evidence["matched"] == 18
    assert evidence["mismatched"] == 0
    assert "rows" not in rendered
    assert "select" not in rendered
    assert "secret-customer-row" not in rendered
    assert all("sha256" in case for case in evidence["queries"])
    assert len(seen) == 18


def test_mismatch_returns_one_without_result_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A changed count/hash is a failed comparison with redacted evidence."""
    monkeypatch.setenv("PHYTOMNI_RUN_INTEGRATION", "1")
    monkeypatch.setenv("PHYTOMNI_ALLOW_NETWORK", "1")
    cases = compare_gauss_queries.load_corpus()
    matching_rows: dict[str, list[dict[str, Any]]] = {
        case["label"]: [] for case in cases
    }
    baseline = _baseline_for(compare_gauss_queries, matching_rows)
    baseline["queries"][0]["sha256"] = "0" * 64
    baseline_path = tmp_path / "baseline.json"
    output_path = tmp_path / "evidence.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    async def fake_gauss_query(_sql: str) -> dict[str, Any]:
        return {"message": "ok", "data": []}

    monkeypatch.setattr(compare_gauss_queries, "gauss_query", fake_gauss_query)
    monkeypatch.setattr(compare_gauss_queries, "git_commit", lambda: "abc1234")
    result = _invoke_main(
        compare_gauss_queries,
        ["--baseline", str(baseline_path), "--output", str(output_path)],
        capsys,
    )

    assert result.exit_code == 1
    evidence = json.loads(output_path.read_text(encoding="utf-8"))
    assert evidence["matched"] == 17
    assert evidence["mismatched"] == 1
    assert "sha256" in json.dumps(evidence)
    assert "SELECT" not in json.dumps(evidence)


@pytest.mark.parametrize(
    "manifest",
    [
        {"version": 1, "queries": []},
        {
            "version": 1,
            "queries": [
                {
                    "label": "duplicate",
                    "row_count": 0,
                    "columns": [],
                    "sha256": "0" * 64,
                },
                {
                    "label": "duplicate",
                    "row_count": 0,
                    "columns": [],
                    "sha256": "0" * 64,
                },
            ],
        },
    ],
)
def test_invalid_baseline_labels_are_external_pending(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    manifest: dict[str, Any],
) -> None:
    """Missing or duplicate labels cannot produce a compatibility claim."""
    baseline_path = tmp_path / "invalid.json"
    baseline_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = _invoke_main(
        compare_gauss_queries,
        ["--baseline", str(baseline_path)],
        capsys,
    )
    assert result.exit_code == 2
    assert "External Pending" in result.stderr


def test_runbook_documents_the_comparison_contract() -> None:
    """Keep the operator command and fingerprint-only output visible."""
    root = Path(__file__).resolve().parents[3]
    runbook = (root / "docs/ops/http-api-runbook.md").read_text(
        encoding="utf-8"
    )
    assert "scripts/compare_gauss_queries.py" in runbook
    assert "owner-approved archived manifest" in runbook
    assert "never writes SQL, DSNs, result rows" in runbook


def test_git_commit_uses_injected_command_without_echoing_output() -> None:
    """The commit lookup is deterministic and independently injectable."""
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(stdout="abc1234\n")

    assert compare_gauss_queries.git_commit(run_command=fake_run) == "abc1234"
    assert calls == [["git", "rev-parse", "HEAD"]]


def test_extra_baseline_label_is_external_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An owner manifest with an extra label cannot claim compatibility."""
    monkeypatch.setenv("PHYTOMNI_RUN_INTEGRATION", "1")
    monkeypatch.setenv("PHYTOMNI_ALLOW_NETWORK", "1")
    cases = compare_gauss_queries.load_corpus()
    empty_rows: dict[str, list[dict[str, Any]]] = {
        case["label"]: [] for case in cases
    }
    manifest = _baseline_for(compare_gauss_queries, empty_rows)
    manifest["queries"].append(
        {
            "label": "owner.extra",
            "row_count": 0,
            "columns": [],
            "sha256": compare_gauss_queries.fingerprint_rows([])["sha256"],
        }
    )
    baseline_path = tmp_path / "extra.json"
    baseline_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = _invoke_main(
        compare_gauss_queries,
        ["--baseline", str(baseline_path)],
        capsys,
    )
    assert result.exit_code == 2
    assert "External Pending" in result.stderr
