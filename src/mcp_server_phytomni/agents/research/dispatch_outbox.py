# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Durable, mark-before-send dispatch for validated Research children."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import sqlite3
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from sqlite3 import Row
from typing import Any, Literal, cast

from ...runtime.research_input_store import ResearchInputStore
from ...runtime.sqlite import sqlite_transaction
from . import dispatch_outbox_storage as _storage
from .input_contracts import ResearchErrorCode, ResearchInputFailure

__all__ = [
    "ResearchDispatchDisposition",
    "ResearchDispatchOutbox",
    "ResearchDispatchRecord",
    "ResearchPlanCommitError",
    "recover_dispatch_outbox",
    "persist_plan_and_outbox",
]

DispatchState = Literal[
    "pending", "leased", "sent", "accepted", "ambiguous", "cancelled"
]
DispositionState = Literal["accepted", "reconciled", "ambiguous", "cancelled"]
Submitter = Callable[["ResearchDispatchRecord"], object]
Lookup = Callable[["ResearchDispatchRecord"], object]
Verifier = Callable[["ResearchDispatchRecord"], object]
Clock = Callable[[], datetime]

_TRACKING_FAILED: ResearchErrorCode = "research_run_tracking_failed"
_UNAVAILABLE: ResearchErrorCode = "research_input_resolution_unavailable"
_TERMINAL = ("succeeded", "failed", "cancelled")
_LEASE = timedelta(seconds=60)
_MAX_PAYLOAD_CHARS = 1_048_576
_DIGEST_SIZE = 64
_OUTBOX_SELECT = "SELECT * FROM research_dispatch_outbox WHERE outbox_id = ?"
_OUTBOX_FAILURES = (Exception,)
_CANCEL_OPTIONS = ("cancelled", _UNAVAILABLE, ("pending", "leased"))
_AMBIGUOUS_OPTIONS = ("ambiguous", _TRACKING_FAILED, ("sent",))


@dataclass(frozen=True, slots=True)
class ResearchDispatchRecord:
    """Stable identity and CAS state for one deterministic child."""

    dispatch_id: str
    run_id: str
    child_ordinal: int
    dispatch_fingerprint: str
    state: DispatchState
    remote_task_id: str | None
    revision: int


@dataclass(frozen=True, slots=True)
class ResearchDispatchDisposition:
    """Safe public-free result of one outbox operation."""

    dispatch_id: str
    state: DispositionState
    remote_task_id: str | None
    failure_code: ResearchErrorCode | None


class ResearchPlanCommitError(ResearchInputFailure):
    """Stable planning failure raised before or during local enqueue."""

    def __init__(self) -> None:
        super().__init__(
            code="research_input_resolution_failed",
            safe_message="Research input resolution failed.",
            http_status_hint=422,
            stage="planning",
            retryable=False,
        )


@dataclass(frozen=True, slots=True)
class _OutboxRow:
    record: ResearchDispatchRecord
    payload: Mapping[str, Any]
    output_dir: str
    grant_ids: tuple[str, ...]
    snapshot_digest: str


@dataclass(frozen=True, slots=True)
class _OutboxOptions:
    submit: Submitter | None = None
    remote_query: Lookup | None = None
    local_lookup: Lookup | None = None
    verify: Verifier | None = None
    attach_task: Callable[[ResearchDispatchRecord, str], object] | None = None
    now: Clock = lambda: datetime.now(UTC)
    lease_ttl: timedelta = _LEASE


@dataclass(frozen=True, slots=True)
class _EnqueuePayload:
    projection_json: str
    plan_digest: str
    children: tuple[Mapping[str, Any], ...]
    execution_fingerprint: str
    evidence_digest: str


