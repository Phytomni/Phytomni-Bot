#!/usr/bin/env python3
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Compare direct GaussDB query fingerprints with an owner manifest.

The runner is an evidence tool, not a replacement BI client.  It reads the
checked-in eighteen-query callsite corpus, queries the hardened direct
``gauss_query`` seam only after explicit live authorization, and writes counts,
sorted columns, and SHA-256 fingerprints.  Result rows, SQL, DSNs, and driver
messages never enter the evidence file or CLI diagnostics.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.shared.gauss import gauss_query
from mcp_server_phytomni.common.gauss_probe import (
    add_environment_output_arguments,
    resolve_git_commit,
)

CORPUS_PATH = (
    Path(__file__).resolve().parents[1]
    / "tests/fixtures/gauss_query_corpus.json"
)
OUTPUT_DIR = Path("e2e/output")
LIVE_FLAGS = ("PHYTOMNI_RUN_INTEGRATION", "PHYTOMNI_ALLOW_NETWORK")
_COMMIT = re.compile(r"^[0-9a-f]{7,64}$")
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class BaselineError(ValueError):
    """Raised when an owner-approved baseline is absent or malformed."""


class ComparisonError(RuntimeError):
    """Raised when a live query cannot produce a safe fingerprint."""


def load_corpus(path: Path = CORPUS_PATH) -> list[dict[str, Any]]:
    """Load and structurally validate the eighteen-query corpus."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineError("query corpus unavailable") from exc
    if not isinstance(payload, list) or not payload:
        raise BaselineError("query corpus shape invalid")

    cases: list[dict[str, Any]] = []
    labels: set[str] = set()
    for case in payload:
        if not isinstance(case, dict):
            raise BaselineError("query corpus case invalid")
        label = case.get("label")
        sql = case.get("sql")
        expected_columns = case.get("expected_columns")
        if not isinstance(label, str) or not _LABEL.fullmatch(label):
            raise BaselineError("query corpus case invalid")
        if label in labels or not isinstance(sql, str) or not sql:
            raise BaselineError("query corpus case invalid")
        if not isinstance(expected_columns, list) or not all(
            isinstance(column, str) for column in expected_columns
        ):
            raise BaselineError("query corpus case invalid")
        labels.add(label)
        cases.append(case)
    return cases


def _safe_commit(value: str) -> str:
    """Return a bounded commit label or a fixed marker."""
    return value if _COMMIT.fullmatch(value) else "unknown"


def git_commit(
    run_command: Callable[..., Any] | None = None,
) -> str:
    """Return ``HEAD`` through an injectable command seam."""
    return resolve_git_commit(
        "",
        cwd=Path(__file__).resolve().parents[1],
        runner=run_command,
    )


def _safe_environment_class(value: str) -> str:
    """Return a bounded environment label without arbitrary echoing."""
    return value if _LABEL.fullmatch(value) else "unspecified"


def fingerprint_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    fallback_columns: Sequence[str] = (),
) -> dict[str, Any]:
    """Return the allowlisted count, sorted columns, and row-set hash."""
    normalized_rows: list[dict[str, Any]] = []
    columns: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ComparisonError("query result shape invalid")
        normalized = {str(key): value for key, value in row.items()}
        columns.update(normalized)
        normalized_rows.append(normalized)
    canonical_rows = sorted(
        json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        for row in normalized_rows
    )
    canonical_payload = json.dumps(
        canonical_rows,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {
        "row_count": len(normalized_rows),
        "columns": sorted(columns or set(fallback_columns)),
        "sha256": hashlib.sha256(
            canonical_payload.encode("utf-8")
        ).hexdigest(),
    }


def _baseline_records(payload: Any) -> list[dict[str, Any]]:
    """Extract the owner manifest's query records without echoing input."""
    records: Any
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        records = payload.get("queries")
        if records is None:
            records = payload.get("cases")
    else:
        records = None
    if not isinstance(records, list):
        raise BaselineError("baseline shape invalid")
    result: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            raise BaselineError("baseline record invalid")
        label = record.get("label")
        row_count = record.get("row_count")
        columns = record.get("columns")
        sha256 = record.get("sha256")
        if not isinstance(label, str) or not _LABEL.fullmatch(label):
            raise BaselineError("baseline record invalid")
        if (
            not isinstance(row_count, int)
            or isinstance(row_count, bool)
            or row_count < 0
        ):
            raise BaselineError("baseline record invalid")
        if not isinstance(columns, list) or not all(
            isinstance(column, str) for column in columns
        ):
            raise BaselineError("baseline record invalid")
        if columns != sorted(set(columns)):
            raise BaselineError("baseline record invalid")
        if not isinstance(sha256, str) or not _SHA256.fullmatch(
            sha256.lower()
        ):
            raise BaselineError("baseline record invalid")
        result.append(
            {
                "label": label,
                "row_count": row_count,
                "columns": columns,
                "sha256": sha256.lower(),
            }
        )
    return result


