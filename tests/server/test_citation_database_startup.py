# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Citation artifact validation at the MCP and HTTP serving boundaries."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from tests.support.citation_database import create_valid_citation_database

from mcp_server_phytomni.agents.shared import citation_database
from mcp_server_phytomni.agents.shared.citation_database import (
    CitationDatabaseArtifactError,
    CitationDatabaseConfigurationError,
    CitationDatabaseError,
    CitationDatabaseFormatError,
    CitationDatabaseIntegrityError,
    CitationDatabaseMetadataError,
    CitationDatabaseSchemaError,
    resolve_citation_database_path,
)
from mcp_server_phytomni.api import app_support
from mcp_server_phytomni.api.app import create_app
from mcp_server_phytomni.mcp import app as mcp_app

pytestmark = pytest.mark.server
_http_lifespan_context = getattr(app_support, "_http_lifespan")


def _clear_citation_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove both aliases and clear the process path resolver."""
    monkeypatch.delenv("CITATION_DB_PATH", raising=False)
    monkeypatch.delenv("PHYTOMNI_CITATION_DB_PATH", raising=False)
    resolve_citation_database_path.cache_clear()


def _assert_stable_error(
    error: CitationDatabaseError,
    expected_code: str,
    *secrets: str,
) -> None:
    """Require the bounded startup error without artifact diagnostics."""
    assert error.code == expected_code
    assert str(error) == f"{expected_code}: verify CITATION_DB_PATH"
    assert all(secret not in str(error) for secret in secrets)


def _unexpected(label: str) -> Callable[..., Any]:
    """Return a fake that fails if startup crosses a guarded boundary."""

    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError(f"startup reached {label}")

    return fail


def test_package_import_does_not_require_citation_db() -> None:
    """Importing the package remains artifact-free in a clean process."""
    environment = os.environ.copy()
    environment.pop("CITATION_DB_PATH", None)
    environment.pop("PHYTOMNI_CITATION_DB_PATH", None)

    result = subprocess.run(
        [sys.executable, "-c", "import mcp_server_phytomni"],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_create_app_does_not_require_citation_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FastAPI construction stays lazy until its lifespan is entered."""
    _clear_citation_config(monkeypatch)

    app = create_app()

    assert isinstance(app, FastAPI)


@pytest.mark.asyncio
async def test_http_lifespan_missing_config_fails_before_client_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP startup rejects absent configuration before client ownership."""
    _clear_citation_config(monkeypatch)
    monkeypatch.setattr(
        app_support,
        "init_shared_client",
        _unexpected("HTTP client init"),
    )

    with pytest.raises(CitationDatabaseConfigurationError) as exc:
        async with _http_lifespan_context(FastAPI()):
            raise AssertionError("HTTP lifespan yielded")

    _assert_stable_error(
        exc.value,
        "citation_db_configuration_missing",
        "driver body",
    )


@pytest.mark.asyncio
async def test_mcp_serve_missing_config_fails_before_stdio_or_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP startup rejects absent configuration before owned resources."""
    _clear_citation_config(monkeypatch)
    monkeypatch.setattr(
        mcp_app,
        "init_shared_client",
        _unexpected("MCP client init"),
    )
    monkeypatch.setattr(mcp_app, "stdio_server", _unexpected("MCP stdio"))

    with pytest.raises(CitationDatabaseConfigurationError) as exc:
        await mcp_app.serve()

    _assert_stable_error(
        exc.value,
        "citation_db_configuration_missing",
        "driver body",
    )


def _prepare_invalid_artifact(
    kind: str,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, type[CitationDatabaseError]]:
    """Create one invalid artifact and return its stable error class."""
    path = root / f"{kind}.sqlite"
    if kind == "missing_file":
        return path, CitationDatabaseArtifactError
    if kind == "non_regular":
        path.mkdir()
        return path, CitationDatabaseArtifactError
    if kind == "non_sqlite":
        path.write_bytes(b"driver body: not a database")
        return path, CitationDatabaseFormatError

    create_valid_citation_database(path)
    statement: str | None = None
    expected_error: type[CitationDatabaseError]
    if kind == "unreadable_regular":
        assert path.is_file()
        monkeypatch.setattr(
            citation_database.os,
            "access",
            lambda _path, _mode: False,
        )
        expected_error = CitationDatabaseArtifactError
    elif kind == "wrong_version":
        statement = "PRAGMA user_version=2"
        expected_error = CitationDatabaseSchemaError
    elif kind == "wrong_schema":
        statement = "DROP TABLE citation_conflicts"
        expected_error = CitationDatabaseSchemaError
    elif kind == "wrong_counts":
        statement = "UPDATE citation_build_metadata SET source_record_count=1"
        expected_error = CitationDatabaseMetadataError
    elif kind == "failed_integrity":
        _install_failed_integrity(monkeypatch)
        expected_error = CitationDatabaseIntegrityError
    else:
        raise AssertionError(f"unknown artifact kind: {kind}")

    if statement is not None:
        with sqlite3.connect(path) as connection:
            connection.execute(statement)
    return path, expected_error


