# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Offline safety tests for the read-only Analyst history probe."""

from __future__ import annotations

import json
import runpy
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


def _load_probe_module() -> Any:
    """Load the standalone script without packaging ``scripts/``."""
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts/analyst_history_repair_probe.py"
    )
    namespace = runpy.run_path(
        str(path), run_name="analyst_history_repair_probe"
    )
    return SimpleNamespace(**namespace)


probe = _load_probe_module()


@dataclass(frozen=True)
class _RecordOptions:
    """Optional exact identity values for one in-memory candidate."""

    owner: str = "owner-1"
    request_id: str | None = None
    dialogue_id: str | None = None
    tasks_valid: bool = True


def _record(
    run_id: str,
    *,
    options: _RecordOptions | None = None,
) -> Any:
    """Build one correlation candidate with incidental private fields."""
    options = options or _RecordOptions()
    return probe.HistoricalRunRecord(
        owner=options.owner,
        agent="analyst",
        run_id=run_id,
        request_id=options.request_id or probe.HISTORICAL_WEB_REQUEST_ID,
        dialogue_id=options.dialogue_id,
        task_ids=(f"task-{run_id}",),
        tasks_valid=options.tasks_valid,
    )


@pytest.mark.parametrize(
    ("matches", "status"),
    [
        ([], "zero_matches"),
        ([_record("run-1")], "unique_match"),
        ([_record("run-1"), _record("run-2")], "multiple_matches"),
    ],
)
def test_probe_requires_exactly_one_owner_scoped_match(
    matches: list[Any],
    status: str,
) -> None:
    """Zero, one, and multiple exact matches stay non-writable."""
    result = probe.correlate_historical_analyst(
        owner="owner-1",
        web_request_id=probe.HISTORICAL_WEB_REQUEST_ID,
        records=matches,
    )
    assert result.status == status
    assert result.write_authorized is False


def test_probe_excludes_foreign_owner_and_invalid_child_linkage() -> None:
    """Foreign rows and incomplete child linkage never become candidates."""
    result = probe.correlate_historical_analyst(
        owner="owner-1",
        web_request_id=probe.HISTORICAL_WEB_REQUEST_ID,
        records=[
            _record(
                "foreign",
                options=_RecordOptions(owner="owner-2"),
            ),
            _record(
                "invalid",
                options=_RecordOptions(tasks_valid=False),
            ),
        ],
    )
    assert result.status == "zero_matches"
    assert result.run_id is None


def test_probe_ignores_title_and_output_path_matches() -> None:
    """Query text and output paths are never identity evidence."""
    result = probe.correlate_historical_analyst(
        owner="owner-1",
        web_request_id=probe.HISTORICAL_WEB_REQUEST_ID,
        records=[
            _record(
                "title-only",
                options=_RecordOptions(request_id="another-request"),
            ),
        ],
    )
    assert result.status == "zero_matches"
    assert result.task_ids == ()


def test_probe_requires_all_optional_exact_identifiers() -> None:
    """Independent dialogue and run evidence is conjunctive, not fuzzy."""
    record = _record(
        "run-exact",
        options=_RecordOptions(dialogue_id="dialogue-exact"),
    )
    result = probe.correlate_historical_analyst(
        owner="owner-1",
        web_request_id=probe.HISTORICAL_WEB_REQUEST_ID,
        records=[record],
        evidence=probe.ProbeIdentity(
            owner="owner-1",
            web_request_id=probe.HISTORICAL_WEB_REQUEST_ID,
            dialogue_id="dialogue-exact",
            run_id="run-exact",
        ),
    )
    assert result.status == "unique_match"
    assert result.run_id == "run-exact"


