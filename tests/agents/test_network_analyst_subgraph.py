# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Dispatch tests for GeneNetworkAgents analyst-subgraph routing.

Mirrors ``test_design_analyst_subgraph``: asserts
``_dispatch_and_wait_analysis`` unconditionally routes through
``submit_analyst_via_subgraph`` (the structural-mount path). The
subgraph adapter is unit-tested in the design sibling; this file only
pins the network dispatcher's routing decision and ``to_id`` mapping.
"""

# The direct dispatcher probes below target the smallest network routing seam;
# each carries a symbol-scoped protected-access directive.

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.analyst.agent import AnalystAgent
from mcp_server_phytomni.agents.network.agent import (
    GeneNetworkAgents,
    GeneNetworkConfig,
    GeneNetworkState,
)
from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.runtime import run_registry_reports
from mcp_server_phytomni.storage.artifact_listing import ListedArtifactObject

from ._subgraph_branch_fakes import stub_prompt_parts

pytestmark = pytest.mark.agent

_NETWORK_MODULE = "mcp_server_phytomni.agents.network.agent"


def _build_agent() -> GeneNetworkAgents:
    """Construct a network dispatcher wired with an analyst stub."""
    analyst_stub = SimpleNamespace(identifier=lambda: "stub-analyst")
    return GeneNetworkAgents(
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, analyst_stub),
        gene_network_config=GeneNetworkConfig(),
    )


async def test_dispatch_routes_through_subgraph_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatch always calls ``submit_analyst_via_subgraph`` exclusively.

    The structural-mount path replaces the direct-``arun`` call with
    the compiled subgraph entry; the test asserts the legacy helper is
    never awaited so the network dispatcher matches the design
    dispatcher's routing semantics.
    """
    agent = _build_agent()
    legacy_mock = AsyncMock(return_value={"task_id": "legacy-task"})
    subgraph_mock = AsyncMock(return_value={"task_id": "subgraph-task"})
    monkeypatch.setattr(
        f"{_NETWORK_MODULE}.submit_analyst_via_subgraph", subgraph_mock
    )
    stub_prompt_parts(monkeypatch, agent)

    result = await getattr(agent, "_dispatch_and_wait_analysis")(
        analysis_type="gene_network_analysis",
        species_code="ath",
        to_id="TO:0000621",
        output_dir="/tmp/network-out",
    )

    assert result == {"task_id": "subgraph-task"}
    subgraph_mock.assert_awaited_once()
    legacy_mock.assert_not_awaited()


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
    agent = _build_agent()
    subgraph_mock = AsyncMock(return_value={"task_id": "subgraph-task"})
    monkeypatch.setattr(
        f"{_NETWORK_MODULE}.submit_analyst_via_subgraph", subgraph_mock
    )
    monkeypatch.setattr(
        agent,
        "_analysis_prompt_parts",
        lambda *_a, **_kw: ("goal", "meta", {}),
    )

    await getattr(agent, "_dispatch_and_wait_analysis")(
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
    # Network keeps fire-and-poll-elsewhere semantics: the helper is
    # called with is_polling=False (the submit-return default).
    assert call_args.kwargs["is_polling"] is False


def test_network_analysis_prompt_requires_artifact_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Network producers must describe files for report and ZIP delivery."""
    agent = _build_agent()
    monkeypatch.setattr(
        f"{_NETWORK_MODULE}.get_prompt",
        lambda _path, key, *_args: (
            "goal" if key.endswith("analysis") else "meta"
        ),
    )
    monkeypatch.setattr(f"{_NETWORK_MODULE}.get_data_list", lambda *_args: {})

    _goal, instructions, _data = getattr(agent, "_analysis_prompt_parts")(
        "gene_network_analysis", "osa", "TO:0000011"
    )

    assert ".phytomni-artifacts.json" in instructions
    assert "scientific_report" in instructions


async def test_prepare_tasks_reallocates_shared_default_output_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured dump prefix is not an allocated per-run result root."""
    agent = _build_agent()
    allocated = "/obs/phytomni/users/user-1/gene_network_task/run-unique"
    create_output = AsyncMock(return_value=allocated)
    monkeypatch.setattr(f"{_NETWORK_MODULE}.create_output_dir", create_output)

    result = await agent.prepare_tasks(
        cast(
            GeneNetworkState,
            {
                "to_id": "TO:0000621",
                "species_code": "osa",
                "user_id": "user-1",
                "output_dir": f"{agent.gene_network_config.OUTPUT_DIR}/children/part-001",
            },
        )
    )

    assert result["output_dir"] == allocated
    assert result["network_tasks"][0]["output_dir"] == (
        f"{allocated}/children/part-001"
    )
    create_output.assert_awaited_once()


async def test_prepare_tasks_preserves_caller_owned_output_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit root outside the configured dump remains reusable."""
    agent = _build_agent()
    create_output = AsyncMock(return_value="/obs/phytomni/unexpected")
    monkeypatch.setattr(f"{_NETWORK_MODULE}.create_output_dir", create_output)
    caller_root = "/obs/phytomni/users/user-1/gene_network_task/caller-run"

    result = await agent.prepare_tasks(
        cast(
            GeneNetworkState,
            {
                "to_id": "TO:0000621",
                "species_code": "osa",
                "user_id": "user-1",
                "output_dir": caller_root,
            },
        )
    )

    assert result["output_dir"] == caller_root
    assert result["network_tasks"][0]["output_dir"] == (
        f"{caller_root}/children/part-001"
    )
    create_output.assert_not_awaited()


async def test_reallocated_network_root_is_the_only_harvest_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminal collection enumerates the fresh child, never the dump root."""
    agent = _build_agent()
    allocated = "/obs/phytomni/users/user-1/gene_network_task/run-isolated"
    monkeypatch.setattr(
        f"{_NETWORK_MODULE}.create_output_dir",
        AsyncMock(return_value=allocated),
    )
    prepared = await agent.prepare_tasks(
        cast(
            GeneNetworkState,
            {
                "to_id": "TO:0000621",
                "species_code": "osa",
                "user_id": "user-1",
                "output_dir": agent.gene_network_config.OUTPUT_DIR,
            },
        )
    )
    child_output = prepared["network_tasks"][0]["output_dir"]
    listed: list[str] = []

    async def object_lister(output_dir: str) -> list[ListedArtifactObject]:
        listed.append(output_dir)
        return [
            ListedArtifactObject(
                relative_path="network.json",
                source_path=f"{output_dir}/network.json",
                size_bytes=16,
                download_ref=f"{output_dir}/network.json",
            )
        ]

    async def manifest_loader(_output_dir: str) -> dict[str, object]:
        return {
            "version": "1.0",
            "artifacts": [
                {
                    "path": "network.json",
                    "role": "scientific_data",
                    "media_type": "application/json",
                }
            ],
        }

    groups = await run_registry_reports.collect_report_artifact_groups(
        [
            {
                "task_id": "network-child",
                "status": "succeeded",
                "output_dir": child_output,
            }
        ],
        lister=None,
        object_lister=object_lister,
        manifest_loader=manifest_loader,
    )

    assert listed == [f"{allocated}/children/part-001"]
    assert [
        artifact.relative_path for artifact in groups[0].artifact_set.artifacts
    ] == ["network.json"]
