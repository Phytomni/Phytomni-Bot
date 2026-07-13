# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Contract tests for the Research/Design interop request controls.

The controls are explicit per-request policy in C4.7. They must be accepted
by the MCP schema, projected into the OpenAI function-tool schema, and survive
the native HTTP run seam without changing the default local-only behavior.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from mcp_server_phytomni import server
from mcp_server_phytomni.mcp.schemas import (
    DigitalDesignAgent,
    InSilicoResearchAgent,
    agent_openai_tool_specs,
)
from mcp_server_phytomni.runtime.submit_recorder import records_submission

pytestmark = pytest.mark.server


def test_research_and_design_default_to_local_only() -> None:
    """Omitting controls keeps both public tools in ``off`` mode."""
    research = InSilicoResearchAgent(
        user_query="paper",
        data_list={},
        obs_file_list=[],
    )
    design = DigitalDesignAgent(
        species_code="ath",
        gene_id="AT1G01010",
        obs_file_list=[],
    )

    assert research.interop_mode == "off"
    assert research.interop_targets == []
    assert design.interop_mode == "off"
    assert design.interop_targets == []


@pytest.mark.parametrize(
    "model",
    [InSilicoResearchAgent, DigitalDesignAgent],
)
def test_interop_mode_is_closed_and_targets_are_explicit(
    model: type[InSilicoResearchAgent | DigitalDesignAgent],
) -> None:
    """Both tools accept only the three planned modes and target ids."""
    base: dict[str, Any]
    if model is InSilicoResearchAgent:
        base = {
            "user_query": "paper",
            "data_list": {},
            "obs_file_list": [],
        }
    else:
        base = {
            "species_code": "ath",
            "gene_id": "AT1G01010",
            "obs_file_list": [],
        }

    for mode in ("off", "auto", "required"):
        instance = model(
            **base,
            interop_mode=mode,
            interop_targets=["mcp-peer", "a2a-peer"],
        )
        assert instance.interop_mode == mode
        assert instance.interop_targets == ["mcp-peer", "a2a-peer"]

    invalid_mode: Any = "always"
    with pytest.raises(ValidationError):
        model(**base, interop_mode=invalid_mode)


def test_openai_tool_specs_project_controls_for_research_and_design() -> None:
    """OpenAI routing receives the same optional control fields as MCP."""
    specs = {
        spec["function"]["name"]: spec["function"]["parameters"]
        for spec in agent_openai_tool_specs()
    }

    for tool_name in ("InSilicoResearchAgent", "DigitalDesignAgent"):
        properties = specs[tool_name]["properties"]
        assert properties["interop_mode"]["enum"] == [
            "off",
            "auto",
            "required",
        ]
        assert properties["interop_mode"]["default"] == "off"
        assert properties["interop_targets"]["type"] == "array"
        assert "interop_mode" not in specs[tool_name].get("required", [])
        assert "interop_targets" not in specs[tool_name].get("required", [])
        description = properties["interop_mode"]["description"]
        assert "never discovers or invokes a peer" in description
        assert "fails the request" in description


async def test_native_http_runs_forward_controls_to_handler(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native HTTP runs validate and forward the opt-in controls."""
    captured: dict[str, Any] = {}

    async def fake(args: Any) -> dict[str, Any]:
        captured["mode"] = args.interop_mode
        captured["targets"] = args.interop_targets
        return {
            "task_ids": {"goal": "T-INTEROP"},
            "output_dir": "/obs/research",
        }

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.IN_SILICO_RESEARCH_AGENT.value,
        records_submission("research")(fake),
    )

    response = await api_client.post(
        "/v1/agents/research/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "arguments": {
                "user_query": "paper",
                "data_list": {},
                "obs_file_list": [],
                "interop_mode": "required",
                "interop_targets": ["mcp-peer"],
            }
        },
    )

    assert response.status_code == 202
    assert captured == {"mode": "required", "targets": ["mcp-peer"]}