def persist_plan_and_outbox(
    store: ResearchInputStore,
    run_id: str,
    expected_revision: int,
    prepared: object,
    plan: object,
) -> tuple[ResearchDispatchRecord, ...]:
    """Atomically persist final projection, plan identity, and child rows."""
    children = _validated_children(run_id, prepared, plan)
    final_projection = _final_projection(prepared)
    result = store.persist_plan_and_outbox(
        run_id,
        expected_revision,
        final_projection=final_projection,
        plan_digest=cast(str, getattr(plan, "digest")),
        children=children,
        execution_fingerprint=cast(
            str, getattr(prepared, "execution_fingerprint", "")
        ),
        evidence_digest=cast(str, getattr(prepared, "evidence_digest", "")),
    )
    if result is None:
        raise ResearchPlanCommitError()
    return tuple(_record_from_mapping(row) for row in result)


def _persist_plan_in_store(
    store: ResearchInputStore,
    run_id: str,
    expected_revision: int,
    **values: object,
) -> tuple[dict[str, Any], ...] | None:
    payload = _enqueue_payload(values, expected_revision)
    return _commit_plan(store, run_id, expected_revision, payload)


def _enqueue_payload(
    values: Mapping[str, object], expected_revision: int
) -> _EnqueuePayload:
    projection = values.get("final_projection")
    plan_digest = values.get("plan_digest")
    children = values.get("children")
    execution = values.get("execution_fingerprint", "")
    evidence = values.get("evidence_digest", "")
    if not _valid_enqueue_values(values, expected_revision):
        raise ResearchPlanCommitError()
    projection_json = _canonical_json(projection)
    if not children or len(projection_json) > _MAX_PAYLOAD_CHARS:
        raise ResearchPlanCommitError()
    return _EnqueuePayload(
        projection_json,
        cast(str, plan_digest),
        tuple(
            cast(Mapping[str, Any], child)
            for child in cast(Sequence[object], children)
        ),
        cast(str, execution),
        cast(str, evidence),
    )


def _valid_enqueue_values(
    values: Mapping[str, object], revision: object
) -> bool:
    if (
        not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 0
    ):
        return False
    projection = values.get("final_projection")
    digest = values.get("plan_digest")
    children = values.get("children")
    if not isinstance(projection, Mapping) or not isinstance(digest, str):
        return False
    if not isinstance(children, Sequence) or isinstance(
        children, (str, bytes)
    ):
        return False
    return all(
        isinstance(values.get(name, ""), str)
        for name in ("execution_fingerprint", "evidence_digest")
    )


def _commit_plan(
    store: ResearchInputStore,
    run_id: str,
    expected_revision: int,
    payload: _EnqueuePayload,
) -> tuple[dict[str, Any], ...] | None:
    now = _iso(datetime.now(UTC))
    with sqlite_transaction(store.db_path) as connection:
        connection.row_factory = Row
        connection.execute("BEGIN IMMEDIATE")
        parent = connection.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        resolution = connection.execute(
            "SELECT revision, status, cancel_requested, plan_digest FROM "
            "research_input_resolutions WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if (
            parent is None
            or resolution is None
            or parent[0] in _TERMINAL
            or resolution[2]
        ):
            return None
        if (
            resolution[3] == payload.plan_digest
            and resolution[1] == "planning"
        ):
            return _existing_enqueue_rows(connection, run_id, payload.children)
        if resolution[0] != expected_revision:
            return None
        updated = connection.execute(
            "UPDATE research_input_resolutions SET status='planning', "
            "final_projection_json=?, plan_digest=?, evidence_digest=CASE "
            "WHEN ? <> '' THEN ? ELSE evidence_digest END, "
            "execution_fingerprint=CASE WHEN ? <> '' THEN ? ELSE "
            "execution_fingerprint END, last_stage='planning', updated_at=?, "
            "revision=revision+1 WHERE run_id=? AND revision=? AND "
            "COALESCE(cancel_requested,0)=0",
            (
                payload.projection_json,
                payload.plan_digest,
                payload.evidence_digest,
                payload.evidence_digest,
                payload.execution_fingerprint,
                payload.execution_fingerprint,
                now,
                run_id,
                expected_revision,
            ),
        )
        if updated.rowcount != 1:
            return None
        parent_updated = connection.execute(
            "UPDATE runs SET stage='planning', revision=revision+1, "
            "updated_at=? WHERE run_id=? AND status NOT IN "
            "('succeeded','failed','cancelled')",
            (now, run_id),
        )
        if parent_updated.rowcount != 1:
            raise sqlite3.IntegrityError("research parent changed")
        for child in payload.children:
            _insert_dispatch_row(
                connection, run_id, payload.plan_digest, child, now
            )
        return _existing_enqueue_rows(connection, run_id, payload.children)


