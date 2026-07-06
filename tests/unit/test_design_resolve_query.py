# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline unit tests for the DigitalDesign gene-id resolver wrapper."""

from __future__ import annotations

import json
from typing import Any

import pytest

from mcp_server_phytomni.agents.brief_gene import resolve_query as bga_module
from mcp_server_phytomni.agents.chat import service as chat_service
from mcp_server_phytomni.agents.design.resolve_query import (
    DigitalDesignIdCandidate,
    DigitalDesignResolveError,
    resolve_design_user_query,
)
from mcp_server_phytomni.config.defaults import DigitalDesignConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_phyto_chat_cache() -> None:
    """Drop the persistent SQLite cache between cases."""
    chat_service.run_phyto_chat_cached.cache_clear()


def _make_response(payload: Any) -> dict[str, Any]:
    """Wrap an LLM payload into the OpenAI chat-completion shape."""
    return {"choices": [{"message": {"content": json.dumps(payload)}}]}


@pytest.fixture(name="configs")
def _configs() -> tuple[DigitalDesignConfig, SensitiveConfig]:
    """Return shared config defaults usable across all resolver cases."""
    return DigitalDesignConfig(), SensitiveConfig.load()


async def test_resolver_returns_design_typed_result(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[DigitalDesignConfig, SensitiveConfig],
) -> None:
    """Happy path delegates to BGA and returns Design-typed result."""

    async def fake_phyto_chat(**_kwargs: Any) -> dict[str, Any]:
        return _make_response({"gene_id": "AT1G01010", "species_code": "ath"})

    monkeypatch.setattr(bga_module, "phyto_chat", fake_phyto_chat)
    design_config, sensitive_config = configs

    result = await resolve_design_user_query(
        "design AT1G01010 promoter",
        design_config=design_config,
        sensitive_config=sensitive_config,
    )

    assert result.gene_id == "AT1G01010"
    assert result.species_code == "ath"
    assert result.raw_query == "design AT1G01010 promoter"
    assert len(result.candidates) == 1
    assert isinstance(result.candidates[0], DigitalDesignIdCandidate)
    assert result.candidates[0].species_code == "ath"


async def test_resolver_maps_bga_error_to_design_error(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[DigitalDesignConfig, SensitiveConfig],
) -> None:
    """BriefGeneResolveError raised inside BGA maps to the design type."""

    async def fake_phyto_chat(**_kwargs: Any) -> dict[str, Any]:
        return _make_response(
            {"gene_id": "", "species_code": "ath", "candidates": []}
        )

    monkeypatch.setattr(bga_module, "phyto_chat", fake_phyto_chat)
    design_config, sensitive_config = configs

    with pytest.raises(DigitalDesignResolveError) as excinfo:
        await resolve_design_user_query(
            "garbled query",
            design_config=design_config,
            sensitive_config=sensitive_config,
        )

    assert "no valid candidate" in str(excinfo.value)


async def test_resolver_rejects_blank_query(
    configs: tuple[DigitalDesignConfig, SensitiveConfig],
) -> None:
    """Blank input short-circuits and re-raises as the domain type."""
    design_config, sensitive_config = configs
    with pytest.raises(DigitalDesignResolveError) as excinfo:
        await resolve_design_user_query(
            "",
            design_config=design_config,
            sensitive_config=sensitive_config,
        )
    assert "blank" in str(excinfo.value)
