# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared exception types for local durable-operation boundaries."""

from __future__ import annotations

import sqlite3


class RemoteAnalysisSubmissionError(ValueError):
    """Raised when remote analysis submission omits a task id."""


LOCAL_DURABLE_ERRORS = (
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    sqlite3.Error,
)
