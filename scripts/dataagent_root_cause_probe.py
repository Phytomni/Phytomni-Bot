#!/usr/bin/env python3
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Run one guarded, sanitized DataAgent exact-query replay.

The request identity and query are fixed to the incident under review.  A
live call requires both explicit integration and network flags; the evidence
writer never records the query, credentials, provider body, SQL, sequence, or
private paths.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from mcp_server_phytomni.common.gauss_probe import resolve_git_commit
from mcp_server_phytomni.runtime.stage_trace import DataStage

INCIDENT_QUERY = "What is cDNA sequence of Os09t0241100-01 in rice?"
INCIDENT_DIALOGUE_ID = "932a5dc9-d928-481f-83cc-9346dc990dda"
INCIDENT_LOCALE = "en-US"
NATIVE_RUN_PATH = "/v1/agents/data/runs"
OUTPUT_DIR = Path("e2e/output")
DEFAULT_OUTPUT = OUTPUT_DIR / "dataagent_root_cause.json"
LIVE_FLAGS = ("PHYTOMNI_RUN_INTEGRATION", "PHYTOMNI_ALLOW_NETWORK")
PROBE_TIMEOUT_SECONDS = 1200.0
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_TOKEN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SAFE_CLASS = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,63}$")
_IUPAC = re.compile(r"^[ACGTRYSWKMBDHVUN]+$")
_SEQUENCE_KEYS = frozenset(
    {"sequence", "sequence_string", "cdna_sequence", "nucleotide_sequence"}
)
_STAGES = frozenset(stage.value for stage in DataStage)


class ProbeGuardError(RuntimeError):
    """Raised before a replay when operator authorization is incomplete."""


@dataclass(frozen=True, slots=True)
class ProbeRequestEvidence:
    """Request identity fields for one exact-query replay."""

    observed_at: str
    bot_sha: str
    request_id: str
    dialogue_id: str
    query_sha256: str


@dataclass(frozen=True, slots=True)
class ProbeResponseEvidence:
    """Response identity and safe error fields from one replay."""

    http_status: int
    run_id: str | None
    task_ids: tuple[str, ...]
    error_code: str | None
    error_stage: str | None
    response_sha256: str


@dataclass(frozen=True, slots=True)
class ProbeSequenceMetrics:
    """Non-reversible metrics for a single extracted sequence."""

    sequence_length: int | None = None
    sequence_sha256: str | None = None
    alphabet_valid: bool | None = None


@dataclass(frozen=True, slots=True)
class ProbeEvidence:
    """Allowlisted, JSON-safe metadata from one exact-query replay."""

    request: ProbeRequestEvidence
    response: ProbeResponseEvidence
    stage_summary: tuple[Mapping[str, Any], ...] = ()
    sequence: ProbeSequenceMetrics = ProbeSequenceMetrics()


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser without exposing a query override."""
    parser = argparse.ArgumentParser(
        description="Run one guarded DataAgent exact-query replay."
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="Bot HTTP base URL; no credentials are accepted in this value.",
    )
    parser.add_argument(
        "--api-key-env",
        required=True,
        help="Environment variable containing the API key.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Sanitized JSON evidence path.",
    )
    return parser


def build_incident_payload() -> dict[str, Any]:
    """Return the immutable native request body for the incident."""
    return {
        "arguments": {"user_query": INCIDENT_QUERY},
        "dialogue_id": INCIDENT_DIALOGUE_ID,
        "locale": INCIDENT_LOCALE,
    }


def assert_live_probe_allowed() -> None:
    """Require both explicit flags before any credential or network access."""
    if any(os.environ.get(flag) != "1" for flag in LIVE_FLAGS):
        required = " and ".join(f"{flag}=1" for flag in LIVE_FLAGS)
        raise ProbeGuardError(f"requires {required}")


def _validate_base_url(value: str) -> str:
    """Return a URL without embedded credentials or fragments."""
    try:
        parsed = urlsplit(value)
        username = parsed.username
        password = parsed.password
    except ValueError as exc:
        raise ProbeGuardError("base URL is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProbeGuardError("base URL is invalid")
    if username is not None or password is not None:
        raise ProbeGuardError("base URL is invalid")
    if parsed.query or parsed.fragment:
        raise ProbeGuardError("base URL is invalid")
    return value.rstrip("/")


def _read_api_key(env_name: str) -> str:
    """Read one nonblank API key from a validated environment name."""
    if not _ENV_NAME.fullmatch(env_name):
        raise ProbeGuardError("API key environment name is invalid")
    value = os.environ.get(env_name, "")
    if not value:
        raise ProbeGuardError("API key environment variable is unset")
    return value


def _safe_identity(value: Any) -> str | None:
    """Return an identifier only when it matches the bounded safe alphabet."""
    return (
        value if isinstance(value, str) and _SAFE_ID.fullmatch(value) else None
    )


def _safe_token(value: Any) -> str | None:
    """Return a bounded public error/dependency token."""
    return (
        value
        if isinstance(value, str) and _SAFE_TOKEN.fullmatch(value)
        else None
    )


def _safe_stage(value: Any) -> str | None:
    """Return one of the six fixed DataAgent stage names."""
    return value if isinstance(value, str) and value in _STAGES else None


def _safe_class(value: Any) -> str | None:
    """Return a bounded exception class name without its message."""
    return (
        value
        if isinstance(value, str) and _SAFE_CLASS.fullmatch(value)
        else None
    )


def _safe_int(value: Any) -> int | None:
    """Return a bounded nonnegative integer, excluding booleans."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _safe_stage_summary(value: Any) -> tuple[Mapping[str, Any], ...]:
    """Project structured stage events to their allowlisted scalar fields."""
    if not isinstance(value, (list, tuple)):
        return ()
    projected: list[Mapping[str, Any]] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        event: dict[str, Any] = {}
        stage = _safe_stage(raw.get("stage"))
        if stage is None:
            continue
        event["stage"] = stage
        dependency = _safe_token(raw.get("dependency"))
        if dependency is not None:
            event["dependency"] = dependency
        duration_ms = _safe_int(raw.get("duration_ms"))
        if duration_ms is not None:
            event["duration_ms"] = duration_ms
        error_code = _safe_token(raw.get("error_code"))
        if error_code is not None:
            event["error_code"] = error_code
        error_class = _safe_class(raw.get("error_class"))
        if error_class is not None:
            event["error_class"] = error_class
        final_status = _safe_int(raw.get("final_http_status"))
        if final_status is not None:
            event["final_http_status"] = final_status
        projected.append(event)
    return tuple(projected)


