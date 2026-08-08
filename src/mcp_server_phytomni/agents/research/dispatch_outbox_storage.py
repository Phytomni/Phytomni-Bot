# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Private SQLite mutations for the Research child dispatch outbox."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from ...runtime.sqlite import sqlite_transaction


def insert_dispatch_row(
    connection: sqlite3.Connection, values: Mapping[str, Any]
) -> None:
    """Insert one already-validated work unit and its outbox row."""
    dispatch_id = values["dispatch_id"]
    run_id = values["run_id"]
    fingerprint = values["dispatch_fingerprint"]
    connection.execute(
        "INSERT OR IGNORE INTO research_work_units "
        "(unit_id, run_id, kind, state, "
        "input_digest, policy_digest, attempt, revision, schema_version, "
        "execution_fingerprint, evidence_digest, created_at, updated_at) "
        "VALUES (?, ?, 'dispatch', 'pending', ?, ?, 0, 0, 1, ?, ?, ?, ?)",
        (
            dispatch_id,
            run_id,
            fingerprint,
            values["policy_digest"],
            values.get("execution_fingerprint", ""),
            values.get("evidence_digest", ""),
            values["now"],
            values["now"],
        ),
    )
    work = connection.execute(
        "SELECT run_id, kind FROM research_work_units WHERE unit_id = ?",
        (dispatch_id,),
    ).fetchone()
    if work is None or tuple(work) != (run_id, "dispatch"):
        raise sqlite3.IntegrityError("research dispatch work collision")
    connection.execute(
        "INSERT OR IGNORE INTO research_dispatch_outbox "
        "(outbox_id, run_id, unit_id, payload_digest, state, attempt, "
        "revision, schema_version, child_ordinal, dispatch_fingerprint, "
        "payload_json, output_dir, grant_ids_json, snapshot_digest, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, 'pending', "
        "0, 0, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            dispatch_id,
            run_id,
            dispatch_id,
            values["payload_digest"],
            values["child_ordinal"],
            fingerprint,
            values["payload_json"],
            values.get("output_dir", ""),
            values["grant_ids_json"],
            values.get("snapshot_digest", ""),
            values["now"],
            values["now"],
        ),
    )
    outbox = connection.execute(
        "SELECT run_id, child_ordinal, dispatch_fingerprint, payload_digest "
        "FROM research_dispatch_outbox WHERE outbox_id = ?",
        (dispatch_id,),
    ).fetchone()
    expected = (
        run_id,
        values["child_ordinal"],
        fingerprint,
        values["payload_digest"],
    )
    if outbox is None or tuple(outbox) != expected:
        raise sqlite3.IntegrityError("research dispatch outbox collision")


def mark_row(
    db_path: str,
    record: Any,
    now_iso: str,
    options: tuple[str, str, tuple[str, ...]],
) -> bool:
    """CAS-mark one outbox row with a bounded terminal state."""
    state, failure, allowed = options
    placeholders = ",".join("?" for _ in allowed)
    with sqlite_transaction(db_path) as connection:
        cursor = connection.execute(
            "UPDATE research_dispatch_outbox SET state=?, "
            "lease_owner=NULL, lease_expires_at=NULL, failure_code=?, "
            "failure_retryable=0, updated_at=?, completed_at=?, "
            "revision=revision+1 WHERE outbox_id=? AND revision=? "
            "AND state IN (" + placeholders + ")",
            (
                state,
                failure,
                now_iso,
                now_iso,
                record.dispatch_id,
                record.revision,
                *allowed,
            ),
        )
    return cursor.rowcount == 1