def _existing_enqueue_rows(
    connection: sqlite3.Connection,
    run_id: str,
    children: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...] | None:
    ids = tuple(cast(str, child.get("dispatch_id")) for child in children)
    if len(ids) != len(set(ids)) or any(not item for item in ids):
        raise ResearchPlanCommitError()
    placeholders = ",".join("?" for _ in ids)
    rows = connection.execute(
        "SELECT outbox_id, run_id, child_ordinal, dispatch_fingerprint, "
        "state, "
        "remote_task_id, revision FROM research_dispatch_outbox "
        "WHERE run_id=? "
        f"AND outbox_id IN ({placeholders}) ORDER BY child_ordinal",
        (run_id, *ids),
    ).fetchall()
    if len(rows) != len(children):
        return None
    for child, row in zip(children, rows):
        if (int(row["child_ordinal"]), row["dispatch_fingerprint"]) != (
            int(child["child_ordinal"]),
            child["dispatch_fingerprint"],
        ):
            return None
    return tuple(dict(row) for row in rows)


def _insert_dispatch_row(
    connection: sqlite3.Connection,
    run_id: str,
    plan_digest: str,
    child: Mapping[str, Any],
    now: str,
) -> None:
    dispatch_id = child.get("dispatch_id")
    fingerprint = child.get("dispatch_fingerprint")
    payload = child.get("payload")
    if not isinstance(dispatch_id, str) or not dispatch_id:
        raise ResearchPlanCommitError()
    if not _valid_digest(fingerprint) or not isinstance(payload, Mapping):
        raise ResearchPlanCommitError()
    payload_json = _canonical_json(payload)
    if len(payload_json) > _MAX_PAYLOAD_CHARS:
        raise ResearchPlanCommitError()
    values = dict(child)
    values.update(
        run_id=run_id,
        policy_digest=child.get("policy_digest", plan_digest),
        payload_json=payload_json,
        payload_digest=_digest(payload),
        grant_ids_json=_canonical_json(list(child.get("grant_ids", ()))),
        now=now,
    )
    _storage.insert_dispatch_row(connection, values)


