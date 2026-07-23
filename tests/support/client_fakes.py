# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Neutral MCP client fakes shared by client behavior tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

from mcp_client_phytomni.client import PhytomniMcpClient

__all__ = ["build_client_call_fakes"]


def build_client_call_fakes() -> tuple[
    PhytomniMcpClient,
    AsyncMock,
    AsyncMock,
]:
    """Build a client, session, and successful empty tool result."""
    client = PhytomniMcpClient()
    fake_session = AsyncMock()
    fake_result = AsyncMock()
    fake_result.isError = False
    fake_result.content = []
    client.session = fake_session
    return client, fake_session, fake_result
