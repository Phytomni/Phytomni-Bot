# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Real SQLite connection ownership for task reconciliation lookups."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from unittest.mock import Mock

import pytest

from mcp_server_phytomni.runtime import task_reconcile

pytestmark = pytest.mark.unit


@pytest.fixture(name="lookup_connection")
def _lookup_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[sqlite3.Connection, Mock]]:
    """Keep a real connection alive so garbage collection cannot close it."""
    connection = sqlite3.connect(":memory:")
    try:
        with connection:
            connection.execute(
                "CREATE TABLE research_dispatch_outbox "
                "(outbox_id TEXT, remote_task_id TEXT, payload_json TEXT)"
            )
            connection.execute(
                "INSERT INTO research_dispatch_outbox VALUES (?, ?, ?)",
                ("dispatch-1", "remote-1", '{"compute_resource":"medium"}'),
            )
            connection.execute(
                "CREATE TABLE tasks (task_id TEXT, input_fingerprint TEXT)"
            )
            connection.execute(
                "INSERT INTO tasks VALUES (?, ?)",
                ("task-1", " fingerprint-1 "),
            )
        connect = Mock(return_value=connection)
        monkeypatch.setattr(sqlite3, "connect", connect)
        yield connection, connect
    finally:
        connection.close()


def _assert_closed(connection: sqlite3.Connection, connect: Mock) -> None:
    """Require unchanged connect arguments and an unusable connection."""
    connect.assert_called_once_with("lookup.db")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ('{"compute_resource":"medium"}', {"compute_resource": "medium"}),
        ("invalid-json", {}),
        ("[]", {}),
        (None, {}),
    ],
)
def test_outbox_lookup_closes_after_success(
    lookup_connection: tuple[sqlite3.Connection, Mock],
    payload: str | None,
    expected: dict[str, str],
) -> None:
    """Return the decoded outbox payload without retaining its connection."""
    connection, connect = lookup_connection
    with connection:
        connection.execute(
            "UPDATE research_dispatch_outbox SET payload_json=?", (payload,)
        )
    lookup = getattr(task_reconcile, "_research_outbox_lookup")

    assert lookup("lookup.db", ("missing", " remote-1 ")) == (
        "dispatch-1",
        expected,
    )
    _assert_closed(connection, connect)


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM research_dispatch_outbox",
        "DROP TABLE research_dispatch_outbox",
        "ALTER TABLE research_dispatch_outbox DROP COLUMN payload_json",
    ],
    ids=["missing-row", "missing-table", "query-error"],
)
def test_outbox_lookup_closes_after_miss_or_error(
    lookup_connection: tuple[sqlite3.Connection, Mock], statement: str
) -> None:
    """Close the outbox connection on early returns and SQLite errors."""
    connection, connect = lookup_connection
    with connection:
        connection.execute(statement)
    lookup = getattr(task_reconcile, "_research_outbox_lookup")

    assert lookup("lookup.db", ("remote-1",)) is None
    _assert_closed(connection, connect)


def test_outbox_lookup_does_not_connect_for_blank_ids(
    lookup_connection: tuple[sqlite3.Connection, Mock],
) -> None:
    """Reject empty identifiers before opening a database connection."""
    connection, connect = lookup_connection
    lookup = getattr(task_reconcile, "_research_outbox_lookup")

    assert lookup("lookup.db", ("", " ")) is None
    connect.assert_not_called()
    assert connection.execute("SELECT 1").fetchone() == (1,)


@pytest.mark.parametrize(
    ("fingerprint", "expected"),
    [(" fingerprint-1 ", "fingerprint-1"), (" ", None), (None, None)],
)
def test_fingerprint_lookup_closes_after_read(
    lookup_connection: tuple[sqlite3.Connection, Mock],
    fingerprint: str | None,
    expected: str | None,
) -> None:
    """Normalize stored fingerprints and close the query connection."""
    connection, connect = lookup_connection
    with connection:
        connection.execute(
            "UPDATE tasks SET input_fingerprint=?", (fingerprint,)
        )
    lookup = getattr(task_reconcile, "_task_input_fingerprint")

    assert lookup("lookup.db", "task-1") == expected
    _assert_closed(connection, connect)


@pytest.mark.parametrize(
    "statement",
    ["DELETE FROM tasks", "DROP TABLE tasks"],
    ids=["missing-row", "query-error"],
)
def test_fingerprint_lookup_closes_after_miss_or_error(
    lookup_connection: tuple[sqlite3.Connection, Mock], statement: str
) -> None:
    """Close the fingerprint connection even without a usable task row."""
    connection, connect = lookup_connection
    with connection:
        connection.execute(statement)
    lookup = getattr(task_reconcile, "_task_input_fingerprint")

    assert lookup("lookup.db", "task-1") is None
    _assert_closed(connection, connect)
