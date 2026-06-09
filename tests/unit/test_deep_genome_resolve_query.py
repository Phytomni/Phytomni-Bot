# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline unit tests for the DeepGenome gene-id resolver wrapper."""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from mcp_server_phytomni.agents.brief_gene import resolve_query as bga_module
from mcp_server_phytomni.agents.chat import service as chat_service
from mcp_server_phytomni.agents.deep_genome.resolve_query import (
    DeepGenomeIdCandidate,
    DeepGenomeResolveError,
    resolve_deep_genome_user_query,
)
from mcp_server_phytomni.config.defaults import DeepGenomeConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_phyto_chat_cache() -> None:
    """Drop the persistent SQLite cache between cases."""
    chat_service.run_phyto_chat_cached.cache_clear()


def _make_response(payload: Any) -> Dict[str, Any]:
    """Wrap an LLM payload into the OpenAI chat-completion shape."""
    return {"choices": [{"message": {"content": json.dumps(payload)}}]}


@pytest.fixture(name="configs")
def _configs() -> tuple[DeepGenomeConfig, SensitiveConfig]:
    """Return shared config defaults usable across all resolver cases."""
    return DeepGenomeConfig(), SensitiveConfig.load()


async def test_resolver_returns_deep_genome_typed_result(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[DeepGenomeConfig, SensitiveConfig],
) -> None:
    """Happy path delegates to BGA and returns DeepGenome-typed result.

    Asserts the wrapper exposes the result through the per-domain
    pydantic model rather than leaking BGA's class so callers can
    discriminate on type alone.
    """
    captured: dict[str, Any] = {}

    async def fake_phyto_chat(**kwargs: Any) -> Dict[str, Any]:
        captured["kwargs"] = kwargs
        return _make_response(
            {
                "gene_id": "Os01g0177400",
                "species_code": "osa",
                "candidates": [
                    {
                        "gene_id": "Os01g0177400",
                        "confidence": 0.92,
                        "species_code": "osa",
                    }
                ],
            }
        )

    monkeypatch.setattr(bga_module, "phyto_chat", fake_phyto_chat)
    deep_genome_config, sensitive_config = configs

    result = await resolve_deep_genome_user_query(
        "tell me about CAB1 in rice",
        deep_genome_config=deep_genome_config,
        sensitive_config=sensitive_config,
    )

    assert result.gene_id == "Os01g0177400"
    assert result.raw_query == "tell me about CAB1 in rice"
    assert len(result.candidates) == 1
    assert isinstance(result.candidates[0], DeepGenomeIdCandidate)
    assert result.candidates[0].confidence == pytest.approx(0.92)


async def test_resolver_maps_bga_error_to_deep_genome_error(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[DeepGenomeConfig, SensitiveConfig],
) -> None:
    """BriefGeneResolveError raised inside BGA maps to the deep_genome type.

    Per-domain error class is the contract the HTTP API uses to
    disambiguate 400 responses, so the wrapper must not leak the
    BGA-typed exception to callers.
    """

    async def fake_phyto_chat(**_kwargs: Any) -> Dict[str, Any]:
        return _make_response(
            {"gene_id": "", "species_code": "osa", "candidates": []}
        )

    monkeypatch.setattr(bga_module, "phyto_chat", fake_phyto_chat)
    deep_genome_config, sensitive_config = configs

    with pytest.raises(DeepGenomeResolveError) as excinfo:
        await resolve_deep_genome_user_query(
            "not a gene",
            deep_genome_config=deep_genome_config,
            sensitive_config=sensitive_config,
        )

    assert "no valid candidate" in str(excinfo.value)


async def test_resolver_rejects_blank_query(
    configs: tuple[DeepGenomeConfig, SensitiveConfig],
) -> None:
    """Blank input short-circuits before any LLM call.

    BGA's blank-query guard fires before the chat dispatch, so the
    deep_genome wrapper sees the BGA error and re-raises as the
    domain-typed error without ever needing a phyto_chat mock.
    """
    deep_genome_config, sensitive_config = configs
    with pytest.raises(DeepGenomeResolveError) as excinfo:
        await resolve_deep_genome_user_query(
            "   ",
            deep_genome_config=deep_genome_config,
            sensitive_config=sensitive_config,
        )
    assert "blank" in str(excinfo.value)
