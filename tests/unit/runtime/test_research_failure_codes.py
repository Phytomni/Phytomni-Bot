# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for shared Research terminal-failure code validation."""

from __future__ import annotations

from mcp_server_phytomni.runtime.research_failure_codes import (
    is_research_failure_code,
)


def test_research_failure_code_validation_accepts_only_contract_codes() -> (
    None
):
    """The one shared validator bounds both admission and run projection."""
    assert is_research_failure_code("research_cancel_conflict")
    assert not is_research_failure_code("research_unknown")
    assert not is_research_failure_code(None)


def test_goal_extraction_failed_is_a_public_research_code() -> None:
    """Goal-extraction failure is a first-class public code."""
    assert is_research_failure_code("research_goal_extraction_failed")
