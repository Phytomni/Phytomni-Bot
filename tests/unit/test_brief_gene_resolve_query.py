# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline unit tests for the BriefGene gene-id resolver."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

import pytest

from mcp_server_phytomni.agents.brief_gene import resolve_query
from mcp_server_phytomni.agents.brief_gene.resolve_query import (
    BriefGeneIdCandidate,
    BriefGeneResolveError,
    resolve_brief_gene_user_query,
)
from mcp_server_phytomni.agents.chat import service as chat_service
from mcp_server_phytomni.config.defaults import BriefGeneConfig
from mcp_server_phytomni.config.settings import SensitiveConfig


@pytest.fixture(autouse=True)
def _clear_phyto_chat_cache() -> None:
    """Drop the persistent SQLite cache between cases."""
    chat_service.run_phyto_chat_cached.cache_clear()


def _make_response(payload: Any) -> Dict[str, Any]:
    """Wrap an LLM payload into the OpenAI chat-completion shape."""
    return {
        "choices": [
            {"message": {"content": json.dumps(payload)}},
        ],
    }


@pytest.fixture(name="configs")
def _configs() -> tuple[BriefGeneConfig, SensitiveConfig]:
    """Return shared config defaults usable across all resolver cases."""
    return BriefGeneConfig(), SensitiveConfig.load()


async def test_resolver_returns_typed_result_for_single_id(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[BriefGeneConfig, SensitiveConfig],
) -> None:
    """Top-level gene_id with no candidates becomes a single-element list."""
    brief_config, sensitive_config = configs

    async def fake_phyto_chat(**_: Any) -> Dict[str, Any]:
        return _make_response({"gene_id": "AT5G42800"})

    monkeypatch.setattr(resolve_query, "phyto_chat", fake_phyto_chat)

    result = await resolve_brief_gene_user_query(
        "What does AT5G42800 do in Arabidopsis?",
        brief_config=brief_config,
        sensitive_config=sensitive_config,
    )

    assert result.gene_id == "AT5G42800"
    assert result.raw_query == "What does AT5G42800 do in Arabidopsis?"
    assert result.candidates == [
        BriefGeneIdCandidate(gene_id="AT5G42800", confidence=1.0)
    ]


async def test_resolver_selects_top_confidence_candidate(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[BriefGeneConfig, SensitiveConfig],
) -> None:
    """Multi-candidate output picks the highest-confidence id."""
    brief_config, sensitive_config = configs

    async def fake_phyto_chat(**_: Any) -> Dict[str, Any]:
        return _make_response(
            {
                "gene_id": "X",
                "candidates": [
                    {"gene_id": "X", "confidence": 0.3},
                    {"gene_id": "Os01g0177400", "confidence": 0.9},
                ],
            }
        )

    monkeypatch.setattr(resolve_query, "phyto_chat", fake_phyto_chat)

    result = await resolve_brief_gene_user_query(
        "rice TPR6 function",
        brief_config=brief_config,
        sensitive_config=sensitive_config,
    )

    assert result.gene_id == "Os01g0177400"
    assert [c.gene_id for c in result.candidates] == [
        "Os01g0177400",
        "X",
    ]


async def test_resolver_accepts_top_level_gene_id_without_candidates(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[BriefGeneConfig, SensitiveConfig],
) -> None:
    """When candidates is missing, synthesize one from gene_id field."""
    brief_config, sensitive_config = configs

    async def fake_phyto_chat(**_: Any) -> Dict[str, Any]:
        return _make_response({"gene_id": "  Zm00001eb000010  "})

    monkeypatch.setattr(resolve_query, "phyto_chat", fake_phyto_chat)

    result = await resolve_brief_gene_user_query(
        "tell me about Zm00001eb000010 in maize",
        brief_config=brief_config,
        sensitive_config=sensitive_config,
    )

    assert result.gene_id == "Zm00001eb000010"
    assert len(result.candidates) == 1
    assert result.candidates[0].confidence == 1.0


