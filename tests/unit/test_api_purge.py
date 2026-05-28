# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``_purge_expired_runs_best_effort`` (api/app.py).

Pins the sanitized-logging path the 2026-05-28 audit (TW-002)
flagged as silently dropping ``sqlite3.Error`` / ``OSError``
without any ops signal. The logger now emits the exception class
name only; this guards against drift toward logging the full
exception message (which can leak SQL fragments or filesystem
paths from pysqlite error strings).
"""

from __future__ import annotations

import logging
import sqlite3
from types import SimpleNamespace
from typing import Callable

import pytest

from mcp_server_phytomni.api import app as api_app
from mcp_server_phytomni.api.app import _purge_expired_runs_best_effort

pytestmark = pytest.mark.unit


def _registry_factory_raising(
    exc: Exception,
) -> Callable[..., SimpleNamespace]:
    """Return a fake ``RunRegistry`` callable whose purge raises ``exc``.

    The production helper calls ``RunRegistry(path).purge_expired()``;
    the factory returned here matches that two-step shape while
    delegating to a ``SimpleNamespace`` so the test fakes carry no
    classes for the R0903 too-few-public-methods ratchet to trip on.
    """

    def _raise() -> None:
        raise exc

    def _factory(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(purge_expired=_raise)

    return _factory


def _enable_propagation_for_caplog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-enable ``mcp_server_phytomni`` propagation so caplog can capture.

    ``common/logging_config.py:configure_logging`` sets
    ``propagate=False`` on the ``mcp_server_phytomni`` package logger to
    avoid polluting the MCP stdio JSON-RPC channel. When an earlier
    test in the session has already triggered that path, ``caplog``
    (which attaches its handler to the root logger) no longer sees
    WARNING records emitted from inside the package, so each test here
    re-enables propagation through ``monkeypatch`` so the restore is
    automatic at teardown.
    """
    package_logger = logging.getLogger("mcp_server_phytomni")
    monkeypatch.setattr(package_logger, "propagate", True)


def test_purge_expired_logs_sanitized_class_on_sqlite_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``sqlite3.Error`` is swallowed but logged with the class name only."""
    _enable_propagation_for_caplog(monkeypatch)
    leaky_exc = sqlite3.OperationalError(
        "database is locked at /var/run/leaky-path/server_tasks.db"
    )
    monkeypatch.setattr(
        api_app, "RunRegistry", _registry_factory_raising(leaky_exc)
    )

    caplog.set_level(logging.WARNING, logger=api_app.__name__)

    # Should not raise; the swallow contract is what every API write
    # path relies on so a failed GC never breaks a user-facing response.
    _purge_expired_runs_best_effort()

    records = [
        record
        for record in caplog.records
        if "TTL purge failed" in record.message
    ]
    assert records, "expected at least one TTL purge warning"
    record = records[-1]
    assert record.levelno == logging.WARNING
    assert "OperationalError" in record.message
    assert (
        "/var/run/leaky-path" not in record.message
    ), "exception message must not leak the on-disk path"
    assert (
        "database is locked" not in record.message
    ), "exception message must not leak SQL fragments"


def test_purge_expired_logs_sanitized_class_on_os_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``OSError`` follows the same swallow-and-log contract."""
    _enable_propagation_for_caplog(monkeypatch)
    monkeypatch.setattr(
        api_app,
        "RunRegistry",
        _registry_factory_raising(OSError("disk full")),
    )
    caplog.set_level(logging.WARNING, logger=api_app.__name__)

    _purge_expired_runs_best_effort()

    records = [
        record
        for record in caplog.records
        if "TTL purge failed" in record.message
    ]
    assert records, "expected at least one TTL purge warning"
    record = records[-1]
    assert "OSError" in record.message
    assert "disk full" not in record.message
