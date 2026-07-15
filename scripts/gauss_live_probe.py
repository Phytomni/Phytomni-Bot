#!/usr/bin/env python3
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Run an explicitly authorized, redacted GaussDB safety probe.

The probe is deliberately separate from the application request path.  It
opens one direct connection and one one-connection pool, verifies the
read-only transaction contract, attempts a zero-row write to observe the
deployment role's SQLSTATE, and checks that ``RESET ALL`` isolates pooled
session settings.  No live connection is attempted unless both integration
and network flags are set to ``1``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import asyncpg

from mcp_server_phytomni.agents.shared.gauss import _gauss_reset
from mcp_server_phytomni.config.settings import SensitiveConfig

OUTPUT_DIR = Path("e2e/output")
LIVE_FLAGS = ("PHYTOMNI_RUN_INTEGRATION", "PHYTOMNI_ALLOW_NETWORK")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SQLSTATE = re.compile(r"^[0-9A-Z]{5}$")
_SAFE_COMMIT = re.compile(r"^[0-9a-f]{7,64}$")
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")

ConnectFn = Callable[..., Awaitable[Any]]
CreatePoolFn = Callable[..., Awaitable[Any]]


def _validate_table(table: str) -> bool:
    """Return whether a table is a simple or schema-qualified identifier."""
    parts = table.split(".")
    return len(parts) in {1, 2} and all(
        _IDENTIFIER.fullmatch(part) for part in parts
    )


def _validate_column(column: str) -> bool:
    """Return whether a column is a plain SQL identifier."""
    return bool(_IDENTIFIER.fullmatch(column))


def _safe_commit(value: str) -> str:
    """Return a bounded commit label or a fixed unknown marker."""
    return value if _SAFE_COMMIT.fullmatch(value) else "unknown"


def _safe_environment_class(value: str) -> str:
    """Return a bounded environment label without echoing arbitrary input."""
    return value if _SAFE_LABEL.fullmatch(value) else "unspecified"


def _zero_row_write_sql(table: str, column: str) -> str:
    """Build the validated zero-row write used only for SQLSTATE checks."""
    return f"INSERT INTO {table} ({column}) SELECT NULL WHERE FALSE"


async def _denied_write_in_transaction(
    connection: Any,
    statement: str,
    expected: str,
    *,
    readonly: bool,
) -> tuple[str, str | None]:
    """Run one denied write and roll back before inspecting its SQLSTATE."""
    try:
        async with connection.transaction(readonly=readonly):
            await connection.execute(statement)
    except asyncpg.PostgresError as exc:
        state = getattr(exc, "sqlstate", None)
        state = state if isinstance(state, str) else None
        state = state if _SQLSTATE.fullmatch(state or "") else None
        return ("pass", state) if state == expected else ("fail", state)
    return "fail", None


async def _probe_transactions(
    connection: Any,
    statement: str,
) -> tuple[dict[str, str], dict[str, str | None]]:
    """Verify transaction mode and the two expected denied-write states."""
    checks = {
        "readonly_transaction": "fail",
        "bot_transaction_denied_write": "fail",
        "role_denied_write": "fail",
    }
    sqlstates: dict[str, str | None] = {
        "bot_transaction_denied_write": None,
        "role_denied_write": None,
    }
    async with connection.transaction(readonly=True):
        read_only_state = await connection.fetchval(
            "SELECT current_setting('transaction_read_only')"
        )
        if read_only_state == "on":
            checks["readonly_transaction"] = "pass"
    status, state = await _denied_write_in_transaction(
        connection, statement, "25006", readonly=True
    )
    checks["bot_transaction_denied_write"] = status
    sqlstates["bot_transaction_denied_write"] = state

    status, state = await _denied_write_in_transaction(
        connection, statement, "42501", readonly=False
    )
    checks["role_denied_write"] = status
    sqlstates["role_denied_write"] = state
    return checks, sqlstates