class ResearchDispatchOutbox:
    """Claim, verify, mark, submit, and reconcile one child safely."""

    def __init__(
        self,
        store: ResearchInputStore,
        *,
        options: _OutboxOptions | None = None,
        **port_options: object,
    ) -> None:
        """Bind storage and optional direct/relay provider ports."""
        if options is not None and port_options:
            raise TypeError("options cannot be combined with port options")
        self.store = store
        self.options = options or _OutboxOptions(**cast(Any, port_options))

    def load(self, dispatch_id: str) -> ResearchDispatchRecord:
        """Load one public-free row or raise a bounded key error."""
        row = _load_row(self.store, dispatch_id)
        if row is None:
            raise KeyError(dispatch_id)
        return row.record

    def heartbeat(
        self,
        dispatch_id: str,
        lease_owner: str,
        revision: int,
        now: datetime | None = None,
    ) -> ResearchDispatchRecord | None:
        """Renew one leased/sent outbox row with owner and revision CAS."""
        timestamp = now or self.options.now()
        expiry = _iso(timestamp + self.options.lease_ttl)
        updated = _iso(timestamp)
        with sqlite_transaction(self.store.db_path) as connection:
            connection.row_factory = Row
            changed = connection.execute(
                "UPDATE research_dispatch_outbox SET lease_expires_at=?, "
                "updated_at=?, revision=revision+1 WHERE outbox_id=? "
                "AND lease_owner=? AND revision=? AND state IN "
                "('leased','sent') "
                "AND " + _parent_live_sql(),
                (expiry, updated, dispatch_id, lease_owner, revision),
            )
        return self.load(dispatch_id) if changed.rowcount == 1 else None

    async def dispatch_once(
        self, dispatch_id: str, lease_owner: str
    ) -> ResearchDispatchDisposition:
        """Verify, mark sent, submit once, and CAS-accept one child."""
        row = _load_row(self.store, dispatch_id)
        terminal = _terminal_disposition(dispatch_id, row)
        if terminal is not None:
            return terminal
        assert row is not None
        if not _parent_live(self.store, row.record.run_id):
            _mark_row(
                self.store, row.record, self.options.now(), _CANCEL_OPTIONS
            )
            return _cancelled(dispatch_id)
        known = await self._lookup_local(row)
        if known:
            return self._accept_known(row, known)
        return await self._dispatch_claimed(row, dispatch_id, lease_owner)

    async def _dispatch_claimed(
        self, row: _OutboxRow, dispatch_id: str, lease_owner: str
    ) -> ResearchDispatchDisposition:
        claimed = _claim_row(
            self.store,
            row.record,
            lease_owner,
            self.options.now(),
            self.options.lease_ttl,
        )
        if claimed is None:
            return await self._after_claim_miss(dispatch_id, lease_owner)
        if not await self._verify_row(claimed):
            _mark_row(
                self.store,
                claimed.record,
                self.options.now(),
                _AMBIGUOUS_OPTIONS,
            )
            return _ambiguous(dispatch_id)
        sent = _mark_sent(
            self.store,
            claimed,
            lease_owner,
            self.options.now(),
            self.options.lease_ttl,
        )
        if sent is None:
            return _ambiguous(dispatch_id)
        return await self._submit_sent(sent, dispatch_id)

    async def _after_claim_miss(
        self, dispatch_id: str, lease_owner: str
    ) -> ResearchDispatchDisposition:
        latest = _load_row(self.store, dispatch_id)
        terminal = _terminal_disposition(dispatch_id, latest)
        if terminal is not None:
            return terminal
        if latest is not None and latest.record.state == "sent":
            return await self.reconcile_once(dispatch_id, lease_owner)
        return _ambiguous(dispatch_id)

    async def _submit_sent(
        self, sent: _OutboxRow, dispatch_id: str
    ) -> ResearchDispatchDisposition:
        try:
            response = await _maybe_await(
                self.options.submit(sent.record)
                if self.options.submit is not None
                else None
            )
        except _OUTBOX_FAILURES:
            queried = await self._query_remote(sent)
            if queried is not None:
                return self._accept_known(sent, queried)
            _mark_row(
                self.store, sent.record, self.options.now(), _AMBIGUOUS_OPTIONS
            )
            return _ambiguous(dispatch_id)
        task_id = _task_id(response)
        if task_id is None:
            _mark_row(
                self.store, sent.record, self.options.now(), _AMBIGUOUS_OPTIONS
            )
            return _ambiguous(dispatch_id)
        return self._accept_known(sent, task_id)

    def _accept_known(
        self, row: _OutboxRow, task_id: str
    ) -> ResearchDispatchDisposition:
        return _accept_row(
            self.store,
            row.record,
            task_id,
            self.options.now(),
            self.options.attach_task,
        )

    async def reconcile_once(
        self, dispatch_id: str, lease_owner: str
    ) -> ResearchDispatchDisposition:
        """Resolve a sent row without ever resubmitting it."""
        row = _load_row(self.store, dispatch_id)
        terminal = _terminal_disposition(dispatch_id, row)
        if terminal is not None:
            return terminal
        assert row is not None
        if row.record.state != "sent":
            return await self.dispatch_once(dispatch_id, lease_owner)
        return await self._reconcile_sent(row, dispatch_id)

    async def _reconcile_sent(
        self, row: _OutboxRow, dispatch_id: str
    ) -> ResearchDispatchDisposition:
        if not _parent_live(self.store, row.record.run_id):
            _mark_row(
                self.store, row.record, self.options.now(), _CANCEL_OPTIONS
            )
            return _cancelled(dispatch_id)
        known = await self._lookup_local(row)
        if known:
            return self._accept_known(row, known)
        if not await self._verify_row(row):
            _mark_row(
                self.store, row.record, self.options.now(), _AMBIGUOUS_OPTIONS
            )
            return _ambiguous(dispatch_id)
        queried = await self._query_remote(row)
        if queried is None:
            _mark_row(
                self.store, row.record, self.options.now(), _AMBIGUOUS_OPTIONS
            )
            return _ambiguous(dispatch_id)
        return self._accept_known(row, queried)

    async def _lookup_local(self, row: _OutboxRow) -> str | None:
        if self.options.local_lookup is not None:
            return _task_id(
                await _maybe_await(self.options.local_lookup(row.record))
            )
        with sqlite_transaction(self.store.db_path) as connection:
            found = connection.execute(
                "SELECT task_id FROM tasks WHERE run_id = ? AND "
                "input_fingerprint = ? ORDER BY created_at, task_id LIMIT 1",
                (row.record.run_id, row.record.dispatch_fingerprint),
            ).fetchone()
        return None if found is None else cast(str, found[0])

    async def _query_remote(self, row: _OutboxRow) -> str | None:
        if self.options.remote_query is None:
            return None
        try:
            response = await _maybe_await(
                self.options.remote_query(row.record)
            )
        except _OUTBOX_FAILURES:
            return None
        return _task_id(response)

    async def _verify_row(self, row: _OutboxRow) -> bool:
        if self.options.verify is None:
            return True
        try:
            result = await _maybe_await(self.options.verify(row.record))
        except _OUTBOX_FAILURES:
            return False
        return result is not False


