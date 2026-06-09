# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Flag-branch tests for GeneNetworkAgents analyst-subgraph dispatch.

Mirrors ``test_design_analyst_subgraph``: asserts
``_dispatch_and_wait_analysis`` calls ``submit_analyst_analysis``
when ``USE_ANALYST_SUBGRAPH=False`` and
``submit_analyst_via_subgraph`` when ``True``. The subgraph
adapter is unit-tested in the design sibling; this file only
pins the network dispatcher's flag-routing decision.
"""

# pylint: disable=protected-access
# Test file exercises ``GeneNetworkAgents._dispatch_and_wait_analysis``
# directly to assert flag routing, mirroring ``test_design_helpers``.

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.network.agent import (
    GeneNetworkAgents,
    GeneNetworkConfig,
)

from ._subgraph_branch_fakes import (
    assert_branch_taken,
    build_branch_agent,
    install_branch_mocks,
    stub_prompt_parts,
)

pytestmark = pytest.mark.agent

_NETWORK_MODULE = "mcp_server_phytomni.agents.network.agent"


def _build_agent(use_subgraph: bool) -> GeneNetworkAgents:
    """Construct a network dispatcher with the flag set per the arg."""
    return build_branch_agent(
        GeneNetworkConfig,
        GeneNetworkAgents,
        "gene_network_config",
        use_subgraph,
    )


async def test_dispatch_uses_legacy_submit_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default flag-off path calls the legacy ``submit_analyst_analysis``.

    Pins the production-default routing decision for the gene
    network dispatcher so a typo or stale wire-up of the flag check
    in ``_dispatch_and_wait_analysis`` surfaces here before changing
    user-visible behavior.
    """
    agent = _build_agent(use_subgraph=False)
    legacy_mock, subgraph_mock = install_branch_mocks(
        monkeypatch, _NETWORK_MODULE
    )
    stub_prompt_parts(monkeypatch, agent)

    result = await agent._dispatch_and_wait_analysis(
        analysis_type="gene_network_analysis",
        species_code="ath",
        to_id="TO:0000621",
        output_dir="/tmp/network-out",
    )

    assert_branch_taken(result, legacy_mock, subgraph_mock, subgraph=False)


async def test_dispatch_uses_subgraph_submit_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-on path calls ``submit_analyst_via_subgraph`` exclusively.

    The opt-in path replaces the direct-``arun`` call with the
    compiled subgraph entry; the test asserts the legacy helper is
    bypassed so the flag's behavior is binary (no double-dispatch,
    no fallback) and the network dispatcher matches the design
    dispatcher's routing semantics.
    """
    agent = _build_agent(use_subgraph=True)
    legacy_mock, subgraph_mock = install_branch_mocks(
        monkeypatch, _NETWORK_MODULE
    )
    stub_prompt_parts(monkeypatch, agent)

    result = await agent._dispatch_and_wait_analysis(
        analysis_type="gene_network_analysis",
        species_code="ath",
        to_id="TO:0000621",
        output_dir="/tmp/network-out",
    )

    assert_branch_taken(result, legacy_mock, subgraph_mock, subgraph=True)


async def test_dispatch_request_carries_to_id_as_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The request payload threads ``to_id`` into ``target_id`` verbatim.

    Network dispatchers identify the target by ``to_id`` (e.g. a
    GO/TO ontology id) while design uses ``gene_id``; the test pins
    the network-specific mapping so a copy-paste regression from
    the design dispatcher fails loudly rather than silently mixing
    up the target identifier downstream.
    """
    agent = _build_agent(use_subgraph=True)
    monkeypatch.setattr(
        f"{_NETWORK_MODULE}.submit_analyst_analysis", AsyncMock()
    )
    subgraph_mock = AsyncMock(return_value={"task_id": "subgraph-task"})
    monkeypatch.setattr(
        f"{_NETWORK_MODULE}.submit_analyst_via_subgraph", subgraph_mock
    )
    monkeypatch.setattr(
        agent,
        "_analysis_prompt_parts",
        lambda *_a, **_kw: ("goal", "meta", {}),
    )

    await agent._dispatch_and_wait_analysis(
        analysis_type="gene_network_analysis",
        species_code="ath",
        to_id="TO:0000621",
        output_dir="/tmp/network-out",
    )

    call_args = subgraph_mock.await_args
    assert call_args is not None
    request = call_args.args[3]
    assert request["target_id"] == "TO:0000621"
    assert request["analysis_type"] == "gene_network_analysis"
    assert request["output_dir"] == "/tmp/network-out"
    # Network preserves its current fire-and-poll-elsewhere semantics:
    # must override the producer-side default of True.
    assert call_args.kwargs["is_polling"] is False
