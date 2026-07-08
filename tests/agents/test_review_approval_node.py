# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the ReviewAgent approval node and state channels."""

from __future__ import annotations

from mcp_server_phytomni.agents.review.state import DeepResearchState


def test_state_has_approval_channels() -> None:
    """DeepResearchState declares the approval channels."""
    annotations = DeepResearchState.__annotations__
    assert "approval_pending" in annotations
    assert "approval_decision" in annotations