@pytest.mark.parametrize("blank", ["", "   ", "\n\t  "])
async def test_resolver_rejects_blank_query(
    blank: str,
    configs: tuple[BriefGeneConfig, SensitiveConfig],
) -> None:
    """Blank or whitespace-only queries short-circuit with 400 semantics."""
    brief_config, sensitive_config = configs
    with pytest.raises(BriefGeneResolveError, match="blank"):
        await resolve_brief_gene_user_query(
            blank,
            brief_config=brief_config,
            sensitive_config=sensitive_config,
        )


async def test_resolver_rejects_empty_candidates(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[BriefGeneConfig, SensitiveConfig],
) -> None:
    """Empty gene_id and empty candidates raise no-valid-candidate."""
    brief_config, sensitive_config = configs

    async def fake_phyto_chat(**_: Any) -> Dict[str, Any]:
        return _make_response({"gene_id": "", "candidates": []})

    monkeypatch.setattr(resolve_query, "phyto_chat", fake_phyto_chat)

    with pytest.raises(BriefGeneResolveError, match="no valid candidate"):
        await resolve_brief_gene_user_query(
            "ambiguous query",
            brief_config=brief_config,
            sensitive_config=sensitive_config,
        )


async def test_resolver_rejects_non_json_response(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[BriefGeneConfig, SensitiveConfig],
) -> None:
    """Non-JSON LLM payload surfaces non-parseable error."""
    brief_config, sensitive_config = configs

    async def fake_phyto_chat(**_: Any) -> Dict[str, Any]:
        return {
            "choices": [
                {"message": {"content": "sorry I cannot determine"}},
            ],
        }

    monkeypatch.setattr(resolve_query, "phyto_chat", fake_phyto_chat)

    with pytest.raises(BriefGeneResolveError, match="non-parseable"):
        await resolve_brief_gene_user_query(
            "what gene is involved in rice TPR6 flowering",
            brief_config=brief_config,
            sensitive_config=sensitive_config,
        )


async def test_resolver_falls_back_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[BriefGeneConfig, SensitiveConfig],
) -> None:
    """Inflight LLM timeout maps to BriefGeneResolveError, not propagated."""
    brief_config, sensitive_config = configs

    async def slow_phyto_chat(**_: Any) -> Dict[str, Any]:
        raise asyncio.TimeoutError("simulated")

    monkeypatch.setattr(resolve_query, "phyto_chat", slow_phyto_chat)

    with pytest.raises(BriefGeneResolveError, match="timeout"):
        await resolve_brief_gene_user_query(
            "any query",
            brief_config=brief_config,
            sensitive_config=sensitive_config,
            timeout_seconds=0.1,
        )


async def test_resolver_handles_none_phyto_chat_response(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[BriefGeneConfig, SensitiveConfig],
) -> None:
    """phyto_chat returning None (retry exhaustion) maps to resolve error."""
    brief_config, sensitive_config = configs

    async def fake_phyto_chat(**_: Any) -> None:
        return None

    monkeypatch.setattr(resolve_query, "phyto_chat", fake_phyto_chat)

    with pytest.raises(
        BriefGeneResolveError, match="no content after retries"
    ):
        await resolve_brief_gene_user_query(
            "any query",
            brief_config=brief_config,
            sensitive_config=sensitive_config,
        )


async def test_resolver_clamps_out_of_range_confidence(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[BriefGeneConfig, SensitiveConfig],
) -> None:
    """LLM-returned confidence outside [0, 1] is clamped, not rejected."""
    brief_config, sensitive_config = configs

    async def fake_phyto_chat(**_: Any) -> Dict[str, Any]:
        return _make_response(
            {
                "gene_id": "AT1G01010",
                "candidates": [
                    {"gene_id": "AT1G01010", "confidence": 1.5},
                    {"gene_id": "X", "confidence": -0.2},
                ],
            }
        )

    monkeypatch.setattr(resolve_query, "phyto_chat", fake_phyto_chat)

    result = await resolve_brief_gene_user_query(
        "AT1G01010",
        brief_config=brief_config,
        sensitive_config=sensitive_config,
    )

    confidences: List[float] = [c.confidence for c in result.candidates]
    assert max(confidences) == pytest.approx(1.0)
    assert min(confidences) == pytest.approx(0.0)
    assert result.gene_id == "AT1G01010"
