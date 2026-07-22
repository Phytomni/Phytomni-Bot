# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Sensitive marker fixtures used by stream-sanitization tests."""

from __future__ import annotations

FORBIDDEN_STREAM_CONTENT = (
    "bearer-secret",
    "postgresql://",
    "db-user:db-password",
    "SELECT secret_token",
)
