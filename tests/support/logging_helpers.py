# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Logging fixtures shared by tests for non-propagating package loggers."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

__all__ = ["capture_non_propagating_logger"]


@contextmanager
def capture_non_propagating_logger(
    logger_name: str,
    handler: logging.Handler,
) -> Iterator[None]:
    """Attach a capture handler while preserving logger propagation state."""
    logger = logging.getLogger(logger_name)
    previous_propagate = logger.propagate
    logger.propagate = False
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)
        logger.propagate = previous_propagate
