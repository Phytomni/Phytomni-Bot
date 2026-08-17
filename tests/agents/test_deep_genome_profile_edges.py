# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Edge coverage for DeepGenome profile symbol and annotation lookups."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.agents.deep_genome import profile as profile_mod
from mcp_server_phytomni.agents.deep_genome.profile import (
    DeepGenomeProfileMixin,
)

pytestmark = pytest.mark.agent


class _ProfileHost(DeepGenomeProfileMixin):
    """Minimal host that exposes the profile lookup public wrappers."""

    def __init__(self) -> None:
        self.deep_genome_config = SimpleNamespace(TIMEOUT=1.5)


async def test_cached_gene_symbol_lookup_splits_comma_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Comma-separated symbols become a de-duplicated list."""

    async def fake_post(
        _sql: str, request_timeout: float = 0.0
    ) -> dict[str, Any]:
        del request_timeout
        return {"data": [{"symbol": "NAC001,NAC002,NAC001"}]}

    monkeypatch.setattr(profile_mod, "_post_bi_sql", fake_post)
    symbol_lookup = getattr(profile_mod, "_cached_gene_symbol_lookup")
    symbols = await symbol_lookup("ath", "AT1G01010")

    assert set(symbols) == {"NAC001", "NAC002"}


async def test_cached_gene_symbol_lookup_returns_empty_for_null_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A null symbol cell yields an empty list rather than a None entry."""

    async def fake_post(
        _sql: str, request_timeout: float = 0.0
    ) -> dict[str, Any]:
        del request_timeout
        return {"data": [{"symbol": None}]}

    monkeypatch.setattr(profile_mod, "_post_bi_sql", fake_post)

    symbol_lookup = getattr(profile_mod, "_cached_gene_symbol_lookup")
    assert await symbol_lookup("ath", "AT1G01010") == []


async def test_profile_public_lookups_honor_optional_semaphore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public wrappers reach the cached lookups with and without a lock."""
    symbol_calls: list[float] = []
    annotation_calls: list[float] = []

    async def fake_symbols(
        species_code: str,
        gene_id: str,
        request_timeout: float = 0.0,
    ) -> list[str]:
        del species_code, gene_id
        symbol_calls.append(request_timeout)
        return ["SYM"]

    async def fake_annotations(
        species_code: str,
        gene_id: str,
        request_timeout: float = 0.0,
    ) -> dict[str, Any]:
        del species_code, gene_id
        annotation_calls.append(request_timeout)
        return {"description": [{"note": "ok"}]}

    monkeypatch.setattr(
        profile_mod, "_cached_gene_symbol_lookup", fake_symbols
    )
    monkeypatch.setattr(
        profile_mod, "_cached_gene_annotation_lookup", fake_annotations
    )
    host = _ProfileHost()
    semaphore = asyncio.Semaphore(1)

    assert await host.gene_symbol("ath", "AT1G01010") == ["SYM"]
    assert await host.gene_symbol("ath", "AT1G01010", semaphore=semaphore) == [
        "SYM"
    ]
    assert await host.gene_annotation("ath", "AT1G01010") == {
        "description": [{"note": "ok"}]
    }
    assert await host.gene_annotation(
        "ath", "AT1G01010", semaphore=semaphore
    ) == {"description": [{"note": "ok"}]}
    assert symbol_calls == [1.5, 1.5]
    assert annotation_calls == [1.5, 1.5]


async def test_cached_gene_annotation_lookup_keeps_populated_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only annotation sections that return rows are copied into the dict."""
    payloads = [
        {"data": [{"description": "leaf"}]},
        {"data": []},
        {"data": [{"interpro_id": "IPR1"}]},
        {"data": []},
    ]

    async def fake_post(
        _sql: str, request_timeout: float = 0.0
    ) -> dict[str, Any]:
        del request_timeout
        return payloads.pop(0)

    monkeypatch.setattr(profile_mod, "_post_bi_sql", fake_post)
    annotation_lookup = getattr(profile_mod, "_cached_gene_annotation_lookup")
    result = await annotation_lookup("ath", "AT1G01010")

    assert result == {
        "description": [{"description": "leaf"}],
        "interpro": [{"interpro_id": "IPR1"}],
    }