async def recover_dispatch_outbox(
    outbox: ResearchDispatchOutbox,
    now: datetime,
    limit: int,
    lease_owner: str,
) -> tuple[DispositionState, ...]:
    """Reconcile a bounded pending/sent outbox set after a restart."""
    if limit <= 0:
        return ()
    now_iso = _iso(now)
    with sqlite_transaction(outbox.store.db_path) as connection:
        rows = connection.execute(
            "SELECT outbox_id FROM research_dispatch_outbox "
            "WHERE state='pending' "
            "OR (state='sent' AND lease_expires_at IS NOT NULL AND "
            "lease_expires_at <= ?) ORDER BY updated_at, outbox_id LIMIT ?",
            (now_iso, limit),
        ).fetchall()
    outcomes: list[DispositionState] = []
    for row in rows:
        dispatch_id = cast(str, row[0])
        current = _load_row(outbox.store, dispatch_id)
        if current is None:
            continue
        if current.record.state == "sent":
            disposition = await outbox.reconcile_once(dispatch_id, lease_owner)
        else:
            disposition = await outbox.dispatch_once(dispatch_id, lease_owner)
        outcomes.append(disposition.state)
    return tuple(outcomes)


def _validated_children(
    run_id: str, prepared: object, plan: object
) -> tuple[dict[str, Any], ...]:
    if not isinstance(run_id, str) or not run_id.strip():
        raise ResearchPlanCommitError()
    digest = getattr(plan, "digest", None)
    children = getattr(plan, "children", None)
    if not _valid_digest(digest) or not isinstance(children, tuple):
        raise ResearchPlanCommitError()
    values: list[dict[str, Any]] = []
    seen = cast(
        dict[str, set[str]],
        {key: set() for key in ("fingerprints", "task_names", "output_dirs")},
    )
    for expected, child in enumerate(children):
        ordinal, fingerprint, task_name, output_dir = _child_identity(
            child, expected, seen
        )
        data = getattr(child, "data_list", None)
        targets = getattr(child, "interop_targets", ())
        if not isinstance(data, Mapping) or not isinstance(targets, tuple):
            raise ResearchPlanCommitError()
        values.append(
            {
                "dispatch_id": f"{run_id}:dispatch:{ordinal}",
                "child_ordinal": ordinal,
                "dispatch_fingerprint": fingerprint,
                "payload": {
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
                },
                "output_dir": output_dir,
                "grant_ids": tuple(),
                "snapshot_digest": getattr(prepared, "inventory_digest", ""),
                "policy_digest": cast(str, digest),
            }
        )
    if not values:
        raise ResearchPlanCommitError()
    return tuple(values)


