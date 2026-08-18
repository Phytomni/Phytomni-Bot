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

import hashlib
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from mcp_server_phytomni.api.research_input import (
    ResearchClientFingerprintInput,
    compute_research_client_fingerprint,
)
from mcp_server_phytomni.mcp.schemas import (
    DigitalDesignAgent,
    InSilicoResearchAgent,
    agent_openai_tool_specs,
)
from mcp_server_phytomni.runtime.research_input_store import ResearchInputStore
from mcp_server_phytomni.runtime.run_registry import RunRegistry

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
    tasks_db_path: str,
) -> None:
    """Native HTTP admission fingerprints the opt-in controls."""

    response = await api_client.post(
        "/v1/agents/research/runs",
        headers={
            "Authorization": f"Bearer {issued_api_key}",
            "Idempotency-Key": "test-interop-research",
        },
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
    body = response.json()
    assert body["task_ids"] == []
    registry = RunRegistry(tasks_db_path)
    record = registry.get_run(body["run_id"], owner="u1")
    assert record is not None
    assert record.status == "running"
    assert not record.task_ids
    resolution = ResearchInputStore(tasks_db_path).load_resolution(
        body["run_id"]
    )
    assert resolution is not None
    assert resolution["status"] == "pending"
    assert resolution["effective_query"] == "paper"
    assert resolution["client_fingerprint"] == (
        compute_research_client_fingerprint(
            ResearchClientFingerprintInput(
                original_query_digest=hashlib.sha256(b"paper").hexdigest(),
                original_query_length=len("paper"),
                managed_asset_ids=(),
                locale="en-US",
                interop_mode="required",
                interop_targets=("mcp-peer",),
                conversation_identity_digest=None,
            )
        )
    )