async def _probe_pool_isolation(
    create_pool_fn: CreatePoolFn,
    dsn: str,
) -> str:
    """Verify that the shared GaussDB reset clears pooled session state."""
    pool = await create_pool_fn(
        dsn=dsn,
        min_size=1,
        max_size=1,
        reset=_gauss_reset,
    )
    try:
        async with pool.acquire() as pooled_connection:
            await pooled_connection.execute(
                "SET application_name = 'phytomni-gauss-probe-marker'"
            )
        async with pool.acquire() as pooled_connection:
            application_name = await pooled_connection.fetchval(
                "SELECT current_setting('application_name')"
            )
            return (
                "pass"
                if application_name != "phytomni-gauss-probe-marker"
                else "fail"
            )
    finally:
        await pool.close()


# The public test seam keeps the DSN, validated identifiers, redaction labels,
# and injectable driver hooks explicit so the live contract is auditable.
# pylint: disable=too-many-arguments
async def run_probe(
    *,
    dsn: str,
    table: str,
    column: str,
    commit: str,
    environment_class: str,
    connect: ConnectFn | None = None,
    create_pool: CreatePoolFn | None = None,
) -> dict[str, Any]:
    """Run the probe and return only allowlisted evidence fields.

    ``connect`` and ``create_pool`` are injectable solely for offline tests;
    the default functions are the asyncpg driver entry points.
    """
    if not _validate_table(table) or not _validate_column(column):
        raise ValueError("invalid probe identifier")

    connect_fn = connect or asyncpg.connect
    create_pool_fn = create_pool or asyncpg.create_pool
    statement = _zero_row_write_sql(table, column)
    connection = await connect_fn(dsn=dsn)
    try:
        checks, sqlstates = await _probe_transactions(connection, statement)
    finally:
        await connection.close()

    checks["reset_all_isolation"] = await _probe_pool_isolation(
        create_pool_fn, dsn
    )

    return {
        "commit": _safe_commit(commit),
        "environment_class": _safe_environment_class(environment_class),
        "checks": checks,
        "sqlstates": sqlstates,
    }


# pylint: enable=too-many-arguments


def _live_authorized() -> bool:
    """Return whether both explicit live-run flags are enabled."""
    return all(os.getenv(name) == "1" for name in LIVE_FLAGS)


def _git_commit() -> str:
    """Return a safe commit label without exposing command output."""
    configured = os.getenv("PHYTOMNI_GIT_COMMIT", "")
    if _SAFE_COMMIT.fullmatch(configured):
        return configured
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            cwd=Path(__file__).resolve().parents[1],
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return _safe_commit(result.stdout.strip())


def _output_path(value: Path) -> Path | None:
    """Return an output path constrained to the repository output directory."""
    candidate = value.expanduser()
    output_root = OUTPUT_DIR.resolve()
    try:
        candidate_resolved = candidate.resolve()
        candidate_resolved.relative_to(output_root)
    except (OSError, ValueError):
        return None
    return candidate_resolved


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse probe command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Run an authorized, redacted GaussDB safety probe."
    )
    parser.add_argument("--table", required=True)
    parser.add_argument("--column", required=True)
    parser.add_argument(
        "--environment-class",
        default=os.getenv("PHYTOMNI_ENVIRONMENT_CLASS", "unspecified"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_DIR / "gauss_live_probe.json",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the guarded probe and return a process exit code."""
    args = _parse_args(argv)
    if not _live_authorized():
        print(
            "GaussDB live probe requires "
            "PHYTOMNI_RUN_INTEGRATION=1 and PHYTOMNI_ALLOW_NETWORK=1"
        )
        return 2
    if not _validate_table(args.table) or not _validate_column(args.column):
        print("GaussDB live probe rejected invalid identifiers")
        return 2
    output_path = _output_path(args.output)
    if output_path is None:
        print("GaussDB live probe output must be under e2e/output")
        return 2

    try:
        sensitive = SensitiveConfig.load()
        evidence = asyncio.run(
            run_probe(
                dsn=sensitive.GAUSS_DSN.get_secret_value(),
                table=args.table,
                column=args.column,
                commit=_git_commit(),
                environment_class=args.environment_class,
            )
        )
    except (
        asyncpg.PostgresError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        print("GaussDB live probe failed")
        return 1

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        print("GaussDB live probe could not write evidence")
        return 1

    passed = all(value == "pass" for value in evidence["checks"].values())
    print(f"GaussDB live probe: {'pass' if passed else 'fail'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
