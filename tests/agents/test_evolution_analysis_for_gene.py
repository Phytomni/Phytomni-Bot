# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Producer-side tests for ``evolution_analysis_for_gene``.

Pins the new module-level wrapper that deep_genome will reroute its
``evolution_analysis`` branch to in commit 2 (AF-019 producer-first
ordering). Asserts the wrapper passes the analyst-subgraph helper a
request dict matching design's ``_dispatch_and_wait_analysis`` shape
and propagates the helper's return value verbatim.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.evolution import agent as evolution_agent
from mcp_server_phytomni.agents.evolution.agent import (
    evolution_analysis_for_gene,
)

pytestmark = pytest.mark.agent


def _install_stub_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    submit_return: dict[str, Any] | None = None,
) -> AsyncMock:
    """Patch prompt / data / AnalystAgent / submit deps on evolution.agent."""
    monkeypatch.setattr(
        evolution_agent,
        "get_prompt",
        lambda *_a, **_kw: "prompt-stub",
    )
    monkeypatch.setattr(
        evolution_agent,
        "get_data_list",
        lambda *_a, **_kw: ["obs://data/evo-input"],
    )
    monkeypatch.setattr(
        evolution_agent,
        "AnalystAgent",
        lambda **_kw: "analyst-agent-stub",
    )
    submit_mock = AsyncMock(
        return_value=submit_return
        or {
            "task_id": "evo-task-id",
            "output_dir": "obs://run/evo-out",
            "task_status": "SUCCEEDED",
        }
    )
    monkeypatch.setattr(
        evolution_agent, "submit_analyst_via_subgraph", submit_mock
    )
    return submit_mock


async def test_returns_submit_helper_result_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrapper returns the analyst-subgraph helper's projected dict.

    The helper's projection (``map_analyst_output_to_dispatch_state``)
    is the contract deep_genome dispatch consumes at lines 501-503;
    the wrapper must not reshape it.
    """
    submit_mock = _install_stub_dependencies(monkeypatch)

    result = await evolution_analysis_for_gene(
        species_code="ath",
        gene_id="AT1G01010",
    )

    assert result == {
        "task_id": "evo-task-id",
        "output_dir": "obs://run/evo-out",
        "task_status": "SUCCEEDED",
    }
    submit_mock.assert_awaited_once()


async def test_request_shape_targets_evolution_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Request dict carries the 5 contract keys design's helper expects.

    Pins ``analysis_type='evolution_analysis'``, ``target_id`` =
    gene_id, ``prompt_parts`` is a 3-tuple, ``compute_resource`` =
    'medium' (per deep_genome dispatch MEDIUM_COMPUTE_ANALYSIS_TYPES).
    """
    submit_mock = _install_stub_dependencies(monkeypatch)

    await evolution_analysis_for_gene(
        species_code="ath",
        gene_id="AT1G01010",
    )

    call_args = submit_mock.await_args
    assert call_args is not None
    request = call_args.args[3]
    assert request["analysis_type"] == "evolution_analysis"
    assert request["target_id"] == "AT1G01010"
    assert isinstance(request["prompt_parts"], tuple)
    assert len(request["prompt_parts"]) == 3
    assert request["compute_resource"] == "medium"
    assert call_args.kwargs["is_polling"] is True
