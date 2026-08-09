# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Private SQLite mutations for the Research child dispatch outbox."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from importlib import import_module
from typing import Any, NamedTuple, cast

from ...runtime.sqlite import sqlite_transaction


class SqlContext(NamedTuple):
    """SQL fragments shared by one outbox mutation."""

    db_path: str
    select_sql: str
    parent_sql: str


class ClaimRequest(NamedTuple):
    """Inputs for one compare-and-swap lease claim."""

    sql: SqlContext
    dispatch_id: str
    owner: str
    revision: int
    now_iso: str
    expiry: str


class SentRequest(NamedTuple):
    """Inputs for one verified sent transition."""

    sql: SqlContext
    record: Any
    owner: str
    now_iso: str
    expiry: str


def recovery_ports(options: dict[str, object]) -> dict[str, object]:
    """Normalize recovery adapter ports and remove them from options."""
    return {
        "submit": options.pop("dispatch_submit", None),
        "remote_query": options.pop("dispatch_query", None),
        "local_lookup": options.pop("local_lookup", None),
        "verify": options.pop("dispatch_verify", None),
        "authority_verifier": options.pop("authority_verifier", None),
        "attach_task": options.pop("attach_task", None),
    }


def recovery_outbox(options: dict[str, object], store: Any) -> Any:
    """Construct one recovery outbox when a child submitter is supplied."""
    ports = recovery_ports(options)
    if not callable(ports["submit"]):
        return None
    factory = getattr(
        import_module("mcp_server_phytomni.agents.research.dispatch_outbox"),
        "ResearchDispatchOutbox",
    )
    return factory(store, **cast(Any, ports))


