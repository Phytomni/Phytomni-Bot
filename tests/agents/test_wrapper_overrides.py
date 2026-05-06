# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for public wrapper override propagation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from mcp_server_phytomni import (
    analysis_workflow_helpers,
    digital_design_agents,
    gene_network_agents,
    in_silico_research_agents,
)

pytestmark = pytest.mark.agent


def _no_cache(
    name: str,
    factory: Callable[[], Any],
    fingerprint_values: Any = None,
) -> Any:
    """Verify no cache."""
    cache_identity = (name, fingerprint_values)
    assert cache_identity[0]
    return factory()


async def test_design_module_applies_config_and_secret_overrides(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify design module applies config and secret overrides."""
    captured: dict[str, Any] = {}

    class FakeDigitalDesignAgents:
        """Fake workflow that records constructor and run arguments."""

        def __init__(self, digital_design_config, sensitive_config):
            """Verify init  ."""
            captured["config"] = digital_design_config
            captured["sensitive"] = sensitive_config

        async def arun(
            self,
            species: str,
            gene_id: str,
            **kwargs: Any,
        ) -> dict[str, Any]:
            """Verify arun."""
            captured["run"] = {
                "species": species,
                "gene_id": gene_id,
                "user_id": kwargs.get("user_id"),
                "batch": kwargs.get("batch", False),
                "output_dir": kwargs.get("output_dir"),
            }
            return {"design": "ok"}

        def captured_config(self) -> Any:
            """Return the recorded config object."""
            return captured["config"]

    monkeypatch.setattr(
        digital_design_agents,
        "DigitalDesignAgents",
        FakeDigitalDesignAgents,
    )
    monkeypatch.setattr(
        analysis_workflow_helpers, "get_cached_agent", _no_cache
    )

    result = await digital_design_agents.design_module(
        species="osa",
        gene_id="gene-1",
        user_id="user-1",
        batch=False,
        output_dir="/tmp/design",
        deepgenome_data="/data/design.json",
        model_url="https://coder.example",
        model_name="coder-model",
        coder_api_key="coder-secret",
    )

    assert result == {"design": "ok"}
    assert captured["config"].USER_ID == "user-1"
    assert captured["config"].OUTPUT_DIR == "/tmp/design"
    assert captured["config"].DEEPGENOME_DATA == "/data/design.json"
    assert captured["sensitive"].CODER_URL == "https://coder.example"
    assert captured["sensitive"].CODER_MODEL == "coder-model"
    assert (
        captured["sensitive"].CODER_API_KEY.get_secret_value()
        == "coder-secret"
    )
    assert captured["run"]["output_dir"] == "/tmp/design"


async def test_network_analysis_applies_config_and_secret_overrides(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify network analysis applies config and secret overrides."""
    captured: dict[str, Any] = {}

    class FakeGeneNetworkAgents:
        """Fake workflow that records constructor and run arguments."""

        def __init__(self, gene_network_config, sensitive_config):
            """Verify init  ."""
            captured["config"] = gene_network_config
            captured["sensitive"] = sensitive_config

        async def arun(
            self,
            species: str,
            to_id: str,
            **kwargs: Any,
        ) -> dict[str, Any]:
            """Verify arun."""
            captured["run"] = {
                "species": species,
                "to_id": to_id,
                "user_id": kwargs.get("user_id"),
                "batch": kwargs.get("batch", False),
                "output_dir": kwargs.get("output_dir"),
            }
            return {"network": "ok"}

        def captured_config(self) -> Any:
            """Return the recorded config object."""
            return captured["config"]

    monkeypatch.setattr(
        gene_network_agents,
        "GeneNetworkAgents",
        FakeGeneNetworkAgents,
    )
    monkeypatch.setattr(
        analysis_workflow_helpers, "get_cached_agent", _no_cache
    )

    result = await gene_network_agents.network_analysis(
        species="osa",
        to_id="to-1",
        user_id="user-2",
        batch=True,
        output_dir="/tmp/network",
        deepgenome_data="/data/network.json",
        model_url="https://coder.example",
        coder_api_key="coder-secret",
    )

    assert result == {"network": "ok"}
    assert captured["config"].USER_ID == "user-2"
    assert captured["config"].OUTPUT_DIR == "/tmp/network"
    assert captured["config"].DEEPGENOME_DATA == "/data/network.json"
    assert captured["sensitive"].CODER_URL == "https://coder.example"
    assert (
        captured["sensitive"].CODER_API_KEY.get_secret_value()
        == "coder-secret"
    )
    assert captured["run"]["output_dir"] == "/tmp/network"


async def test_in_silico_research_applies_config_and_secret_overrides(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify in silico research applies config and secret overrides."""
    captured: dict[str, Any] = {}

    class FakeInSilicoResearchAgents:
        """Fake workflow that records constructor and run arguments."""

        def __init__(self, in_silico_config, sensitive_config):
            """Verify init  ."""
            captured["config"] = in_silico_config
            captured["sensitive"] = sensitive_config

        async def arun(
            self,
            paper_text: str,
            data_list: dict[str, str],
            **kwargs: Any,
        ) -> dict[str, Any]:
            """Verify arun."""
            captured["run"] = {
                "paper_text": paper_text,
                "data_list": data_list,
                "user_id": kwargs.get("user_id"),
                "obs_file_list": kwargs.get("obs_file_list"),
                "output_dir": kwargs.get("output_dir"),
            }
            return {"research": "ok"}

        def captured_config(self) -> Any:
            """Return the recorded config object."""
            return captured["config"]

    monkeypatch.setattr(
        in_silico_research_agents,
        "InSilicoResearchAgents",
        FakeInSilicoResearchAgents,
    )
    monkeypatch.setattr(
        in_silico_research_agents,
        "get_cached_agent",
        _no_cache,
    )

    result = await in_silico_research_agents.in_silico_research(
        user_query="paper text",
        data_list={"input": "obs://data"},
        user_id="user-3",
        obs_file_list=["obs://paper.pdf"],
        output_dir="/tmp/research",
        repo_id_dict={"paper": 1},
        model_url="https://coder.example",
        coder_api_key="coder-secret",
    )

    assert result == {"research": "ok"}
    assert captured["config"].USER_ID == "user-3"
    assert captured["config"].OUTPUT_DIR == "/tmp/research"
    assert captured["config"].REPO_ID_DICT == {"paper": 1}
    assert captured["sensitive"].CODER_URL == "https://coder.example"
    assert (
        captured["sensitive"].CODER_API_KEY.get_secret_value()
        == "coder-secret"
    )
    assert captured["run"]["output_dir"] == "/tmp/research"
    assert captured["run"]["data_list"] == {"input": "obs://data"}
