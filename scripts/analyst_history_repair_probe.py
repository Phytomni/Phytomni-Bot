#!/usr/bin/env python3
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Build a read-only, owner-scoped Analyst history repair packet.

This probe is deliberately not a repair tool.  It uses SQLite's read-only
URI mode, a deny-by-default SQL authorizer for mutations, and fixed query
shapes.  A unique match produces only a dry-run mutation and reversal
proposal; no database row can be changed by this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

HISTORICAL_WEB_REQUEST_ID = "bdda4801-3ba9-4692-8d16-ad9807a6674d"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_ANALYST = "analyst"
_APPROVALS = (
    "incident_owner",
    "application_owner",
    "database_owner",
)
_PUBLIC_RUN_FIELDS = (
    "run_id",
    "user_id",
    "agent",
    "status",
    "created_at",
    "updated_at",
    "expires_at",
    "request_id",
    "dialogue_id",
)
_PUBLIC_TASK_FIELDS = ("task_id", "run_id", "user_id", "agent", "status")
_REQUIRED_RUN_COLUMNS = frozenset(
    {"run_id", "user_id", "agent", "request_id", "dialogue_id"}
)
_REQUIRED_TASK_COLUMNS = frozenset(
    {"task_id", "run_id", "user_id", "agent", "status"}
)
MatchStatus = Literal[
    "zero_matches",
    "unique_match",
    "multiple_matches",
]

__all__ = [
    "HISTORICAL_WEB_REQUEST_ID",
    "HistoricalCorrelation",
    "HistoricalRunRecord",
    "MatchStatus",
    "ProbeIdentity",
    "correlate_historical_analyst",
    "main",
    "open_read_only",
    "read_only_connection",
    "run_probe",
]


class ProbeError(RuntimeError):
    """Raised when the read-only registry packet cannot be produced."""


@dataclass(frozen=True, slots=True)
class ProbeIdentity:
    """Evidence supplied by the operator for one historical incident."""

    owner: str
    web_request_id: str
    bot_request_id: str | None = None
    dialogue_id: str | None = None
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class HistoricalRunRecord:
    """Small correlation view independent of the writable runtime registry."""

    owner: str
    agent: str
    run_id: str
    request_id: str | None
    dialogue_id: str | None
    task_ids: tuple[str, ...]
    tasks_valid: bool = True


@dataclass(frozen=True, slots=True)
class HistoricalCorrelation:
    """Result of exact historical identity correlation."""

    status: MatchStatus
    web_request_id: str
    bot_request_id: str | None
    dialogue_id: str | None
    run_id: str | None
    task_ids: tuple[str, ...]
    snapshot_sha256: str | None

    @property
    def write_authorized(self) -> Literal[False]:
        """Expose the immutable no-write contract."""
        return False


@dataclass(frozen=True, slots=True)
class _HistoricalCandidate:
    """Private database rows retained only for hashing/proposal generation."""

    record: HistoricalRunRecord
    run_row: dict[str, Any]
    task_rows: tuple[dict[str, Any], ...]


def _record_value(record: Any, name: str, default: Any = None) -> Any:
    """Read one field from a mapping or an object without invoking writes."""
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _record_view(record: Any) -> HistoricalRunRecord:
    """Normalize a runtime-like record into the read-only correlation view."""
    spec = _record_value(record, "spec")
    request_info = _record_value(record, "request_info")
    owner = _record_value(spec, "user_id", _record_value(record, "owner"))
    agent = _record_value(spec, "agent", _record_value(record, "agent"))
    run_id = _record_value(spec, "run_id", _record_value(record, "run_id"))
    request_id = _record_value(
        request_info,
        "request_id",
        _record_value(record, "request_id"),
    )
    dialogue_id = _record_value(
        request_info,
        "dialogue_id",
        _record_value(record, "dialogue_id"),
    )
    task_ids = _record_value(record, "task_ids", ())
    if not isinstance(task_ids, (tuple, list)):
        task_ids = ()
    return HistoricalRunRecord(
        owner=str(owner or ""),
        agent=str(agent or ""),
        run_id=str(run_id or ""),
        request_id=request_id if isinstance(request_id, str) else None,
        dialogue_id=dialogue_id if isinstance(dialogue_id, str) else None,
        task_ids=tuple(
            task_id for task_id in task_ids if isinstance(task_id, str)
        ),
        tasks_valid=bool(_record_value(record, "tasks_valid", True)),
    )