def _canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def is_valid_digest(value: object) -> bool:
    """Return whether a persisted digest has the canonical SHA-256 shape."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.lower()
        and all(character in "0123456789abcdef" for character in value)
    )


def payload_is_consistent(
    payload: object,
    payload_digest: object,
    output_dir: object,
    dispatch_fingerprint: object,
) -> bool:
    """Reject tampered payloads while retaining blank-digest legacy rows."""
    if not payload_digest:
        return True
    if not isinstance(payload, Mapping) or not isinstance(payload_digest, str):
        return False
    if not is_valid_digest(payload_digest) or payload_digest != _digest(
        payload
    ):
        return False
    if "output_dir" not in payload or "dispatch_fingerprint" not in payload:
        return False
    return (
        payload["output_dir"] == output_dir
        and payload["dispatch_fingerprint"] == dispatch_fingerprint
    )


def task_id(value: object) -> str | None:
    """Extract one bounded task identity from a provider response."""
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, Mapping):
        for name in ("task_id", "remote_task_id", "id"):
            candidate = value.get(name)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
    return None


def reusable_task_id(value: object) -> str | None:
    """Reject local task identities in terminal failure states."""
    if isinstance(value, Mapping):
        status = value.get("status")
        if isinstance(status, str) and status.lower() in {
            "failed",
            "error",
            "cancelled",
            "failed_at_agent_level",
        }:
            return None
    return task_id(value)


def binding_values(record: Any) -> tuple[str, str, str, str]:
    """Return canonical private binding columns for one outbox record."""
    payload_json = _canonical_json(record.payload)
    return (
        payload_json,
        _digest(record.payload),
        _canonical_json(list(record.grant_ids)),
        record.snapshot_digest,
    )


def same_dispatch_identity(before: Any, after: Any) -> bool:
    """Keep verifier rotation private to one immutable dispatch identity."""
    return all(
        getattr(before, name) == getattr(after, name)
        for name in (
            "dispatch_id",
            "run_id",
            "child_ordinal",
            "dispatch_fingerprint",
            "state",
            "remote_task_id",
            "revision",
            "output_dir",
            "parent_revision",
        )
    )


def _valid_child_shape(
    fields: tuple[object, ...],
    expected: int,
    seen: Mapping[str, set[str]],
    valid_digest: Callable[[object], bool],
) -> bool:
    """Validate one child identity against its deterministic plan position."""
    ordinal, fingerprint, task_name, output_dir = fields
    return all(
        (
            ordinal == expected,
            valid_digest(fingerprint),
            isinstance(fingerprint, str)
            and fingerprint not in seen["fingerprints"],
            isinstance(task_name, str)
            and task_name.strip()
            and task_name not in seen["task_names"],
            isinstance(output_dir, str)
            and output_dir.strip()
            and output_dir not in seen["output_dirs"],
        )
    )


def plan_children(
    run_id: str,
    prepared: object,
    plan: object,
    valid_digest: Callable[[object], bool],
    error_factory: Callable[[], Exception],
) -> tuple[dict[str, Any], ...]:
    """Validate and project deterministic child rows before one transaction."""
    if not isinstance(run_id, str) or not run_id.strip():
        raise error_factory()
    digest = getattr(plan, "digest", None)
    children = getattr(plan, "children", None)
    if not valid_digest(digest) or not isinstance(children, tuple):
        raise error_factory()
    values: list[dict[str, Any]] = []
    seen: dict[str, set[str]] = {
        key: set() for key in ("fingerprints", "task_names", "output_dirs")
    }

    def project(child: object, expected: int) -> dict[str, Any]:
        fields = tuple(
            getattr(child, name, None)
            for name in (
                "ordinal",
                "dispatch_fingerprint",
                "task_name",
                "output_dir",
            )
        )
        if not _valid_child_shape(fields, expected, seen, valid_digest):
            raise error_factory()
        ordinal, fingerprint, task_name, output_dir = fields
        data = getattr(child, "data_list", None)
        targets = getattr(child, "interop_targets", ())
        if not isinstance(data, Mapping) or not isinstance(targets, tuple):
            raise error_factory()
        seen["fingerprints"].add(cast(str, fingerprint))
        seen["task_names"].add(cast(str, task_name))
        seen["output_dirs"].add(cast(str, output_dir))
        research_grants, resolved_grant_ids = _research_grants(
            prepared, error_factory
        )
        payload = {
            "context": getattr(child, "context", ""),
            "data_list": list(data.items()),
            "dispatch_fingerprint": fingerprint,
            "goal_description": getattr(child, "goal_description", ""),
            "interop_mode": getattr(child, "interop_mode", "off"),
            "interop_targets": targets,
            "ordinal": ordinal,
            "output_dir": output_dir,
            "task_name": task_name,
            "thread_id": getattr(child, "thread_id", ""),
        }
        if research_grants:
            payload["research_grants"] = research_grants
        return {
            "dispatch_id": f"{run_id}:dispatch:{ordinal}",
            "child_ordinal": ordinal,
            "dispatch_fingerprint": fingerprint,
            "payload": payload,
            "output_dir": output_dir,
            "grant_ids": resolved_grant_ids
            or tuple(
                getattr(
                    prepared,
                    "grant_ids",
                    getattr(prepared, "authority_ids", ()),
                )
            ),
            "snapshot_digest": getattr(prepared, "inventory_digest", ""),
            "policy_digest": digest,
        }

    for expected, child in enumerate(children):
        values.append(project(child, expected))
    if not values:
        raise error_factory()
    return tuple(values)


def _research_grants(
    prepared: object, error_factory: Callable[[], Exception]
) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    """Project exact references and snapshots into a private child payload."""
    bindings = getattr(prepared, "authorities", ())
    if not bindings:
        return (), ()
    if not isinstance(bindings, tuple):
        raise error_factory()
    grants: list[dict[str, Any]] = []
    grant_ids: list[str] = []
    for binding in bindings:
        authority = getattr(binding, "authority", None)
        snapshot = getattr(authority, "snapshot", None)
        values = {
            "dataset_id": getattr(binding, "dataset_id", None),
            "exact_reference": getattr(binding, "exact_reference", None),
            "compound_suffix": getattr(binding, "compound_suffix", None),
            "grant_id": getattr(authority, "authority_id", None),
            "snapshot_digest": getattr(snapshot, "snapshot_digest", None),
        }
        if not all(
            isinstance(value, str) and value for value in values.values()
        ):
            raise error_factory()
        grants.append(
            {
                **values,
                "snapshot": {
                    name: getattr(snapshot, name)
                    for name in (
                        "dataset_id",
                        "size_bytes",
                        "etag",
                        "version_id",
                        "last_modified",
                        "placeholder",
                        "snapshot_digest",
                    )
                },
            }
        )
        grant_ids.append(cast(str, values["grant_id"]))
    return tuple(grants), tuple(grant_ids)


def existing_plan_rows(
    connection: sqlite3.Connection,
    run_id: str,
    children: Sequence[Mapping[str, Any]],
    error_factory: Callable[[], Exception],
) -> tuple[dict[str, Any], ...] | None:
    """Return replay rows only when every durable child field agrees."""
    ids = tuple(str(child.get("dispatch_id", "")) for child in children)
    if len(ids) != len(set(ids)) or any(not item for item in ids):
        raise error_factory()
    placeholders = ",".join("?" for _ in ids)
    rows = connection.execute(
        "SELECT outbox_id, run_id, child_ordinal, dispatch_fingerprint, "
        "state, remote_task_id, revision, lease_expires_at, payload_json, "
        "output_dir, grant_ids_json, snapshot_digest, parent_revision "
        "FROM research_dispatch_outbox WHERE run_id=? "
        f"AND outbox_id IN ({placeholders}) ORDER BY child_ordinal",
        (run_id, *ids),
    ).fetchall()
    if len(rows) != len(children):
        return None
    for child, row in zip(children, rows):
        if (
            int(row["child_ordinal"]),
            row["dispatch_fingerprint"],
            row["payload_json"],
            row["output_dir"],
            row["grant_ids_json"],
            row["snapshot_digest"],
        ) != (
            int(child["child_ordinal"]),
            child["dispatch_fingerprint"],
            _canonical_json(child["payload"]),
            child["output_dir"],
            _canonical_json(list(child.get("grant_ids", ()))),
            child.get("snapshot_digest", ""),
        ):
            return None
    return tuple(dict(row) for row in rows)


def insert_plan_row(
    connection: sqlite3.Connection,
    values: Mapping[str, Any],
    valid_digest: Callable[[object], bool],
    error_factory: Callable[[], Exception],
) -> None:
    """Validate and insert one child through the low-level storage helper."""
    run_id = values["run_id"]
    plan_digest = values["plan_digest"]
    child = values["child"]
    now = values["now"]
    parent_revision = values["parent_revision"]
    if not isinstance(run_id, str) or not isinstance(plan_digest, str):
        raise error_factory()
    if not isinstance(child, Mapping):
        raise error_factory()
    dispatch_id = child.get("dispatch_id")
    fingerprint = child.get("dispatch_fingerprint")
    payload = child.get("payload")
    if (
        not isinstance(dispatch_id, str)
        or not dispatch_id
        or not valid_digest(fingerprint)
        or not isinstance(payload, Mapping)
    ):
        raise error_factory()
    try:
        payload_json = _canonical_json(payload)
        grant_json = _canonical_json(list(child.get("grant_ids", ())))
    except (TypeError, ValueError, OverflowError) as error:
        raise error_factory() from error
    if len(payload_json) > 1_048_576:
        raise error_factory()
    values = dict(child)
    values.update(
        run_id=run_id,
        policy_digest=child.get("policy_digest", plan_digest),
        payload_json=payload_json,
        payload_digest=_digest(payload),
        grant_ids_json=grant_json,
        now=now,
        parent_revision=parent_revision,
    )
    insert_dispatch_row(connection, values)


def attach_task(
    connection: sqlite3.Connection,
    run_id: str,
    fingerprint: str,
    child_task_id: str,
    output_dir: str,
) -> bool:
    """Attach one accepted child to the local task registry in the CAS tx."""
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
    }
    required = {
        "task_id",
        "status",
        "output_dir",
        "run_id",
        "input_fingerprint",
    }
    if not required.issubset(columns):
        return False
    existing = connection.execute(
        "SELECT run_id FROM tasks WHERE task_id=?", (child_task_id,)
    ).fetchone()
    if existing is not None and existing[0] not in (None, run_id):
        return False
    connection.execute(
        "INSERT INTO tasks(task_id,status,analysis_id,output_dir,run_id,"
        "input_fingerprint) VALUES(?,?,?,?,?,?) ON CONFLICT(task_id) DO "
        "UPDATE SET run_id=excluded.run_id, output_dir=excluded.output_dir, "
        "input_fingerprint=COALESCE(excluded.input_fingerprint,"
        "tasks.input_fingerprint)",
        (child_task_id, "submitted", "", output_dir, run_id, fingerprint),
    )
    return True


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
        "parent_revision, payload_json, output_dir, grant_ids_json, "
        "snapshot_digest, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, 'pending', "
        "0, 0, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            dispatch_id,
            run_id,
            dispatch_id,
            values["payload_digest"],
            values["child_ordinal"],
            fingerprint,
            values.get("parent_revision", 0),
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


def claim_row(request: ClaimRequest) -> bool:
    """CAS-claim one pending or expired leased outbox row."""
    sql = request.sql
    with sqlite_transaction(sql.db_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            sql.select_sql, (request.dispatch_id,)
        ).fetchone()
        if row is None or not _parent_live(connection, row["run_id"]):
            return False
        state, stored_expiry = row["state"], row["lease_expires_at"]
        if state not in {"pending", "leased"} or (
            state == "leased"
            and stored_expiry is not None
            and stored_expiry > request.now_iso
        ):
            return False
        changed = connection.execute(
            "UPDATE research_dispatch_outbox SET state='leased', "
            "lease_owner=?, lease_expires_at=?, attempt=attempt+1, "
            "updated_at=?, revision=revision+1 WHERE outbox_id=? "
            "AND revision=? AND state IN ('pending','leased') AND "
            "(state='pending' OR lease_expires_at <= ?) AND " + sql.parent_sql,
            (
                request.owner,
                request.expiry,
                request.now_iso,
                request.dispatch_id,
                request.revision,
                request.now_iso,
            ),
        )
    return changed.rowcount == 1


def mark_sent(request: SentRequest) -> bool:
    """Persist a verified binding and mark a claimed row sent atomically."""
    sql = request.sql
    record = request.record
    payload_json, payload_digest, grant_json, snapshot_digest = binding_values(
        record
    )
    with sqlite_transaction(sql.db_path) as connection:
        changed = connection.execute(
            "UPDATE research_dispatch_outbox SET state='sent', "
            "lease_owner=?, lease_expires_at=?, sent_at=?, updated_at=?, "
            "revision=revision+1, payload_json=?, payload_digest=?, "
            "grant_ids_json=?, snapshot_digest=? WHERE outbox_id=? "
            "AND lease_owner=? AND revision=? AND state='leased' "
            "AND lease_expires_at IS NOT NULL AND lease_expires_at > ? "
            "AND " + sql.parent_sql,
            (
                request.owner,
                request.expiry,
                request.now_iso,
                request.now_iso,
                payload_json,
                payload_digest,
                grant_json,
                snapshot_digest,
                record.dispatch_id,
                request.owner,
                record.revision,
                request.now_iso,
            ),
        )
    return changed.rowcount == 1


def _parent_live(connection: sqlite3.Connection, run_id: str) -> bool:
    """Check the parent row while the claim transaction is open."""
    row = connection.execute(
        "SELECT status, COALESCE((SELECT cancel_requested FROM "
        "research_input_resolutions WHERE run_id = runs.run_id),0) "
        "FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return (
        row is not None
        and row[0]
        not in {
            "succeeded",
            "failed",
            "cancelled",
        }
        and not bool(row[1])
    )


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
