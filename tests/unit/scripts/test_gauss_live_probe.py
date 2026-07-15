# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Offline authorization and redaction tests for the GaussDB probe."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import asyncpg
import pytest


def _load_probe_module() -> Any:
    """Load the standalone script without packaging ``scripts/``."""
    path = Path(__file__).resolve().parents[3] / "scripts/gauss_live_probe.py"
    spec = importlib.util.spec_from_file_location("gauss_live_probe", path)
    if spec is None or spec.loader is None:
        raise AssertionError("probe module spec is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gauss_live_probe = _load_probe_module()


class FakeDatabaseError(asyncpg.PostgresError):
    """Driver-like error exposing only a SQLSTATE code."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__("driver message must not be recorded")
        self.sqlstate = sqlstate


class FakeConnection:
    """Minimal connection fake for the probe's four checks."""

    def __init__(self) -> None:
        self.readonly = False
        self.application_name = ""

    def transaction(self, *, readonly: bool = False) -> Any:
        """Return a transaction context with the requested mode."""
        connection = self

        class Transaction:
            """Minimal async transaction context."""

            async def __aenter__(self) -> FakeConnection:
                connection.readonly = readonly
                return connection

            async def __aexit__(
                self,
                _exc_type: type[BaseException] | None,
                _exc: BaseException | None,
                _traceback: Any,
            ) -> None:
                connection.readonly = False

        return Transaction()

    async def fetchval(self, query: str) -> str:
        """Return the two settings the probe is allowed to inspect."""
        if "transaction_read_only" in query:
            return "on" if self.readonly else "off"
        if "application_name" in query:
            return self.application_name
        raise AssertionError("unexpected probe query")

    async def execute(self, query: str) -> str:
        """Raise the expected SQLSTATE for the zero-row write check."""
        if query == "RESET ALL":
            self.application_name = ""
            return "RESET"
        if query.startswith("SET application_name"):
            self.application_name = "probe-marker"
            return "SET"
        if query.startswith("INSERT"):
            raise FakeDatabaseError("25006" if self.readonly else "42501")
        raise AssertionError("unexpected probe statement")

    async def close(self) -> None:
        """Close the fake connection."""


class FakePool:
    """One-connection pool fake that runs the supplied reset callback."""

    def __init__(self, connection: FakeConnection, reset: Any) -> None:
        self.connection = connection
        self.reset = reset

    def acquire(self) -> Any:
        """Return a borrower context."""
        pool = self

        class Borrower:
            """Minimal async pool borrower context."""

            async def __aenter__(self) -> FakeConnection:
                return pool.connection

            async def __aexit__(
                self,
                _exc_type: type[BaseException] | None,
                _exc: BaseException | None,
                _traceback: Any,
            ) -> None:
                await pool.reset(pool.connection)

        return Borrower()

    async def close(self) -> None:
        """Close the fake pool."""


@pytest.mark.asyncio
async def test_probe_records_only_expected_sqlstates(tmp_path: Path) -> None:
    """The probe returns allowlisted evidence and proves every check."""
    connection = FakeConnection()

    async def connect(**_kwargs: Any) -> FakeConnection:
        return connection

    async def create_pool(**kwargs: Any) -> FakePool:
        return FakePool(connection, kwargs["reset"])

    evidence = await gauss_live_probe.run_probe(
        dsn="postgresql://secret:password@db.example.invalid/app",
        table="safe_table",
        column="id",
        commit="abc1234",
        environment_class="test",
        connect=connect,
        create_pool=create_pool,
    )

    assert evidence["checks"] == {
        "readonly_transaction": "pass",
        "bot_transaction_denied_write": "pass",
        "role_denied_write": "pass",
        "reset_all_isolation": "pass",
    }
    assert evidence["sqlstates"] == {
        "bot_transaction_denied_write": "25006",
        "role_denied_write": "42501",
    }
    rendered = json.dumps(evidence).lower()
    assert "dsn" not in rendered
    assert "password" not in rendered
    assert "safe_table" not in rendered
    assert "insert" not in rendered
    assert "driver message" not in rendered
    assert not (tmp_path / "probe.json").exists()


def test_probe_refuses_without_both_live_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing authorization flags stop before loading credentials."""
    monkeypatch.delenv("PHYTOMNI_RUN_INTEGRATION", raising=False)
    monkeypatch.delenv("PHYTOMNI_ALLOW_NETWORK", raising=False)
    monkeypatch.setattr(
        gauss_live_probe.SensitiveConfig,
        "load",
        lambda: (_ for _ in ()).throw(
            AssertionError("credentials must not be loaded")
        ),
    )

    assert (
        gauss_live_probe.main(["--table", "safe_table", "--column", "id"]) == 2
    )


def test_runbook_documents_the_guarded_probe() -> None:
    """Keep the operator command and redaction contract discoverable."""
    root = Path(__file__).resolve().parents[3]
    runbook = (root / "docs/ops/http-api-runbook.md").read_text(
        encoding="utf-8"
    )
    assert "scripts/gauss_live_probe.py" in runbook
    assert "PHYTOMNI_RUN_INTEGRATION=1" in runbook
    assert "PHYTOMNI_ALLOW_NETWORK=1" in runbook
    assert "never records a DSN" in runbook
