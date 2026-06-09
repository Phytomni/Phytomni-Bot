# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for public wrapper override propagation.

Covers design, network, and in-silico wrapper config overrides, secret
overrides, cached-agent bypassing, and forwarded run arguments.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from mcp_server_phytomni.agents.design import agent as digital_design_agents
from mcp_server_phytomni.agents.network import agent as gene_network_agents
from mcp_server_phytomni.agents.research import (
    agent as in_silico_research_agents,
)
from mcp_server_phytomni.agents.shared import analysis as analysis_helpers

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
    """Verify design_module applies config and secret overrides.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace agent cache.

    Returns:
        None after wrapper override assertions pass.
    """
    captured: dict[str, Any] = {}

    class FakeDigitalDesignAgents:
        """Fake workflow that records constructor and run arguments.

        Attributes:
            Constructor and run inputs are stored in the outer captured dict.
        """

        def __init__(self, digital_design_config, sensitive_config):
            """Verify init  ."""
            captured["config"] = digital_design_config
            captured["sensitive"] = sensitive_config

        async def arun(
            self,
            species_code: str,
            gene_id: str,
            **kwargs: Any,
        ) -> dict[str, Any]:
            """Capture design run arguments.

            Args:
                species_code: Species code forwarded by design_module.
                gene_id: Target gene forwarded by design_module.
                **kwargs: Additional wrapper options forwarded to arun.

            Returns:
                Minimal design result payload.
            """
            captured["run"] = {
                "species_code": species_code,
                "gene_id": gene_id,
                "user_id": kwargs.get("user_id"),
                "batch": kwargs.get("batch", False),
                "output_dir": kwargs.get("output_dir"),
            }
            return {"design": "ok"}

        def captured_config(self) -> Any:
            """Return the recorded config object.

            Returns:
                Captured DigitalDesignConfig-like object.
            """
            return captured["config"]

    monkeypatch.setattr(
        digital_design_agents,
        "DigitalDesignAgents",
        FakeDigitalDesignAgents,
    )
    monkeypatch.setattr(analysis_helpers, "get_cached_agent", _no_cache)

    result = await digital_design_agents.design_module(
        species_code="osa",
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
    """Verify network_analysis applies config and secret overrides.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace agent cache.

    Returns:
        None after wrapper override assertions pass.
    """
    captured: dict[str, Any] = {}

    class FakeGeneNetworkAgents:
        """Fake workflow that records constructor and run arguments.

        Attributes:
            Constructor and run inputs are stored in the outer captured dict.
        """

        def __init__(self, gene_network_config, sensitive_config):
            """Verify init  ."""
            captured["config"] = gene_network_config
            captured["sensitive"] = sensitive_config

        async def arun(
            self,
            species_code: str,
            to_id: str,
            **kwargs: Any,
        ) -> dict[str, Any]:
            """Capture network run arguments.

            Args:
                species_code: Species code forwarded by network_analysis.
                to_id: Target id forwarded by network_analysis.
                **kwargs: Additional wrapper options forwarded to arun.

            Returns:
                Minimal network result payload.
            """
            captured["run"] = {
                "species_code": species_code,
                "to_id": to_id,
                "user_id": kwargs.get("user_id"),
                "batch": kwargs.get("batch", False),
                "output_dir": kwargs.get("output_dir"),
            }
            return {"network": "ok"}

        def captured_config(self) -> Any:
            """Return the recorded config object.

            Returns:
                Captured GeneNetworkConfig-like object.
            """
            return captured["config"]

    monkeypatch.setattr(
        gene_network_agents,
        "GeneNetworkAgents",
        FakeGeneNetworkAgents,
    )
    monkeypatch.setattr(analysis_helpers, "get_cached_agent", _no_cache)

    result = await gene_network_agents.network_analysis(
        species_code="osa",
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
    """Verify in_silico_research applies config and secret overrides.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace agent cache.

    Returns:
        None after wrapper override assertions pass.
    """
    captured: dict[str, Any] = {}

    class FakeInSilicoResearchAgents:
        """Fake workflow that records constructor and run arguments.

        Attributes:
            Constructor and run inputs are stored in the outer captured dict.
        """

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
            """Capture research run arguments.

            Args:
                paper_text: Paper text forwarded by in_silico_research.
                data_list: Data list forwarded by in_silico_research.
                **kwargs: Additional wrapper options forwarded to arun.

            Returns:
                Minimal research result payload.
            """
            captured["run"] = {
                "paper_text": paper_text,
                "data_list": data_list,
                "user_id": kwargs.get("user_id"),
                "obs_file_list": kwargs.get("obs_file_list"),
                "output_dir": kwargs.get("output_dir"),
            }
            return {"research": "ok"}

        def captured_config(self) -> Any:
            """Return the recorded config object.

            Returns:
                Captured InSilicoResearchConfig-like object.
            """
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
