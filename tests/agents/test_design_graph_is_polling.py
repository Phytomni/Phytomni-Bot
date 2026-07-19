# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the digital-design producer: prompt restore + is_polling.

Covers that the restored ``promoter_design_analysis`` prompt resolves and
that ``run_design_node`` threads ``is_polling`` from graph state into the
analyst submission.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from mcp_server_phytomni.agents.design.agent import (
    DIGITAL_DESIGN_CONFIG,
    DigitalDesignAgents,
)
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


@pytest.mark.parametrize("polling", [True, False])
async def test_run_design_node_threads_is_polling(polling: bool) -> None:
    """run_design_node forwards state['is_polling'] to the analyst submit."""
    agents = DigitalDesignAgents()
    captured: dict = {}

    async def _fake_submit(*_args, **kwargs):
        captured["is_polling"] = kwargs.get("is_polling")
        return {
            "task_id": "t1",
            "output_dir": "/obs/o",
            "task_status": "SUCCEEDED",
        }

    state: Any = {
        "species_code": "osa",
        "gene_id": "Os01g0177400",
        "analysis_type": "protein_design_analysis",
        "task_index": 0,
        "is_polling": polling,
        "output_dir": None,
        "task_ids": {},
    }
    with patch(
        "mcp_server_phytomni.agents.design.agent.submit_remote_analysis",
        new=AsyncMock(side_effect=_fake_submit),
    ):
        await agents.run_design_node(state)

    assert captured["is_polling"] is polling