def _child_identity(
    child: object,
    expected: int,
    seen: dict[str, set[str]],
) -> tuple[int, str, str, str]:
    ordinal = getattr(child, "ordinal", None)
    fingerprint = getattr(child, "dispatch_fingerprint", None)
    task_name = getattr(child, "task_name", None)
    output_dir = getattr(child, "output_dir", None)
    fingerprints = seen["fingerprints"]
    task_names = seen["task_names"]
    output_dirs = seen["output_dirs"]
    if ordinal != expected or not _valid_digest(fingerprint):
        raise ResearchPlanCommitError()
    if not _valid_child_fields(fingerprint, task_name, output_dir, seen):
        raise ResearchPlanCommitError()
    fingerprints.add(cast(str, fingerprint))
    task_names.add(cast(str, task_name))
    output_dirs.add(cast(str, output_dir))
    return (
        cast(int, ordinal),
        cast(str, fingerprint),
        cast(str, task_name),
        cast(str, output_dir),
    )


def _valid_child_fields(
    fingerprint: object,
    task_name: object,
    output_dir: object,
    seen: dict[str, set[str]],
) -> bool:
    fingerprints = seen["fingerprints"]
    task_names = seen["task_names"]
    output_dirs = seen["output_dirs"]
    if fingerprint in fingerprints:
        return False
    if not isinstance(task_name, str) or not task_name.strip():
        return False
    if task_name in task_names:
        return False
    if not isinstance(output_dir, str) or not output_dir.strip():
        return False
    return output_dir not in output_dirs


def _final_projection(prepared: object) -> dict[str, Any]:
    query = getattr(prepared, "effective_query", None)
    documents = getattr(prepared, "obs_file_list", None)
    data = getattr(prepared, "data_list", None)
    if not isinstance(query, str) or not isinstance(documents, tuple):
        raise ResearchPlanCommitError()
    if not isinstance(data, Mapping):
        raise ResearchPlanCommitError()
    return {
        "authority_ids": tuple(getattr(prepared, "authority_ids", ())),
        "data_list": list(data.items()),
        "effective_query": query,
        "execution_fingerprint": getattr(
            prepared, "execution_fingerprint", ""
        ),
        "inventory_digest": getattr(prepared, "inventory_digest", ""),
        "obs_file_list": documents,
        "schema_version": 1,
    }


def _load_row(
    store: ResearchInputStore, dispatch_id: str
) -> _OutboxRow | None:
    with sqlite_transaction(store.db_path) as connection:
        connection.row_factory = Row
        row = connection.execute(_OUTBOX_SELECT, (dispatch_id,)).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"] or "{}")
        grants = json.loads(row["grant_ids_json"] or "[]")
    except (TypeError, ValueError):
        payload, grants = {}, []
    if not isinstance(payload, Mapping) or not isinstance(grants, list):
        payload, grants = {}, []
    return _OutboxRow(
        record=ResearchDispatchRecord(
            dispatch_id=row["outbox_id"],
            run_id=row["run_id"],
            child_ordinal=int(row["child_ordinal"]),
            dispatch_fingerprint=row["dispatch_fingerprint"],
            state=cast(DispatchState, row["state"]),
            remote_task_id=row["remote_task_id"],
            revision=int(row["revision"]),
        ),
        payload=payload,
        output_dir=row["output_dir"] or "",
        grant_ids=tuple(item for item in grants if isinstance(item, str)),
        snapshot_digest=row["snapshot_digest"] or "",
    )


