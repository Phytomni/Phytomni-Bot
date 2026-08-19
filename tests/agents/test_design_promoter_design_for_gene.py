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

import pytest

from mcp_server_phytomni.agents.design.agent import (
    promoter_design_for_gene,
)
from tests.support.design_fakes import (
    install_design_dependencies,
    invoke_design_case,
)

pytestmark = pytest.mark.agent


async def test_returns_submit_helper_result_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrapper returns the analyst-subgraph helper's projected dict."""
    submit_mock = install_design_dependencies(
        monkeypatch,
        data_uri="obs://data/promoter-input",
        task_id="promoter-task-id",
        output_dir="obs://run/promoter-out",
    )

    result, _request, _request_kwargs = await invoke_design_case(
        promoter_design_for_gene,
        submit_mock,
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

    Compute tier comes from DigitalDesignConfig.COMPUTE_RESOURCE
    because promoter analysis is not in COMPUTE_RESOURCE_BY_TYPE.
    """
    submit_mock = install_design_dependencies(
        monkeypatch,
        data_uri="obs://data/promoter-input",
        task_id="promoter-task-id",
        output_dir="obs://run/promoter-out",
    )

    _result, request, request_kwargs = await invoke_design_case(
        promoter_design_for_gene,
        submit_mock,
        species_code="ath",
        gene_id="AT1G01010",
    )

    assert request.analysis_type == "promoter_analysis"
    assert request.target_id == "AT1G01010"
    assert request.goal_description == "prompt-stub"
    assert request.meta == "prompt-stub"
    assert request.data_list == {"obs://data/promoter-input": "fixture"}
    assert request.compute_resource == "small"
    assert request_kwargs["is_polling"] is False