def load_baseline(path: Path, labels: set[str]) -> dict[str, dict[str, Any]]:
    """Load an owner manifest and require an exact, unique label set."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineError("owner baseline missing or invalid") from exc
    records = _baseline_records(payload)
    record_labels = [record["label"] for record in records]
    if len(record_labels) != len(set(record_labels)):
        raise BaselineError("owner baseline labels invalid")
    if set(record_labels) != labels:
        raise BaselineError("owner baseline labels invalid")
    return {record["label"]: record for record in records}


def _live_authorized() -> bool:
    """Return whether both explicit live-run flags are enabled."""
    return all(os.getenv(name) == "1" for name in LIVE_FLAGS)


async def _compare_case(
    case: dict[str, Any],
    expected: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Fingerprint one corpus case and compare it to its baseline record."""
    try:
        result = await gauss_query(case["sql"])
        rows = result.get("data") if isinstance(result, dict) else None
        if not isinstance(rows, list):
            raise ComparisonError("query result shape invalid")
        fingerprint = fingerprint_rows(
            rows,
            fallback_columns=case["expected_columns"],
        )
    except (
        ComparisonError,
        McpError,
        OSError,
        TypeError,
        ValueError,
        RuntimeError,
    ) as exc:
        raise ComparisonError("query comparison failed") from exc
    is_match = all(
        fingerprint[key] == expected[key]
        for key in ("row_count", "columns", "sha256")
    )
    return (
        {
            "label": case["label"],
            **fingerprint,
            "status": "match" if is_match else "mismatch",
        },
        is_match,
    )


async def run_comparison(
    *,
    baseline_path: Path,
    corpus_path: Path = CORPUS_PATH,
    environment_class: str = "unspecified",
    commit: str | None = None,
    git_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Query the corpus and return metadata-only comparison evidence."""
    cases = load_corpus(corpus_path)
    baseline = load_baseline(baseline_path, {case["label"] for case in cases})
    evidence_queries: list[dict[str, Any]] = []
    matched = 0
    for case in cases:
        evidence, is_match = await _compare_case(case, baseline[case["label"]])
        matched += int(is_match)
        evidence_queries.append(evidence)
    resolved_commit = commit
    if resolved_commit is None:
        resolved_commit = (
            git_commit()
            if git_runner is None
            else git_commit(run_command=git_runner)
        )
    return {
        "commit": _safe_commit(resolved_commit),
        "environment_class": _safe_environment_class(environment_class),
        "total": len(cases),
        "matched": matched,
        "mismatched": len(cases) - matched,
        "queries": evidence_queries,
    }


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse the comparison CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Compare direct GaussDB query fingerprints."
    )
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, default=CORPUS_PATH)
    add_environment_output_arguments(
        parser,
        environment_default=os.getenv(
            "PHYTOMNI_ENVIRONMENT_CLASS", "unspecified"
        ),
        output_default=OUTPUT_DIR / "gauss_query_comparison.json",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the guarded comparison and return a process exit code."""
    args = _parse_args(argv)
    if not _live_authorized():
        print(
            "External Pending: comparison requires "
            "PHYTOMNI_RUN_INTEGRATION=1 and PHYTOMNI_ALLOW_NETWORK=1",
            file=sys.stderr,
        )
        return 2
    try:
        evidence = asyncio.run(
            run_comparison(
                baseline_path=args.baseline,
                corpus_path=args.corpus,
                environment_class=args.environment_class,
            )
        )
    except BaselineError:
        print(
            "External Pending: owner-approved baseline is missing or invalid",
            file=sys.stderr,
        )
        return 2
    except (ComparisonError, OSError, RuntimeError, TypeError, ValueError):
        print("GaussDB query comparison failed", file=sys.stderr)
        return 1

    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        print(
            "GaussDB comparison evidence could not be written", file=sys.stderr
        )
        return 1

    if evidence["mismatched"]:
        print(
            f"GaussDB comparison mismatch: {evidence['mismatched']} of "
            f"{evidence['total']} queries",
            file=sys.stderr,
        )
        return 1
    print(f"GaussDB comparison matched {evidence['matched']} queries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
