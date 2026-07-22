# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared environment graph monkeypatches for offline tests."""

from __future__ import annotations

from typing import Any

from mcp_server_phytomni.agents.environment import graph as environment_graph

__all__ = ["install_environment_submitter"]


def install_environment_submitter(
    monkeypatch: Any,
    submit_mock: Any,
) -> None:
    """Patch environment graph dispatch and cached-agent construction."""
    monkeypatch.setattr(
        environment_graph,
        "submit_analyst_via_subgraph",
        submit_mock,
    )
    monkeypatch.setattr(
        environment_graph,
        "_build_submit_agent",
        lambda *_args, **_kwargs: (
            "analyst-agent-stub",
            "",
            "small",
            "thread-x",
        ),
    )