def _record_from_mapping(row: Mapping[str, Any]) -> ResearchDispatchRecord:
    return ResearchDispatchRecord(
        dispatch_id=cast(str, row.get("dispatch_id", row.get("outbox_id"))),
        run_id=cast(str, row["run_id"]),
        child_ordinal=int(row["child_ordinal"]),
        dispatch_fingerprint=cast(str, row["dispatch_fingerprint"]),
        state=cast(DispatchState, row["state"]),
        remote_task_id=cast(str | None, row.get("remote_task_id")),
        revision=int(row["revision"]),
    )


def _claim_row(
    store: ResearchInputStore,
    record: ResearchDispatchRecord,
    owner: str,
    now: datetime,
    ttl: timedelta,
) -> _OutboxRow | None:
    now_iso, expiry = _iso(now), _iso(now + ttl)
    with sqlite_transaction(store.db_path) as connection:
        connection.row_factory = Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            _OUTBOX_SELECT, (record.dispatch_id,)
        ).fetchone()
        if row is None or not _parent_live_connection(
            connection, row["run_id"]
        ):
            return None
        state, expiry = row["state"], row["lease_expires_at"]
        if state not in {"pending", "leased"} or (
            state == "leased" and expiry is not None and expiry > now_iso
        ):
            return None
        changed = connection.execute(
            "UPDATE research_dispatch_outbox SET state='leased', "
            "lease_owner=?,"
            "lease_expires_at=?, attempt=attempt+1, updated_at=?, revision="
            "revision+1 WHERE outbox_id=? AND revision=? AND state IN "
            "('pending','leased') AND "
            "(state='pending' OR lease_expires_at <= ?)"
            " AND " + _parent_live_sql(),
            (
                owner,
                expiry,
                now_iso,
                record.dispatch_id,
                row["revision"],
                now_iso,
            ),
        )
        if changed.rowcount != 1:
            return None
        latest = connection.execute(
            _OUTBOX_SELECT, (record.dispatch_id,)
        ).fetchone()
    return _load_row(store, record.dispatch_id) if latest is not None else None


def _mark_sent(
    store: ResearchInputStore,
    row: _OutboxRow,
    owner: str,
    now: datetime,
    ttl: timedelta,
) -> _OutboxRow | None:
    now_iso, expiry = _iso(now), _iso(now + ttl)
    with sqlite_transaction(store.db_path) as connection:
        changed = connection.execute(
            "UPDATE research_dispatch_outbox SET state='sent', "
            "lease_owner=?,"
            "lease_expires_at=?, sent_at=?, updated_at=?, revision=revision+1 "
            "WHERE outbox_id=? AND lease_owner=? AND revision=? "
            "AND state='leased' "
            "AND " + _parent_live_sql(),
            (
                owner,
                expiry,
                now_iso,
                now_iso,
                row.record.dispatch_id,
                owner,
                row.record.revision,
            ),
        )
    return (
        _load_row(store, row.record.dispatch_id)
        if changed.rowcount == 1
        else None
    )


def _accept_row(
    store: ResearchInputStore,
    record: ResearchDispatchRecord,
    task_id: str,
    now: datetime,
    attach_task: Callable[[ResearchDispatchRecord, str], object] | None,
) -> ResearchDispatchDisposition:
    if not task_id:
        return _disposition(
            record.dispatch_id, "ambiguous", None, _TRACKING_FAILED
        )
    now_iso = _iso(now)
    with sqlite_transaction(store.db_path) as connection:
        changed = connection.execute(
            "UPDATE research_dispatch_outbox SET state='accepted', "
            "remote_task_id=?, lease_owner=NULL, lease_expires_at=NULL, "
            "completed_at=?, updated_at=?,"
            "failure_code=NULL, failure_retryable=NULL, revision=revision+1 "
            "WHERE outbox_id=? AND revision=? "
            "AND state IN ('sent','leased','pending')"
            " AND " + _parent_live_sql(),
            (task_id, now_iso, now_iso, record.dispatch_id, record.revision),
        )
        if changed.rowcount == 1:
            connection.execute(
                "UPDATE runs SET stage='execution', revision=revision+1, "
                "updated_at=?"
                " WHERE run_id=? AND status NOT IN "
                "('succeeded','failed','cancelled')"
                " AND stage='planning'",
                (now_iso, record.run_id),
            )
    latest = _load_row(store, record.dispatch_id)
    if latest is not None and latest.record.state == "accepted":
        if attach_task is not None:
            try:
                result = attach_task(latest.record, task_id)
                if inspect.isawaitable(result):
                    _schedule_awaitable(result)
            except _OUTBOX_FAILURES:
                pass
        state: DispositionState = (
            "accepted" if changed.rowcount == 1 else "reconciled"
        )
        return _disposition(record.dispatch_id, state, task_id)
    if not _parent_live(store, record.run_id):
        _mark_row(store, record, now, _CANCEL_OPTIONS)
        return _disposition(
            record.dispatch_id, "cancelled", None, _UNAVAILABLE
        )
    return _disposition(
        record.dispatch_id, "ambiguous", None, _TRACKING_FAILED
    )


