# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline unit tests for the BriefGene gene-id resolver."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

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

_RESOLVER_LOGGER_NAME = "mcp_server_phytomni.agents.brief_gene.resolve_query"


@pytest.fixture(autouse=True)
def _clear_phyto_chat_cache() -> None:
    """Drop the persistent SQLite cache between cases."""
    chat_service.run_phyto_chat_cached.cache_clear()


def _make_response(payload: Any) -> dict[str, Any]:
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

    async def fake_phyto_chat(**_: Any) -> dict[str, Any]:
        return _make_response({"gene_id": "AT5G42800", "species_code": "ath"})

    monkeypatch.setattr(resolve_query, "phyto_chat", fake_phyto_chat)

    result = await resolve_brief_gene_user_query(
        "What does AT5G42800 do in Arabidopsis?",
        brief_config=brief_config,
        sensitive_config=sensitive_config,
    )

    assert result.gene_id == "AT5G42800"
    assert result.species_code == "ath"
    assert result.raw_query == "What does AT5G42800 do in Arabidopsis?"
    assert result.candidates == [
        BriefGeneIdCandidate(
            gene_id="AT5G42800",
            confidence=1.0,
            species_code="ath",
        )
    ]


async def test_resolver_warns_but_accepts_unsupported_species(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[BriefGeneConfig, SensitiveConfig],
    attach_resolver_caplog: Callable[[str], pytest.LogCaptureFixture],
    assert_unsupported_species_warning: Callable[..., None],
) -> None:
    """A non-blank species_code outside the data map warns, not rejects.

    Mirrors the network resolver's warn-but-accept handling of
    upstream-deprecated TO ids: the resolution still returns so a
    steered-but-imperfect LLM species choice does not hard-fail, but a
    WARNING surfaces the likely downstream data miss for operators.
    """
    brief_config, sensitive_config = configs
    caplog = attach_resolver_caplog(_RESOLVER_LOGGER_NAME)

    async def fake_phyto_chat(**_: Any) -> dict[str, Any]:
        return _make_response({"gene_id": "GENE1", "species_code": "zzz"})

    monkeypatch.setattr(resolve_query, "phyto_chat", fake_phyto_chat)

    with caplog.at_level(logging.WARNING):
        result = await resolve_brief_gene_user_query(
            "some obscure organism gene",
            brief_config=brief_config,
            sensitive_config=sensitive_config,
        )

    assert result.species_code == "zzz"
    assert result.gene_id == "GENE1"
    assert_unsupported_species_warning(caplog.records, "zzz")


async def test_resolver_selects_top_confidence_candidate(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[BriefGeneConfig, SensitiveConfig],
) -> None:
    """Multi-candidate output picks the highest-confidence id."""
    brief_config, sensitive_config = configs

    async def fake_phyto_chat(**_: Any) -> dict[str, Any]:
        return _make_response(
            {
                "gene_id": "X",
                "species_code": "osa",
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
    assert result.species_code == "osa"
    assert [c.gene_id for c in result.candidates] == [
        "Os01g0177400",
        "X",
    ]
    assert all(c.species_code == "osa" for c in result.candidates)


async def test_resolver_accepts_top_level_gene_id_without_candidates(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[BriefGeneConfig, SensitiveConfig],
) -> None:
    """When candidates is missing, synthesize one from gene_id field."""
    brief_config, sensitive_config = configs

    async def fake_phyto_chat(**_: Any) -> dict[str, Any]:
        return _make_response(
            {"gene_id": "  Zm00001eb000010  ", "species_code": "zma"}
        )

    monkeypatch.setattr(resolve_query, "phyto_chat", fake_phyto_chat)

    result = await resolve_brief_gene_user_query(
        "tell me about Zm00001eb000010 in maize",
        brief_config=brief_config,
        sensitive_config=sensitive_config,
    )

    assert result.gene_id == "Zm00001eb000010"
    assert result.species_code == "zma"
    assert len(result.candidates) == 1
    assert result.candidates[0].confidence == 1.0
    assert result.candidates[0].species_code == "zma"


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

    async def fake_phyto_chat(**_: Any) -> dict[str, Any]:
        return _make_response(
            {"gene_id": "", "species_code": "ath", "candidates": []}
        )

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

    async def fake_phyto_chat(**_: Any) -> dict[str, Any]:
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

    async def slow_phyto_chat(**_: Any) -> dict[str, Any]:
        raise TimeoutError("simulated")

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

    async def fake_phyto_chat(**_: Any) -> dict[str, Any]:
        return _make_response(
            {
                "gene_id": "AT1G01010",
                "species_code": "ath",
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

    confidences: list[float] = [c.confidence for c in result.candidates]
    assert max(confidences) == pytest.approx(1.0)
    assert min(confidences) == pytest.approx(0.0)
    assert result.gene_id == "AT1G01010"
    assert result.species_code == "ath"


async def test_resolver_rejects_missing_species_code(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[BriefGeneConfig, SensitiveConfig],
) -> None:
    """LLM payload without species_code field surfaces a 400 semantic error."""
    brief_config, sensitive_config = configs

    async def fake_phyto_chat(**_: Any) -> dict[str, Any]:
        return _make_response({"gene_id": "Os01g0177400"})

    monkeypatch.setattr(resolve_query, "phyto_chat", fake_phyto_chat)

    with pytest.raises(
        BriefGeneResolveError, match="species_code could not be determined"
    ):
        await resolve_brief_gene_user_query(
            "rice flowering gene",
            brief_config=brief_config,
            sensitive_config=sensitive_config,
        )


async def test_resolver_rejects_blank_species_code(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[BriefGeneConfig, SensitiveConfig],
) -> None:
    """Whitespace-only species_code is treated as undetermined and rejected."""
    brief_config, sensitive_config = configs

    async def fake_phyto_chat(**_: Any) -> dict[str, Any]:
        return _make_response(
            {"gene_id": "Os01g0177400", "species_code": "   "}
        )

    monkeypatch.setattr(resolve_query, "phyto_chat", fake_phyto_chat)

    with pytest.raises(
        BriefGeneResolveError, match="species_code could not be determined"
    ):
        await resolve_brief_gene_user_query(
            "rice flowering gene",
            brief_config=brief_config,
            sensitive_config=sensitive_config,
        )


async def test_resolver_preserves_per_candidate_species_code(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[BriefGeneConfig, SensitiveConfig],
) -> None:
    """Explicit per-candidate species_code wins over the top-level fallback."""
    brief_config, sensitive_config = configs

    async def fake_phyto_chat(**_: Any) -> dict[str, Any]:
        return _make_response(
            {
                "gene_id": "Os01g0177400",
                "species_code": "osa",
                "candidates": [
                    {
                        "gene_id": "Os01g0177400",
                        "confidence": 0.9,
                        "species_code": "osa",
                    },
                    {
                        "gene_id": "AT1G01010",
                        "confidence": 0.4,
                        "species_code": "ath",
                    },
                    {"gene_id": "X", "confidence": 0.1},
                ],
            }
        )

    monkeypatch.setattr(resolve_query, "phyto_chat", fake_phyto_chat)

    result = await resolve_brief_gene_user_query(
        "rice vs arabidopsis homolog",
        brief_config=brief_config,
        sensitive_config=sensitive_config,
    )

    assert result.species_code == "osa"
    species_by_gene = {c.gene_id: c.species_code for c in result.candidates}
    assert species_by_gene == {
        "Os01g0177400": "osa",
        "AT1G01010": "ath",
        "X": "osa",
    }
