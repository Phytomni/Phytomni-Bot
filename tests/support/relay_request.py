# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared request-scope fixtures for relay route tests."""

from __future__ import annotations

from typing import Any


def relay_request_scope() -> dict[str, Any]:
    """Return the minimal HTTP scope used by relay request tests."""
    return {
        "type": "http",
        "method": "POST",
        "path": "/v1/relay/llm/chat/completions",
        "headers": [],
        "query_string": b"",
    }