def _record_matches(
    record: HistoricalRunRecord,
    identity: ProbeIdentity,
) -> bool:
    """Require owner, Analyst, exact identifiers, and valid child linkage."""
    expected_request_id = identity.bot_request_id or identity.web_request_id
    return all(
        (
            record.owner == identity.owner,
            record.agent == _ANALYST,
            bool(record.run_id),
            bool(record.task_ids),
            record.tasks_valid,
            record.request_id == expected_request_id,
            identity.dialogue_id is None
            or record.dialogue_id == identity.dialogue_id,
            identity.run_id is None or record.run_id == identity.run_id,
        )
    )


def correlate_historical_analyst(
    *,
    owner: str,
    web_request_id: str,
    records: Sequence[Any],
    evidence: ProbeIdentity | None = None,
    snapshot_sha256: str | None = None,
) -> HistoricalCorrelation:
    """Correlate only exact owner-scoped Analyst records.

    ``records`` must already be read from the registry.  The function never
    treats query text, title-like fields, or output paths as identifiers.
    The Web request id is an exact request-id candidate when no separate Bot
    request id has been supplied; a mismatch therefore degrades to zero.
    """
    identity = evidence or ProbeIdentity(
        owner=owner,
        web_request_id=web_request_id,
    )
    if (identity.owner, identity.web_request_id) != (
        owner,
        web_request_id,
    ):
        raise ProbeError("correlation evidence identity mismatch")
    matched = [
        _record_view(record)
        for record in records
        if _record_matches(_record_view(record), identity)
    ]
    if not matched:
        status: MatchStatus = "zero_matches"
    elif len(matched) == 1:
        status = "unique_match"
    else:
        status = "multiple_matches"
    unique = matched[0] if status == "unique_match" else None
    return HistoricalCorrelation(
        status=status,
        web_request_id=web_request_id,
        bot_request_id=identity.bot_request_id,
        dialogue_id=identity.dialogue_id,
        run_id=unique.run_id if unique is not None else None,
        task_ids=unique.task_ids if unique is not None else (),
        snapshot_sha256=snapshot_sha256,
    )


def _read_only_authorizer(
    action: int,
    _arg1: str | None,
    _arg2: str | None,
    _database: str | None,
    _source: str | None,
) -> int:
    """Deny DML/DDL even if a future edit adds a non-SELECT statement."""
    denied = {
        sqlite3.SQLITE_ALTER_TABLE,
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_INDEX,
        sqlite3.SQLITE_CREATE_TEMP_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
        sqlite3.SQLITE_CREATE_TEMP_VIEW,
        sqlite3.SQLITE_CREATE_TRIGGER,
        sqlite3.SQLITE_CREATE_VTABLE,
        sqlite3.SQLITE_CREATE_VIEW,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_DETACH,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_INDEX,
        sqlite3.SQLITE_DROP_TEMP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_TRIGGER,
        sqlite3.SQLITE_DROP_TEMP_VIEW,
        sqlite3.SQLITE_DROP_TRIGGER,
        sqlite3.SQLITE_DROP_VTABLE,
        sqlite3.SQLITE_DROP_VIEW,
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
    }
    return sqlite3.SQLITE_DENY if action in denied else sqlite3.SQLITE_OK


