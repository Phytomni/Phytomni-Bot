# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Neutral run-registry fixtures for server and unit tests."""

from __future__ import annotations

import sqlite3
from typing import Any

import httpx

from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunSpec,
)

__all__ = [
    "assert_run_not_found",
    "foreign_run_spec",
    "seed_foreign_run",
    "stamp_run_created_at",
]


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
    assert response.status_code == 404
    assert response.json()["error"]["code"] == 404


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
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "UPDATE runs SET created_at = ? WHERE run_id = ?",
            [
                ("2026-01-01T00:00:00+00:00", old_run_id),
                ("2026-06-01T00:00:00+00:00", new_run_id),
            ],
        )