def _install_failed_integrity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Substitute only the final SQLite integrity result."""
    real_connect = getattr(citation_database, "_connect_read_only")

    class _IntegrityConnection:
        """Delegate all connection work except the integrity pragma."""

        def __init__(self, connection: sqlite3.Connection) -> None:
            """Store the real read-only connection."""
            self._connection = connection

        def execute(self, query: str, *args: Any) -> Any:
            """Replace only the final integrity result."""
            if query == "PRAGMA integrity_check":
                return self._connection.execute("SELECT 'not ok'")
            return self._connection.execute(query, *args)

        def close(self) -> None:
            """Close the real read-only connection."""
            self._connection.close()

    monkeypatch.setattr(
        citation_database,
        "_connect_read_only",
        lambda path: _IntegrityConnection(real_connect(path)),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "artifact_kind",
    (
        "missing_file",
        "non_regular",
        "unreadable_regular",
        "non_sqlite",
        "wrong_version",
        "wrong_schema",
        "wrong_counts",
        "failed_integrity",
    ),
)
async def test_invalid_artifact_fails_both_entrypoints_before_client_init(
    artifact_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP and HTTP expose the same stable artifact failure class."""
    path, expected_error = _prepare_invalid_artifact(
        artifact_kind,
        tmp_path,
        monkeypatch,
    )
    monkeypatch.setenv("CITATION_DB_PATH", str(path))
    monkeypatch.delenv("PHYTOMNI_CITATION_DB_PATH", raising=False)
    resolve_citation_database_path.cache_clear()
    monkeypatch.setattr(
        app_support,
        "init_shared_client",
        _unexpected("HTTP client init"),
    )
    monkeypatch.setattr(
        mcp_app,
        "init_shared_client",
        _unexpected("MCP client init"),
    )
    monkeypatch.setattr(mcp_app, "stdio_server", _unexpected("MCP stdio"))

    with pytest.raises(expected_error) as http_exc:
        async with _http_lifespan_context(FastAPI()):
            raise AssertionError("HTTP lifespan yielded")
    with pytest.raises(expected_error) as mcp_exc:
        await mcp_app.serve()

    for error in (http_exc.value, mcp_exc.value):
        _assert_stable_error(
            error,
            expected_error.code,
            str(path),
            "driver body",
            "not a database",
        )


@pytest.mark.asyncio
async def test_http_valid_startup_orders_validation_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP validates before client init and retains cleanup ordering."""
    events: list[str] = []
    monkeypatch.setattr(
        app_support,
        "validate_citation_database",
        lambda: events.append("validate"),
        raising=False,
    )
    monkeypatch.setattr(
        app_support,
        "init_shared_client",
        lambda: events.append("client_init"),
    )

    async def close_client() -> None:
        events.append("client_close")

    async def close_gauss() -> None:
        events.append("gauss_close")

    monkeypatch.setattr(app_support, "aclose_shared_client", close_client)
    monkeypatch.setattr(app_support, "aclose_gauss_pool", close_gauss)

    async with _http_lifespan_context(FastAPI()):
        events.append("yield")

    assert events == [
        "validate",
        "client_init",
        "yield",
        "client_close",
        "gauss_close",
    ]


@pytest.mark.asyncio
async def test_mcp_valid_startup_orders_validation_transport_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP follows the complete startup order and closes both clients."""
    events: list[str] = []

    class _Server:
        """Minimal MCP server fake retaining registration and run events."""

        def __init__(self, name: str) -> None:
            assert name == "Phytomni-Server"
            events.append("server")

        def list_tools(self) -> Callable[[Any], Any]:
            """Record list-tools handler registration."""
            events.append("register_list_tools")
            return lambda function: function

        def call_tool(self) -> Callable[[Any], Any]:
            """Record call-tool handler registration."""
            events.append("register_call_tool")
            return lambda function: function

        def create_initialization_options(self) -> object:
            """Return one opaque initialization-options sentinel."""
            events.append("options")
            return object()

        async def run(self, *_args: Any, **kwargs: Any) -> None:
            """Record the MCP run call and its exception policy."""
            assert kwargs == {"raise_exceptions": True}
            events.append("run")

    @asynccontextmanager
    async def stdio() -> AsyncIterator[tuple[object, object]]:
        """Yield deterministic stdio stream sentinels."""
        events.append("stdio_enter")
        try:
            yield object(), object()
        finally:
            events.append("stdio_exit")

    async def close_client() -> None:
        """Record shared HTTP client cleanup."""
        events.append("client_close")

    async def close_gauss() -> None:
        """Record Gauss pool cleanup."""
        events.append("gauss_close")

    monkeypatch.setattr(
        mcp_app,
        "configure_logging",
        lambda: events.append("logging"),
    )
    monkeypatch.setattr(
        mcp_app,
        "validate_citation_database",
        lambda: events.append("validate"),
        raising=False,
    )
    monkeypatch.setattr(mcp_app, "Server", _Server)
    monkeypatch.setattr(
        mcp_app,
        "init_shared_client",
        lambda: events.append("client_init"),
    )
    monkeypatch.setattr(mcp_app, "stdio_server", stdio)
    monkeypatch.setattr(mcp_app, "aclose_shared_client", close_client)
    monkeypatch.setattr(mcp_app, "aclose_gauss_pool", close_gauss)

    await mcp_app.serve()

    assert events == [
        "logging",
        "validate",
        "server",
        "register_list_tools",
        "register_call_tool",
        "options",
        "client_init",
        "stdio_enter",
        "run",
        "stdio_exit",
        "client_close",
        "gauss_close",
    ]