def _mark_row(
    store: ResearchInputStore,
    record: ResearchDispatchRecord,
    now: datetime,
    options: tuple[str, str, tuple[str, ...]],
) -> bool:
    return _storage.mark_row(store.db_path, record, _iso(now), options)


def _parent_live(store: ResearchInputStore, run_id: str) -> bool:
    with sqlite_transaction(store.db_path) as connection:
        return _parent_live_connection(connection, run_id)


def _parent_live_connection(
    connection: sqlite3.Connection, run_id: str
) -> bool:
    row = connection.execute(
        "SELECT status, COALESCE((SELECT cancel_requested FROM "
        "research_input_resolutions WHERE run_id = runs.run_id),0) FROM runs "
        "WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return row is not None and row[0] not in _TERMINAL and not bool(row[1])


def _parent_live_sql() -> str:
    return (
        "EXISTS (SELECT 1 FROM runs WHERE runs.run_id = "
        "research_dispatch_outbox.run_id AND runs.status NOT IN "
        "('succeeded','failed','cancelled')) AND NOT EXISTS (SELECT 1 FROM "
        "research_input_resolutions WHERE run_id = "
        "research_dispatch_outbox.run_id "
        "AND COALESCE(cancel_requested,0) <> 0)"
    )


def _disposition(
    dispatch_id: str,
    state: DispositionState,
    task_id: str | None,
    failure_code: ResearchErrorCode | None = None,
) -> ResearchDispatchDisposition:
    return ResearchDispatchDisposition(
        dispatch_id, state, task_id, failure_code
    )


def _ambiguous(dispatch_id: str) -> ResearchDispatchDisposition:
    return _disposition(dispatch_id, "ambiguous", None, _TRACKING_FAILED)


def _cancelled(dispatch_id: str) -> ResearchDispatchDisposition:
    return _disposition(dispatch_id, "cancelled", None, _UNAVAILABLE)


def _terminal_disposition(
    dispatch_id: str, row: _OutboxRow | None
) -> ResearchDispatchDisposition | None:
    if row is None:
        return _ambiguous(dispatch_id)
    if row.record.state == "accepted":
        return _disposition(dispatch_id, "accepted", row.record.remote_task_id)
    if row.record.state == "ambiguous":
        return _ambiguous(dispatch_id)
    if row.record.state == "cancelled":
        return _cancelled(dispatch_id)
    return None


def _task_id(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, Mapping):
        for name in ("task_id", "remote_task_id", "id"):
            candidate = value.get(name)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
    return None


async def _maybe_await(value: object) -> object:
    if inspect.isawaitable(value):
        return await cast(Awaitable[object], value)
    return value


def _schedule_awaitable(value: Awaitable[object]) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    asyncio.ensure_future(value, loop=loop)


def _valid_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _DIGEST_SIZE
        and value == value.lower()
        and all(character in "0123456789abcdef" for character in value)
    )


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, OverflowError) as error:
        raise ResearchPlanCommitError() from error


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _iso(value: datetime) -> str:
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return normalized.astimezone(UTC).isoformat()
