# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Package-level logging configuration for the Phytomni MCP server.

Attaches one stderr handler to the ``mcp_server_phytomni`` logger so the MCP
stdio JSON-RPC channel (stdout) is never polluted. Defaults to ``INFO`` and
elevates to ``DEBUG`` when ``PHYTOMNI_DEBUG`` is set to a truthy value. The
configuration is package-scoped (not the root logger) to avoid hijacking
third-party libraries such as httpx, openai, and langchain.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Final

PHYTOMNI_DEBUG_ENV: Final[str] = "PHYTOMNI_DEBUG"
_PACKAGE_LOGGER_NAME: Final[str] = "mcp_server_phytomni"
_LOG_FORMAT: Final[str] = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_HANDLER_SENTINEL: Final[str] = "_phytomni_log_handler"
_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


def debug_enabled() -> bool:
    """Return True when ``PHYTOMNI_DEBUG`` resolves to a truthy value."""
    raw = os.getenv(PHYTOMNI_DEBUG_ENV, "").strip().lower()
    return raw in _TRUTHY


def configure_logging() -> None:
    """Configure the ``mcp_server_phytomni`` logger for stderr output.

    Idempotent: a repeated call reuses the existing handler (identified by
    the sentinel attribute) and only refreshes the log level so the
    ``PHYTOMNI_DEBUG`` toggle can be re-applied without duplicating output.
    Propagation is disabled so the package logger does not bubble messages
    into a third-party root configuration that might route them to stdout.
    """
    level = logging.DEBUG if debug_enabled() else logging.INFO
    package_logger = logging.getLogger(_PACKAGE_LOGGER_NAME)
    package_logger.setLevel(level)
    package_logger.propagate = False

    for existing in package_logger.handlers:
        if getattr(existing, _HANDLER_SENTINEL, False):
            existing.setLevel(level)
            return

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    setattr(handler, _HANDLER_SENTINEL, True)
    package_logger.addHandler(handler)
