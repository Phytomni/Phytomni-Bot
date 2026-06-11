# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Producer-side tests for ``promoter_design_for_gene``.

Pins the new module-level wrapper that deep_genome will reroute its
``promoter_analysis`` branch to in commit 2 (AF-019 producer-first
ordering). Asserts the wrapper passes the analyst-subgraph helper a
request dict matching design's ``_dispatch_and_wait_analysis`` shape
and propagates the helper's return value verbatim.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.design import agent as design_agent
from mcp_server_phytomni.agents.design.agent import (
    promoter_design_for_gene,
)

pytestmark = pytest.mark.agent


def _install_stub_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    submit_return: dict[str, Any] | None = None,
) -> AsyncMock:
    """Patch prompt / data / AnalystAgent / submit deps on design.agent."""
    monkeypatch.setattr(
        design_agent,
        "get_prompt",
        lambda *_a, **_kw: "prompt-stub",
    )
    monkeypatch.setattr(
        design_agent,
        "get_data_list",
        lambda *_a, **_kw: ["obs://data/promoter-input"],
    )
    monkeypatch.setattr(
        design_agent,
        "AnalystAgent",
        lambda **_kw: "analyst-agent-stub",
    )
    submit_mock = AsyncMock(
        return_value=submit_return
        or {
            "task_id": "promoter-task-id",
            "output_dir": "obs://run/promoter-out",
            "task_status": "SUCCEEDED",
        }
    )
    monkeypatch.setattr(
        design_agent, "submit_analyst_via_subgraph", submit_mock
    )
    return submit_mock


async def test_returns_submit_helper_result_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrapper returns the analyst-subgraph helper's projected dict."""
    submit_mock = _install_stub_dependencies(monkeypatch)

    result = await promoter_design_for_gene(
        species_code="ath",
        gene_id="AT1G01010",
    )

    assert result == {
        "task_id": "promoter-task-id",
        "output_dir": "obs://run/promoter-out",
        "task_status": "SUCCEEDED",
    }
    submit_mock.assert_awaited_once()


async def test_request_shape_targets_promoter_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Request dict pins promoter_analysis at small compute tier.

    Compute tier 'small' is hardcoded by the design producer wrapper
    ``promoter_design_for_gene``.
    """
    submit_mock = _install_stub_dependencies(monkeypatch)

    await promoter_design_for_gene(
        species_code="ath",
        gene_id="AT1G01010",
    )

    call_args = submit_mock.await_args
    assert call_args is not None
    request = call_args.args[3]
    assert request["analysis_type"] == "promoter_analysis"
    assert request["target_id"] == "AT1G01010"
    assert isinstance(request["prompt_parts"], tuple)
    assert len(request["prompt_parts"]) == 3
    assert request["compute_resource"] == "small"
    assert call_args.kwargs["is_polling"] is True
