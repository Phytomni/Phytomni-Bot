# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP owner and conflict contracts for Research cancellation."""

from __future__ import annotations

import sqlite3

import httpx
import pytest
from tests.support.sqlite import closed_sqlite_connection

from mcp_server_phytomni.runtime.execution_event_store import (
    SQLiteExecutionEventStore,
)
from mcp_server_phytomni.runtime.research_input_store import ResearchInputStore
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunSpec,
)

pytestmark = pytest.mark.server


def _insert_outbox(
    connection: sqlite3.Connection,
    run_id: str,
    state: str,
    now: str,
) -> None:
    """Insert one child row through a named-value fixture."""
    values = {
        "outbox_id": f"{run_id}:child:0",
        "run_id": run_id,
        "unit_id": f"{run_id}:child:0",
        "state": state,
        "child_ordinal": 0,
        "created_at": now,
        "updated_at": now,
        "sent_at": now if state == "sent" else None,
    }
    columns = tuple(values)
    connection.execute(
        "INSERT INTO research_dispatch_outbox ("
        + ", ".join(columns)
        + ") VALUES ("
        + ", ".join("?" for _ in columns)
        + ")",
        tuple(values.values()),
    )


def _seed_research(
    db_path: str,
    run_id: str,
    *,
    owner: str = "u1",
    outbox_state: str | None = None,
) -> None:
    """Seed a coordinator-owned Research row through the public registry."""
    RunRegistry(db_path).create_run(RunSpec(run_id, owner, "research", "api"))
    ResearchInputStore(db_path)
    if outbox_state is None:
        return
    now = "2026-08-09T00:00:00+00:00"
    with closed_sqlite_connection(db_path) as connection:
        _insert_outbox(connection, run_id, outbox_state, now)


async def test_cancel_research_run_is_owner_scoped_and_idempotent(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """The owner receives canonical cancellation and a repeat replays it."""
    _seed_research(tasks_db_path, "run-http-cancel")
    headers = {"Authorization": f"Bearer {issued_api_key}"}

    first = await api_client.post(
        "/v1/runs/run-http-cancel/cancel", headers=headers
    )
    second = await api_client.post(
        "/v1/runs/run-http-cancel/cancel", headers=headers
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == "cancelled"
    assert first.json()["stage"] is None
    events = SQLiteExecutionEventStore(tasks_db_path).list_events(
        "run-http-cancel", owner="u1"
    )
    assert events is not None
    assert [event.kind for event in events.items] == ["run.cancelled"]


async def test_cancel_research_run_returns_safe_404_for_foreign_owner(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """A foreign owner cannot discover or mutate a Research run."""
    _seed_research(tasks_db_path, "run-http-foreign", owner="u2")
    response = await api_client.post(
        "/v1/runs/run-http-foreign/cancel",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 404


async def test_cancel_research_run_cascades_after_sent(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """A sent child no longer blocks owner cancellation."""
    _seed_research(tasks_db_path, "run-http-sent", outbox_state="sent")
    response = await api_client.post(
        "/v1/runs/run-http-sent/cancel",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


@pytest.mark.parametrize(
    "agent",
    (
        "chat",
        "knowledge",
        "review",
        "analyst",
        "network",
        "design",
        "deep_genome",
    ),
)
async def test_cancel_owner_run_accepts_every_agent(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    agent: str,
) -> None:
    """Any owner-scoped running agent can be cancelled."""
    run_id = f"run-http-{agent}"
    RunRegistry(tasks_db_path).create_run(
        RunSpec(run_id, "u1", agent, "api"),
        outcome=RunOutcome(status="running"),
    )
    response = await api_client.post(
        f"/v1/runs/{run_id}/cancel",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


async def test_cancel_owner_run_conflicts_after_success(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """A succeeded run cannot be cancelled."""
    RunRegistry(tasks_db_path).create_run(
        RunSpec("run-http-done", "u1", "analyst", "api"),
        outcome=RunOutcome(status="succeeded", result={"ok": True}),
    )
    response = await api_client.post(
        "/v1/runs/run-http-done/cancel",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 409
    assert "run_state_conflict" in response.text