@contextmanager
def open_read_only(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open an existing SQLite database read-only and close it on exit."""
    resolved = Path(db_path).expanduser().resolve()
    if not resolved.is_file():
        raise ProbeError("read-only registry is unavailable")
    uri = f"file:{quote(str(resolved), safe='/')}?mode=ro"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.set_authorizer(_read_only_authorizer)
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        raise ProbeError("read-only registry is unavailable") from exc
    try:
        yield connection
    finally:
        connection.close()


read_only_connection = open_read_only


def _table_columns(
    connection: sqlite3.Connection,
    table: Literal["runs", "tasks"],
) -> frozenset[str]:
    """Return a table's columns through a fixed schema inspection query."""
    try:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error as exc:
        raise ProbeError("read-only registry schema is unavailable") from exc
    return frozenset(str(row[1]) for row in rows)


def _require_schema(connection: sqlite3.Connection) -> None:
    """Reject legacy/incomplete stores instead of guessing missing fields."""
    if not _REQUIRED_RUN_COLUMNS.issubset(_table_columns(connection, "runs")):
        raise ProbeError("read-only registry schema is unavailable")
    if not _REQUIRED_TASK_COLUMNS.issubset(
        _table_columns(connection, "tasks")
    ):
        raise ProbeError("read-only registry schema is unavailable")


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert one SQLite row to a private, JSON-compatible snapshot row."""
    result: dict[str, Any] = {}
    keys = tuple(row.keys())
    for key in keys:
        result[str(key)] = row[key]
    return result


def _load_candidates(
    connection: sqlite3.Connection,
    identity: ProbeIdentity,
) -> list[_HistoricalCandidate]:
    """Load exact owner/request candidates and validate child ownership."""
    _require_schema(connection)
    expected_request_id = identity.bot_request_id or identity.web_request_id
    clauses = ["user_id = ?", "agent = ?", "request_id = ?"]
    params: list[str] = [identity.owner, _ANALYST, expected_request_id]
    if identity.dialogue_id is not None:
        clauses.append("dialogue_id = ?")
        params.append(identity.dialogue_id)
    if identity.run_id is not None:
        clauses.append("run_id = ?")
        params.append(identity.run_id)
    query = "SELECT * FROM runs WHERE " + " AND ".join(clauses)
    try:
        run_rows = connection.execute(query, params).fetchall()
    except sqlite3.Error as exc:
        raise ProbeError("read-only registry query failed") from exc

    candidates: list[_HistoricalCandidate] = []
    for raw_run in run_rows:
        run_row = _row_dict(raw_run)
        run_id = run_row.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            continue
        try:
            raw_tasks = connection.execute(
                "SELECT * FROM tasks WHERE run_id = ? ORDER BY task_id",
                (run_id,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise ProbeError("read-only registry query failed") from exc
        task_rows = tuple(_row_dict(row) for row in raw_tasks)
        if not task_rows or not all(
            row.get("run_id") == run_id
            and row.get("user_id") == identity.owner
            and row.get("agent") == _ANALYST
            for row in task_rows
        ):
            continue
        candidates.append(
            _HistoricalCandidate(
                record=HistoricalRunRecord(
                    owner=str(run_row.get("user_id") or ""),
                    agent=str(run_row.get("agent") or ""),
                    run_id=run_id,
                    request_id=(
                        run_row["request_id"]
                        if isinstance(run_row.get("request_id"), str)
                        else None
                    ),
                    dialogue_id=(
                        run_row["dialogue_id"]
                        if isinstance(run_row.get("dialogue_id"), str)
                        else None
                    ),
                    task_ids=tuple(
                        str(row["task_id"])
                        for row in task_rows
                        if isinstance(row.get("task_id"), str)
                    ),
                ),
                run_row=run_row,
                task_rows=task_rows,
            )
        )
    return candidates


def _private_snapshot(
    candidates: Sequence[_HistoricalCandidate],
) -> dict[str, list[dict[str, Any]]]:
    """Build the complete private pre-write snapshot for hashing/storage."""
    return {
        "runs": [candidate.run_row for candidate in candidates],
        "tasks": [
            task_row
            for candidate in candidates
            for task_row in candidate.task_rows
        ],
    }


def _canonical_json(value: Any) -> bytes:
    """Serialize snapshot data deterministically without omitting fields."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _snapshot_sha256(snapshot: Mapping[str, Any]) -> str:
    """Hash the complete private snapshot, including private columns."""
    return hashlib.sha256(_canonical_json(snapshot)).hexdigest()


def _public_snapshot(
    candidates: Sequence[_HistoricalCandidate],
) -> dict[str, list[dict[str, Any]]]:
    """Project only safe identity/status fields for the tracked packet."""
    return {
        "runs": [
            {
                key: candidate.run_row.get(key)
                for key in _PUBLIC_RUN_FIELDS
                if key in candidate.run_row
            }
            for candidate in candidates
        ],
        "tasks": [
            {
                key: task_row.get(key)
                for key in _PUBLIC_TASK_FIELDS
                if key in task_row
            }
            for candidate in candidates
            for task_row in candidate.task_rows
        ],
    }


def _mutation_proposal(
    identity: ProbeIdentity,
    correlation: HistoricalCorrelation,
) -> dict[str, Any]:
    """Build public mutation fields without generating executable SQL."""
    if correlation.status != "unique_match" or correlation.run_id is None:
        return {}
    run_fields: dict[str, str] = {}
    if correlation.bot_request_id is not None:
        run_fields["request_id"] = correlation.bot_request_id
    if correlation.dialogue_id is not None:
        run_fields["dialogue_id"] = correlation.dialogue_id
    return {
        "owner": identity.owner,
        "runs": [{"run_id": correlation.run_id, "fields": run_fields}],
        "tasks": [
            {
                "task_id": task_id,
                "fields": {"run_id": correlation.run_id},
            }
            for task_id in correlation.task_ids
        ],
    }


def _reversal_proposal(
    correlation: HistoricalCorrelation,
    candidate: _HistoricalCandidate | None,
) -> dict[str, Any]:
    """Describe a snapshot-guarded reversal without executable SQL."""
    if candidate is None or correlation.run_id is None:
        return {}
    return {
        "precondition": {
            "snapshot_sha256": correlation.snapshot_sha256,
            "status": "unique_match",
        },
        "runs": [
            {
                "run_id": correlation.run_id,
                "fields": {
                    "request_id": candidate.run_row.get("request_id"),
                    "dialogue_id": candidate.run_row.get("dialogue_id"),
                },
            }
        ],
        "tasks": [
            {
                "task_id": task_row.get("task_id"),
                "fields": {"run_id": task_row.get("run_id")},
            }
            for task_row in candidate.task_rows
        ],
    }


def _packet(
    identity: ProbeIdentity,
    correlation: HistoricalCorrelation,
    candidates: Sequence[_HistoricalCandidate],
    snapshot_location_class: str,
) -> dict[str, Any]:
    """Assemble the sanitized operator packet."""
    unique = candidates[0] if correlation.status == "unique_match" else None
    return {
        "owner": identity.owner,
        "web_request_id": identity.web_request_id,
        "bot_request_id": identity.bot_request_id,
        "dialogue_id": identity.dialogue_id,
        "requested_run_id": identity.run_id,
        "match_count": len(candidates),
        "status": correlation.status,
        "run_id": correlation.run_id,
        "task_ids": list(correlation.task_ids),
        "snapshot_sha256": correlation.snapshot_sha256,
        "snapshot_location_class": snapshot_location_class,
        "pre_write_snapshot": _public_snapshot(candidates),
        "proposed_mutation": _mutation_proposal(identity, correlation),
        "reversal_proposal": _reversal_proposal(correlation, unique),
        "required_l2_approvals": list(_APPROVALS),
        "write_authorized": False,
    }


def _validate_private_snapshot_path(path: Path) -> Path:
    """Require the private snapshot path to be outside this repository."""
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(_REPOSITORY_ROOT)
    except ValueError:
        return resolved
    raise ProbeError("private snapshot path must be outside the repository")


def _write_private_snapshot(path: Path, snapshot: Mapping[str, Any]) -> None:
    """Write the complete private snapshot with restrictive file mode."""
    resolved = _validate_private_snapshot_path(path)
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(_canonical_json(snapshot) + b"\n")
        resolved.chmod(0o600)
    except OSError as exc:
        raise ProbeError("private snapshot could not be written") from exc


def run_probe(
    connection: sqlite3.Connection,
    identity: ProbeIdentity,
    *,
    snapshot_output: Path | None = None,
) -> dict[str, Any]:
    """Read, correlate, hash, and project one registry without writing it."""
    candidates = _load_candidates(connection, identity)
    snapshot = _private_snapshot(candidates)
    snapshot_sha256 = _snapshot_sha256(snapshot) if candidates else None
    records = [candidate.record for candidate in candidates]
    correlation = correlate_historical_analyst(
        owner=identity.owner,
        web_request_id=identity.web_request_id,
        records=records,
        evidence=identity,
        snapshot_sha256=snapshot_sha256,
    )
    location_class = "not_written"
    if snapshot_output is not None and candidates:
        _write_private_snapshot(snapshot_output, snapshot)
        location_class = "operator_protected_path"
    return _packet(identity, correlation, candidates, location_class)


def _write_packet(path: Path, packet: Mapping[str, Any]) -> None:
    """Write canonical, sanitized JSON for operator review."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(
            packet,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        path.write_text(serialized + "\n", encoding="utf-8")
    except OSError as exc:
        raise ProbeError("repair packet could not be written") from exc


def _safe_identifier(value: str | None, name: str) -> str | None:
    """Validate an optional exact identifier without echoing arbitrary text."""
    if value is None:
        return None
    if not _IDENTIFIER.fullmatch(value):
        raise ProbeError(f"invalid {name}")
    return value


def build_parser() -> argparse.ArgumentParser:
    """Build the mutation-free CLI parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Build a read-only, owner-scoped historical Analyst "
            "correlation packet."
        )
    )
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--owner", required=True)
    parser.add_argument(
        "--web-request-id",
        default=HISTORICAL_WEB_REQUEST_ID,
        help="exact historical Web request id",
    )
    parser.add_argument(
        "--bot-request-id",
        help="optional exact Bot request id from independent evidence",
    )
    parser.add_argument(
        "--dialogue-id",
        help="optional exact Bot dialogue id from independent evidence",
    )
    parser.add_argument(
        "--run-id",
        help="optional exact native run id from independent evidence",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--snapshot-output",
        type=Path,
        help="optional protected path outside the repository for private rows",
    )
    return parser


