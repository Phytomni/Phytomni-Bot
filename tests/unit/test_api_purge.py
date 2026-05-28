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

import pytest

from mcp_server_phytomni.api import app as api_app
from mcp_server_phytomni.api.app import _purge_expired_runs_best_effort

pytestmark = pytest.mark.unit


def test_purge_expired_logs_sanitized_class_on_sqlite_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``sqlite3.Error`` is swallowed but logged with the class name only."""

    class _StubRegistry:
        """Drop-in replacement that raises on ``purge_expired``."""

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            """Accept the production constructor signature without using it."""

        def purge_expired(self) -> int:
            """Emit a leaky exception message to prove sanitization."""
            raise sqlite3.OperationalError(
                "database is locked at /var/run/leaky-path/server_tasks.db"
            )

    monkeypatch.setattr(api_app, "RunRegistry", _StubRegistry)

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

    class _StubRegistry:
        """Constructor stub that raises ``OSError`` on purge."""

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            """Match the production constructor signature."""

        def purge_expired(self) -> int:
            """Surface an OS-level failure during the purge."""
            raise OSError("disk full")

    monkeypatch.setattr(api_app, "RunRegistry", _StubRegistry)
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