def _extract_stage_summary(
    body: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    """Read an optional server-projected stage summary, never raw logs."""
    for key in ("stage_summary", "stage_trace"):
        summary = _safe_stage_summary(body.get(key))
        if summary:
            return summary
    return ()


def _extract_task_ids(body: Mapping[str, Any]) -> tuple[str, ...]:
    """Project only top-level accepted task identifiers."""
    raw_task_ids = body.get("task_ids")
    if not isinstance(raw_task_ids, (list, tuple)):
        return ()
    return tuple(
        task_id
        for value in raw_task_ids
        if (task_id := _safe_identity(value)) is not None
    )


def _normalize_sequence(value: Any) -> str | None:
    """Normalize a keyed nucleotide value without retaining its content."""
    if not isinstance(value, str):
        return None
    normalized = "".join(value.split()).upper()
    return normalized or None


def _keyed_sequences(value: Any) -> list[str]:
    """Collect candidate sequences only from explicitly named fields."""
    candidates: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = str(key).lower()
            if normalized_key in _SEQUENCE_KEYS:
                sequence = _normalize_sequence(item)
                if sequence is not None:
                    candidates.append(sequence)
            else:
                candidates.extend(_keyed_sequences(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            candidates.extend(_keyed_sequences(item))
    return candidates


def _tabular_sequences(body: Mapping[str, Any]) -> list[str]:
    """Collect candidates from a table column explicitly named sequence."""
    formatted = body.get("result")
    if isinstance(formatted, Mapping):
        formatted = formatted.get("formatted")
    if not isinstance(formatted, Mapping):
        formatted = body.get("formatted")
    if not isinstance(formatted, Mapping):
        return []
    tabular = formatted.get("tabular")
    if not isinstance(tabular, Mapping):
        return []
    headers = tabular.get("headers")
    rows = tabular.get("rows")
    if not isinstance(headers, Sequence) or isinstance(headers, str):
        return []
    if not isinstance(rows, Sequence) or isinstance(rows, str):
        return []
    indexes = [
        index
        for index, header in enumerate(headers)
        if str(header).lower() in _SEQUENCE_KEYS
    ]
    candidates: list[str] = []
    for row in rows:
        if not isinstance(row, Sequence) or isinstance(row, str):
            continue
        for index in indexes:
            if index < len(row):
                sequence = _normalize_sequence(row[index])
                if sequence is not None:
                    candidates.append(sequence)
    return candidates


def _sequence_metrics(
    body: Mapping[str, Any],
) -> tuple[int, str, bool] | None:
    """Return length, hash, and alphabet validity for one unique sequence."""
    candidates = _keyed_sequences(body) + _tabular_sequences(body)
    unique = set(candidates)
    if len(unique) != 1:
        return None
    sequence = next(iter(unique))
    return (
        len(sequence),
        hashlib.sha256(sequence.encode("utf-8")).hexdigest(),
        bool(_IUPAC.fullmatch(sequence)),
    )


def _response_body(response: Any) -> Mapping[str, Any]:
    """Return a JSON object response, dropping all other shapes."""
    try:
        value = response.json()
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _response_bytes(response: Any, body: Mapping[str, Any]) -> bytes:
    """Return response bytes for hashing without exposing them."""
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content
    return json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def build_evidence(
    *,
    body: Mapping[str, Any],
    http_status: int,
    request_id: str,
    bot_sha: str,
    response_bytes: bytes,
) -> ProbeEvidence:
    """Build one allowlisted evidence record from an HTTP response."""
    error = body.get("error")
    error = error if isinstance(error, Mapping) else {}
    sequence = _sequence_metrics(body)
    return ProbeEvidence(
        request=ProbeRequestEvidence(
            observed_at=datetime.now(UTC).isoformat(),
            bot_sha=bot_sha,
            request_id=request_id,
            dialogue_id=INCIDENT_DIALOGUE_ID,
            query_sha256=hashlib.sha256(INCIDENT_QUERY.encode()).hexdigest(),
        ),
        response=ProbeResponseEvidence(
            http_status=http_status,
            run_id=_safe_identity(body.get("id") or body.get("run_id")),
            task_ids=_extract_task_ids(body),
            error_code=_safe_token(error.get("code")),
            error_stage=_safe_stage(error.get("stage")),
            response_sha256=hashlib.sha256(response_bytes).hexdigest(),
        ),
        stage_summary=_extract_stage_summary(body),
        sequence=ProbeSequenceMetrics(
            sequence_length=sequence[0] if sequence is not None else None,
            sequence_sha256=sequence[1] if sequence is not None else None,
            alphabet_valid=sequence[2] if sequence is not None else None,
        ),
    )


async def run_probe(
    *,
    base_url: str,
    api_key: str,
    request_id: str,
    bot_sha: str,
    client_factory: Callable[..., Any] = httpx.AsyncClient,
) -> ProbeEvidence:
    """Send one fixed native request and return sanitized evidence."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Request-Id": request_id,
    }
    async with client_factory(
        base_url=base_url,
        timeout=httpx.Timeout(PROBE_TIMEOUT_SECONDS),
        follow_redirects=False,
    ) as client:
        response = await client.post(
            NATIVE_RUN_PATH,
            headers=headers,
            json=build_incident_payload(),
        )
    body = _response_body(response)
    return build_evidence(
        body=body,
        http_status=int(response.status_code),
        request_id=request_id,
        bot_sha=bot_sha,
        response_bytes=_response_bytes(response, body),
    )


def _evidence_dict(evidence: ProbeEvidence) -> dict[str, Any]:
    """Convert the dataclass to a JSON-serializable allowlist."""
    data = asdict(evidence)
    return {
        **data["request"],
        **data["response"],
        "stage_summary": data["stage_summary"],
        **data["sequence"],
    }


def write_evidence(path: Path, evidence: ProbeEvidence) -> None:
    """Write canonical sorted JSON with a final newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            _evidence_dict(evidence),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the guarded replay and return a safe process status."""
    args = build_parser().parse_args(argv)
    try:
        assert_live_probe_allowed()
        base_url = _validate_base_url(args.base_url)
        api_key = _read_api_key(args.api_key_env)
    except ProbeGuardError as exc:
        print(f"DataAgent exact-query probe rejected: {exc}", file=sys.stderr)
        return 2

    request_id = str(uuid.uuid4())
    try:
        evidence = asyncio.run(
            run_probe(
                base_url=base_url,
                api_key=api_key,
                request_id=request_id,
                bot_sha=resolve_git_commit(
                    "",
                    cwd=Path(__file__).resolve().parents[1],
                ),
            )
        )
        write_evidence(args.output, evidence)
    except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
        print(
            "DataAgent exact-query probe failed: " f"{type(exc).__name__}",
            file=sys.stderr,
        )
        return 1
    print(
        "DataAgent exact-query probe wrote sanitized evidence: "
        f"HTTP {evidence.response.http_status}, request_id={request_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