def _identity_from_args(args: argparse.Namespace) -> ProbeIdentity:
    """Validate fixed historical identity and optional exact evidence."""
    owner = _safe_identifier(args.owner, "owner")
    web_request_id = _safe_identifier(
        args.web_request_id,
        "web request id",
    )
    if owner is None or web_request_id is None:
        raise ProbeError("owner and Web request id are required")
    if web_request_id != HISTORICAL_WEB_REQUEST_ID:
        raise ProbeError("only the fixed historical Web request id is allowed")
    return ProbeIdentity(
        owner=owner,
        web_request_id=web_request_id,
        bot_request_id=_safe_identifier(
            args.bot_request_id,
            "Bot request id",
        ),
        dialogue_id=_safe_identifier(args.dialogue_id, "dialogue id"),
        run_id=_safe_identifier(args.run_id, "run id"),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the read-only historical probe and write its safe packet."""
    args = build_parser().parse_args(argv)
    try:
        identity = _identity_from_args(args)
        with open_read_only(args.db) as connection:
            packet = run_probe(
                connection,
                identity,
                snapshot_output=args.snapshot_output,
            )
        _write_packet(args.output, packet)
    except ProbeError as exc:
        print(f"Analyst history probe rejected: {exc}", file=sys.stderr)
        return 2
    print(
        "Analyst history repair packet wrote: "
        f"{packet['status']} (write_authorized=false)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
