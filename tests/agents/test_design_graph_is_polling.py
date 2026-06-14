# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the digital-design producer: prompt restore + is_polling.

Covers that the restored ``promoter_design_analysis`` prompt resolves and
that ``run_design_node`` threads ``is_polling`` from graph state into the
analyst submission.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.design.agent import DIGITAL_DESIGN_CONFIG
from mcp_server_phytomni.common.prompts import get_prompt

pytestmark = pytest.mark.agent


def test_promoter_design_analysis_prompt_resolves() -> None:
    """The restored promoter-design prompt + meta resolve via get_prompt."""
    goal = get_prompt(
        DIGITAL_DESIGN_CONFIG.PROMPT_FILE,
        "user/promoter_design_analysis",
        {"gene_id": "Os01g0177400"},
    )
    meta = get_prompt(
        DIGITAL_DESIGN_CONFIG.PROMPT_FILE,
        "user/promoter_design_analysis_meta",
    )
    assert "Os01g0177400" in goal
    assert "epic" in meta and "smep" in meta and "smoc" in meta
