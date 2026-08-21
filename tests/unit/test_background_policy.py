# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for the detached generic background-run policy."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.api import app as api_app
from mcp_server_phytomni.runtime.background_policy import (
    BACKGROUND_SUBMISSION_AGENT_SLUGS,
    is_detached_background_run,
)

pytestmark = pytest.mark.unit

_EXPECTED = frozenset(
    {"analyst", "research", "network", "design", "data", "review"}
)


def test_background_submission_agent_set_is_exact() -> None:
    """The API compatibility seam aliases the canonical policy object."""
    assert BACKGROUND_SUBMISSION_AGENT_SLUGS == _EXPECTED
    assert getattr(api_app, "_BACKGROUND_SUBMISSION_AGENT_SLUGS") is (
        BACKGROUND_SUBMISSION_AGENT_SLUGS
    )


@pytest.mark.parametrize("agent", sorted(_EXPECTED))
def test_remote_canonical_agents_use_detached_policy(agent: str) -> None:
    """Only canonical remote agents use the detached-worker contract."""
    assert is_detached_background_run(agent=agent, origin="remote") is True


@pytest.mark.parametrize(
    ("agent", "origin"),
    [
        ("deep_genome", "remote"),
        ("chat", "remote"),
        ("analyst", "local"),
        ("unknown", "remote"),
    ],
)
def test_non_policy_runs_do_not_use_detached_policy(
    agent: str,
    origin: str,
) -> None:
    """Non-members and local runs retain their existing lifecycle path."""
    assert is_detached_background_run(agent=agent, origin=origin) is False
