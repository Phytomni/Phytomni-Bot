# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Neutral run-registry fixtures for server and unit tests."""

from __future__ import annotations

from typing import Any

import httpx

from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunSpec,
)
from mcp_server_phytomni.runtime.task_manager import RunContext
from tests.support.sqlite import closed_sqlite_connection

__all__ = [
    "assert_not_found_response",
    "assert_run_not_found",
    "foreign_run_spec",
    "fixed_run_context",
    "seed_foreign_run",
    "stamp_run_created_at",
]


def assert_not_found_response(response: httpx.Response) -> None:
    """Assert the unified owner-scoped 404 response envelope."""
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def assert_run_not_found(
    client: httpx.AsyncClient,
    path: str,
    api_key: str,
) -> None:
    """Assert the owner-scoped run route returns the unified 404 envelope."""
    response = await client.get(
        path,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert_not_found_response(response)


def foreign_run_spec(
    run_id: str, user_id: str, agent: str, origin: str
) -> RunSpec:
    """Build a run specification for an owner-isolation fixture."""
    return RunSpec(
        run_id=run_id,
        user_id=user_id,
        agent=agent,
        origin=origin,
    )


def fixed_run_context(spec: RunSpec) -> RunContext:
    """Build the deterministic child-task context shared by registry tests."""
    return RunContext(
        run_id=spec.run_id,
        user_id=spec.user_id,
        agent=spec.agent,
        origin=spec.origin,
        created_at="2026-05-20T00:00:00+00:00",
        updated_at="2026-05-20T00:00:00+00:00",
    )


def seed_foreign_run(
    db_path: str,
    *,
    spec: RunSpec,
    result: dict[str, Any] | None = None,
) -> None:
    """Insert a terminal run owned by a different user."""
    RunRegistry(db_path).create_run(
        spec,
        outcome=RunOutcome(status="succeeded", result=result),
    )


def stamp_run_created_at(
    db_path: str, old_run_id: str, new_run_id: str
) -> None:
    """Set deterministic timestamps used by created-after filtering tests."""
    with closed_sqlite_connection(db_path) as conn:
        conn.executemany(
            "UPDATE runs SET created_at = ? WHERE run_id = ?",
            [
                ("2026-01-01T00:00:00+00:00", old_run_id),
                ("2026-06-01T00:00:00+00:00", new_run_id),
            ],
        )
