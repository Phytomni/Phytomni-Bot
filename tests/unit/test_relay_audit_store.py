# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the relay audit SQLite store.

Covers schema init, atomic insert, filtered listing, request-id lookup,
retention cleanup, and the no-plaintext-key-material invariant.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mcp_server_phytomni.api.relay import audit as audit_mod
from mcp_server_phytomni.api.relay.audit import (
    RelayAuditQuery,
    RelayAuditRecord,
    RelayAuditStore,
)

pytestmark = pytest.mark.unit


def _make_store(tmp_path: Path) -> RelayAuditStore:
    """Build a store on a fresh local SQLite path."""
    return RelayAuditStore(str(tmp_path / "relay_audit.sqlite"))


def _entry(**overrides: object) -> RelayAuditRecord:
    """Build a minimal audit entry, overriding fields as needed."""
    base = RelayAuditRecord(
        request_id="req", user_id="u", key_prefix="ptm_u", service="llm"
    )
    return base.model_copy(update=overrides)


def test_init_creates_empty_db(tmp_path: Path) -> None:
    """A new store starts with no audit records."""
    store = _make_store(tmp_path)

    assert store.query() == []


def test_record_returns_row_id_and_round_trips(tmp_path: Path) -> None:
    """A recorded row is fetchable by request id with fields intact."""
    store = _make_store(tmp_path)

    row_id = store.record(
        RelayAuditRecord(
            request_id="req-1",
            user_id="alice",
            key_prefix="ptm_abc12345",
            service="llm",
            operation="chat.completions",
            status_code=200,
            duration_ms=42,
            request_body='{"q": "hi"}',
            response_body='{"a": "hello"}',
        )
    )

    assert row_id >= 1
    records = store.get_by_request_id("req-1")
    assert len(records) == 1
    record = records[0]
    assert record.id == row_id
    assert record.user_id == "alice"
    assert record.key_prefix == "ptm_abc12345"
    assert record.service == "llm"
    assert record.status_code == 200
    assert record.duration_ms == 42
    assert record.request_body == '{"q": "hi"}'
    assert record.response_body == '{"a": "hello"}'
    assert record.created_at


def test_query_filters_by_user_service_and_status(tmp_path: Path) -> None:
    """Listing narrows by user id, service, and status code."""
    store = _make_store(tmp_path)
    store.record(
        _entry(
            request_id="r1",
            user_id="alice",
            key_prefix="ptm_a",
            service="llm",
            status_code=200,
        )
    )
    store.record(
        _entry(
            request_id="r2",
            user_id="bob",
            key_prefix="ptm_b",
            service="llm",
            status_code=500,
        )
    )
    store.record(
        _entry(
            request_id="r3",
            user_id="alice",
            key_prefix="ptm_a",
            service="retrieve",
            status_code=200,
        )
    )

    by_user = store.query(RelayAuditQuery(user_id="alice"))
    by_service = store.query(RelayAuditQuery(service="llm"))
    by_status = store.query(RelayAuditQuery(status_code=500))
    by_pair = store.query(
        RelayAuditQuery(key_prefix="ptm_a", service="retrieve")
    )

    assert {r.request_id for r in by_user} == {"r1", "r3"}
    assert {r.request_id for r in by_service} == {"r1", "r2"}
    assert {r.request_id for r in by_status} == {"r2"}
    assert {r.request_id for r in by_pair} == {"r3"}


def test_query_filters_by_time_range_and_paginates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Listing honors created_after/created_before plus limit/offset."""
    store = _make_store(tmp_path)
    for stamp, req in (
        ("2026-01-01T00:00:00+00:00", "old"),
        ("2026-06-01T00:00:00+00:00", "mid"),
        ("2026-12-01T00:00:00+00:00", "new"),
    ):
        monkeypatch.setattr(audit_mod, "_now_iso", lambda s=stamp: s)
        store.record(_entry(request_id=req))
    monkeypatch.undo()

    windowed = store.query(
        RelayAuditQuery(
            created_after="2026-03-01T00:00:00+00:00",
            created_before="2026-09-01T00:00:00+00:00",
        )
    )
    assert [r.request_id for r in windowed] == ["mid"]

    page = store.query(RelayAuditQuery(limit=1, offset=1))
    assert [r.request_id for r in page] == ["mid"]


def test_purge_expired_deletes_old_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retention cleanup drops records older than the window only."""
    store = _make_store(tmp_path)
    monkeypatch.setattr(
        audit_mod, "_now_iso", lambda: "2000-01-01T00:00:00+00:00"
    )
    store.record(_entry(request_id="ancient"))
    monkeypatch.undo()
    store.record(_entry(request_id="fresh"))

    deleted = store.purge_expired(retention_days=30)

    assert deleted == 1
    assert [r.request_id for r in store.query()] == ["fresh"]


def test_schema_has_no_plaintext_key_material(tmp_path: Path) -> None:
    """The audit schema stores only the public key prefix, no secrets."""
    store = _make_store(tmp_path)

    conn = sqlite3.connect(store.db_path)
    try:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(relay_audit)")
        }
    finally:
        conn.close()

    assert "key_prefix" in columns
    assert not columns & {
        "key_hash",
        "salt",
        "api_key",
        "key",
        "token",
        "authorization",
    }