def _create_registry(path: Path, *, duplicate: bool = False) -> None:
    """Create a fixture database without invoking the production writers."""
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE runs (
            request_id TEXT NOT NULL,
            run_id TEXT PRIMARY KEY,
            dialogue_id TEXT NOT NULL,
            agent TEXT NOT NULL,
            user_id TEXT NOT NULL,
            status TEXT NOT NULL,
            private_result TEXT,
            private_request TEXT,
            private_query TEXT
        );
        CREATE TABLE tasks (
            run_id TEXT,
            task_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            agent TEXT,
            status TEXT NOT NULL,
            private_task TEXT,
            output_path TEXT
        );
        """)
    runs = [
        (
            probe.HISTORICAL_WEB_REQUEST_ID,
            "run-1",
            "dialogue-1",
            "analyst",
            "owner-1",
            "succeeded",
            "PRIVATE_RESULT",
            "PRIVATE_REQUEST",
            "PRIVATE_QUERY",
        )
    ]
    if duplicate:
        runs.append(
            (
                probe.HISTORICAL_WEB_REQUEST_ID,
                "run-2",
                "dialogue-2",
                "analyst",
                "owner-1",
                "succeeded",
                "PRIVATE_RESULT_2",
                "PRIVATE_REQUEST_2",
                "another-query",
            )
        )
    connection.executemany(
        "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)",
        runs,
    )
    tasks = [
        (
            "run-1",
            "task-1",
            "owner-1",
            "analyst",
            "succeeded",
            "PRIVATE_TASK_RESULT",
            "/private/analyst/output",
        )
    ]
    if duplicate:
        tasks.append(
            (
                "run-2",
                "task-2",
                "owner-1",
                "analyst",
                "succeeded",
                "PRIVATE_TASK_RESULT_2",
                "/private/analyst/output-2",
            )
        )
    connection.executemany(
        "INSERT INTO tasks VALUES (?,?,?,?,?,?,?)",
        tasks,
    )
    connection.commit()
    connection.close()


def test_probe_reads_registry_in_mode_ro_and_writes_no_rows(
    tmp_path: Path,
) -> None:
    """The probe's connection and authorizer both reject mutations."""
    database = tmp_path / "tasks.db"
    _create_registry(database)
    identity = probe.ProbeIdentity(
        owner="owner-1",
        web_request_id=probe.HISTORICAL_WEB_REQUEST_ID,
    )
    with probe.open_read_only(database) as connection:
        packet = probe.run_probe(connection, identity)
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(
                "UPDATE runs SET status = 'failed' WHERE run_id = 'run-1'"
            )
    assert packet["status"] == "unique_match"
    assert packet["write_authorized"] is False
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_probe_emits_hash_and_sanitized_packet_with_private_snapshot(
    tmp_path: Path,
) -> None:
    """Private columns are hashed/stored separately and absent from packet."""
    database = tmp_path / "tasks.db"
    _create_registry(database)
    packet_path = tmp_path / "packet.json"
    snapshot_path = tmp_path / "protected" / "snapshot.json"
    result = probe.main(
        [
            "--db",
            str(database),
            "--owner",
            "owner-1",
            "--output",
            str(packet_path),
            "--snapshot-output",
            str(snapshot_path),
        ]
    )
    assert result == 0
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    rendered = json.dumps(packet)
    for private_value in (
        "PRIVATE_RESULT",
        "PRIVATE_REQUEST",
        "PRIVATE_QUERY",
        "PRIVATE_TASK_RESULT",
        "/private/analyst/output",
    ):
        assert private_value not in rendered
    assert packet["snapshot_sha256"]
    assert packet["snapshot_location_class"] == "operator_protected_path"
    assert packet["proposed_mutation"]["runs"][0]["fields"] == {}
    assert (
        packet["reversal_proposal"]["precondition"]["snapshot_sha256"]
        == packet["snapshot_sha256"]
    )
    assert stat.S_IMODE(snapshot_path.stat().st_mode) == 0o600
    assert "PRIVATE_RESULT" in snapshot_path.read_text(encoding="utf-8")


def test_probe_reports_multiple_matches_without_mutation_proposal(
    tmp_path: Path,
) -> None:
    """Ambiguous exact identity stops before a repair proposal."""
    database = tmp_path / "tasks.db"
    _create_registry(database, duplicate=True)
    packet_path = tmp_path / "packet.json"
    assert (
        probe.main(
            [
                "--db",
                str(database),
                "--owner",
                "owner-1",
                "--output",
                str(packet_path),
            ]
        )
        == 0
    )
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert packet["status"] == "multiple_matches"
    assert packet["match_count"] == 2
    assert packet["run_id"] is None
    assert packet["proposed_mutation"] == {}
    assert packet["reversal_proposal"] == {}


def test_probe_rejects_repository_private_snapshot_and_mutation_flags(
    tmp_path: Path,
) -> None:
    """The CLI cannot authorize writes or place private rows in the repo."""
    database = tmp_path / "tasks.db"
    _create_registry(database)
    parser = probe.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--db",
                str(database),
                "--owner",
                "owner-1",
                "--output",
                str(tmp_path / "packet.json"),
                "--write",
            ]
        )
    assert (
        probe.main(
            [
                "--db",
                str(database),
                "--owner",
                "owner-1",
                "--output",
                str(tmp_path / "packet.json"),
                "--snapshot-output",
                str(Path(__file__).resolve().parents[3] / "private.json"),
            ]
        )
        == 2
    )


def test_probe_requires_fixed_historical_web_identity(
    tmp_path: Path,
) -> None:
    """A caller cannot turn the historical probe into a generic lookup."""
    database = tmp_path / "tasks.db"
    _create_registry(database)
    assert (
        probe.main(
            [
                "--db",
                str(database),
                "--owner",
                "owner-1",
                "--web-request-id",
                "arbitrary-request",
                "--output",
                str(tmp_path / "packet.json"),
            ]
        )
        == 2
    )
