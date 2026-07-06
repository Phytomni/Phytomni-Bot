# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the package-level logging configuration helper.

Verifies that ``configure_logging`` produces a single stderr handler on
the ``mcp_server_phytomni`` logger, respects ``PHYTOMNI_DEBUG``, and is
idempotent under repeated invocation so tests and reloads cannot stack
duplicate handlers.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from typing import Any

import pytest

from mcp_server_phytomni.common.logging_config import (
    _LOG_FORMAT,
    PHYTOMNI_DEBUG_ENV,
    _RedactingFormatter,
    configure_logging,
    debug_enabled,
)

pytestmark = pytest.mark.unit

_PACKAGE_LOGGER_NAME = "mcp_server_phytomni"
_HANDLER_SENTINEL = "_phytomni_log_handler"


@pytest.fixture(autouse=True)
def _isolated_package_logger() -> Iterator[None]:
    """Snapshot and restore the package logger between tests.

    The helper mutates a process-global ``logging.Logger`` so the
    fixture captures the previous level/propagate/handlers and replaces
    them after the test runs to keep parallel test cases independent.
    """
    package_logger = logging.getLogger(_PACKAGE_LOGGER_NAME)
    saved_level = package_logger.level
    saved_propagate = package_logger.propagate
    saved_handlers = list(package_logger.handlers)
    package_logger.handlers = []
    try:
        yield
    finally:
        package_logger.handlers = saved_handlers
        package_logger.setLevel(saved_level)
        package_logger.propagate = saved_propagate


def _phytomni_handlers(logger: logging.Logger) -> list[logging.Handler]:
    """Return only the handlers tagged by ``configure_logging``."""
    return [
        handler
        for handler in logger.handlers
        if getattr(handler, _HANDLER_SENTINEL, False)
    ]


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
def test_debug_enabled_truthy_values(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """All documented truthy spellings flip ``debug_enabled`` to True."""
    monkeypatch.setenv(PHYTOMNI_DEBUG_ENV, raw)
    assert debug_enabled() is True


@pytest.mark.parametrize("raw", ["", "0", "false", "no", "off", "anything"])
def test_debug_enabled_other_values(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """Anything outside the truthy set keeps debug off."""
    monkeypatch.setenv(PHYTOMNI_DEBUG_ENV, raw)
    assert debug_enabled() is False


def test_configure_logging_default_level_is_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ``PHYTOMNI_DEBUG`` the package logger sits at INFO."""
    monkeypatch.delenv(PHYTOMNI_DEBUG_ENV, raising=False)

    configure_logging()

    package_logger = logging.getLogger(_PACKAGE_LOGGER_NAME)
    handlers = _phytomni_handlers(package_logger)
    assert package_logger.level == logging.INFO
    assert len(handlers) == 1
    assert handlers[0].level == logging.INFO


def test_configure_logging_debug_env_promotes_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``PHYTOMNI_DEBUG=1`` elevates both logger and handler to DEBUG."""
    monkeypatch.setenv(PHYTOMNI_DEBUG_ENV, "1")

    configure_logging()

    package_logger = logging.getLogger(_PACKAGE_LOGGER_NAME)
    handlers = _phytomni_handlers(package_logger)
    assert package_logger.level == logging.DEBUG
    assert handlers[0].level == logging.DEBUG


def test_configure_logging_writes_to_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The configured handler routes to ``sys.stderr``, not stdout.

    The MCP stdio channel sits on stdout; routing logs there would
    corrupt JSON-RPC frames. This test pins the destination.
    """
    monkeypatch.delenv(PHYTOMNI_DEBUG_ENV, raising=False)

    configure_logging()

    handler = _phytomni_handlers(logging.getLogger(_PACKAGE_LOGGER_NAME))[0]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.stream is sys.stderr


def test_configure_logging_disables_root_propagation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Propagation stays off so root configurations never see our records."""
    monkeypatch.delenv(PHYTOMNI_DEBUG_ENV, raising=False)

    configure_logging()

    assert logging.getLogger(_PACKAGE_LOGGER_NAME).propagate is False


def test_configure_logging_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated calls reuse the existing handler instead of stacking."""
    monkeypatch.delenv(PHYTOMNI_DEBUG_ENV, raising=False)

    configure_logging()
    package_logger = logging.getLogger(_PACKAGE_LOGGER_NAME)
    first_handler = _phytomni_handlers(package_logger)[0]

    configure_logging()
    handlers_after = _phytomni_handlers(package_logger)

    assert len(handlers_after) == 1
    assert handlers_after[0] is first_handler


def test_configure_logging_refreshes_level_on_re_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A debug toggle picked up after the first call still takes effect."""
    monkeypatch.delenv(PHYTOMNI_DEBUG_ENV, raising=False)
    configure_logging()
    package_logger = logging.getLogger(_PACKAGE_LOGGER_NAME)
    assert package_logger.level == logging.INFO

    monkeypatch.setenv(PHYTOMNI_DEBUG_ENV, "1")
    configure_logging()

    handler = _phytomni_handlers(package_logger)[0]
    assert package_logger.level == logging.DEBUG
    assert handler.level == logging.DEBUG


def test_configure_logging_preserves_external_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User-attached handlers are not removed by ``configure_logging``."""
    monkeypatch.delenv(PHYTOMNI_DEBUG_ENV, raising=False)
    package_logger = logging.getLogger(_PACKAGE_LOGGER_NAME)
    user_handler = logging.NullHandler()
    package_logger.addHandler(user_handler)

    configure_logging()

    assert user_handler in package_logger.handlers
    assert len(_phytomni_handlers(package_logger)) == 1


def test_configure_logging_uses_redacting_formatter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The configured handler scrubs secrets via the redacting formatter."""
    monkeypatch.delenv(PHYTOMNI_DEBUG_ENV, raising=False)

    configure_logging()

    handler = _phytomni_handlers(logging.getLogger(_PACKAGE_LOGGER_NAME))[0]
    assert isinstance(handler.formatter, _RedactingFormatter)


def test_redacting_formatter_scrubs_message_secret() -> None:
    """A bearer token / internal URL in the message is redacted on emit."""
    record = logging.LogRecord(
        name="x",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="auth Bearer abc.def123 to https://internal.host/run?token=sek",
        args=(),
        exc_info=None,
    )

    out = _RedactingFormatter(_LOG_FORMAT).format(record)

    assert "abc.def123" not in out
    assert "internal.host" not in out
    assert "sek" not in out
    assert "<redacted" in out


def test_redacting_formatter_scrubs_exception_traceback() -> None:
    """A secret in a ``logger.exception`` traceback is redacted on emit."""
    exc_info: Any = None
    try:
        raise RuntimeError("token=deadbeef at https://h:9000/x")
    except RuntimeError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="x",
        level=logging.ERROR,
        pathname="",
        lineno=0,
        msg="mount failed",
        args=(),
        exc_info=exc_info,
    )
    out = _RedactingFormatter(_LOG_FORMAT).format(record)

    assert "deadbeef" not in out
    assert "<redacted" in out
