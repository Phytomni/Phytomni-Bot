# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline unit tests for the GeneNetwork TO-id resolver."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict

import pytest

from mcp_server_phytomni.agents.chat import service as chat_service
from mcp_server_phytomni.agents.network import resolve_query as nw_module
from mcp_server_phytomni.agents.network.resolve_query import (
    GeneNetworkResolveError,
    GeneNetworkToIdCandidate,
    resolve_network_user_query,
)
from mcp_server_phytomni.config.defaults import GeneNetworkConfig
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
def _configs() -> tuple[GeneNetworkConfig, SensitiveConfig]:
    """Return shared config defaults usable across all resolver cases."""
    return GeneNetworkConfig(), SensitiveConfig.load()


async def test_resolver_returns_typed_result_for_valid_to_id(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[GeneNetworkConfig, SensitiveConfig],
) -> None:
    """Happy path returns the typed result with the catalog-validated id."""
    captured: dict[str, Any] = {}

    async def fake_phyto_chat(**kwargs: Any) -> Dict[str, Any]:
        captured["kwargs"] = kwargs
        return _make_response({"to_id": "TO:0000207"})

    monkeypatch.setattr(nw_module, "phyto_chat", fake_phyto_chat)
    network_config, sensitive_config = configs

    result = await resolve_network_user_query(
        "rice plant height trait",
        network_config=network_config,
        sensitive_config=sensitive_config,
    )

    assert result.to_id == "TO:0000207"
    assert result.raw_query == "rice plant height trait"
    assert len(result.candidates) == 1
    assert isinstance(result.candidates[0], GeneNetworkToIdCandidate)
    assert result.candidates[0].confidence == pytest.approx(1.0)
    # The resolver embeds the TO catalog into the user prompt; assert
    # the LLM call carries a substantial user_query that includes a
    # representative catalog line.
    assert "TO:0000207 | plant height" in captured["kwargs"]["user_query"]


async def test_resolver_picks_top_confidence_among_candidates(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[GeneNetworkConfig, SensitiveConfig],
) -> None:
    """Sort by confidence descending; chosen id wins the tie-break."""

    async def fake_phyto_chat(**_kwargs: Any) -> Dict[str, Any]:
        return _make_response(
            {
                "to_id": "TO:0000207",
                "candidates": [
                    {"to_id": "TO:0000207", "confidence": 0.55},
                    {"to_id": "TO:0000276", "confidence": 0.91},
                ],
            }
        )

    monkeypatch.setattr(nw_module, "phyto_chat", fake_phyto_chat)
    network_config, sensitive_config = configs

    result = await resolve_network_user_query(
        "drought tolerance",
        network_config=network_config,
        sensitive_config=sensitive_config,
    )

    # Top-confidence id (drought tolerance, TO:0000276) wins.
    assert result.to_id == "TO:0000276"
    assert result.candidates[0].confidence == pytest.approx(0.91)


async def test_resolver_rejects_blank_query(
    configs: tuple[GeneNetworkConfig, SensitiveConfig],
) -> None:
    """Blank input short-circuits with a definitive error."""
    network_config, sensitive_config = configs
    with pytest.raises(GeneNetworkResolveError) as excinfo:
        await resolve_network_user_query(
            "  ",
            network_config=network_config,
            sensitive_config=sensitive_config,
        )
    assert "blank" in str(excinfo.value)


async def test_resolver_rejects_id_not_in_catalog(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[GeneNetworkConfig, SensitiveConfig],
) -> None:
    """Last-line-of-defense: id not in the committed TO catalog is dropped.

    The system prompt instructs the LLM to pick from the supplied
    catalog, but a non-compliant completion still loses its
    fabricated ids inside ``_normalize_candidates`` before they
    reach the downstream agent.
    """

    async def fake_phyto_chat(**_kwargs: Any) -> Dict[str, Any]:
        return _make_response({"to_id": "TO:9999999"})

    monkeypatch.setattr(nw_module, "phyto_chat", fake_phyto_chat)
    network_config, sensitive_config = configs

    with pytest.raises(GeneNetworkResolveError) as excinfo:
        await resolve_network_user_query(
            "made up trait",
            network_config=network_config,
            sensitive_config=sensitive_config,
        )
    assert "catalog" in str(excinfo.value)


async def test_resolver_rejects_non_json_llm_output(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[GeneNetworkConfig, SensitiveConfig],
) -> None:
    """Non-parseable LLM output maps to a definitive error."""

    async def fake_phyto_chat(**_kwargs: Any) -> Dict[str, Any]:
        return {"choices": [{"message": {"content": "not json at all"}}]}

    monkeypatch.setattr(nw_module, "phyto_chat", fake_phyto_chat)
    network_config, sensitive_config = configs

    with pytest.raises(GeneNetworkResolveError) as excinfo:
        await resolve_network_user_query(
            "anything",
            network_config=network_config,
            sensitive_config=sensitive_config,
        )
    assert "non-parseable" in str(excinfo.value)


async def test_resolver_maps_timeout_to_resolve_error(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[GeneNetworkConfig, SensitiveConfig],
) -> None:
    """asyncio.TimeoutError converts to GeneNetworkResolveError."""

    async def fake_phyto_chat(**_kwargs: Any) -> Dict[str, Any]:
        await asyncio.sleep(5)
        return _make_response({"to_id": "TO:0000207"})

    monkeypatch.setattr(nw_module, "phyto_chat", fake_phyto_chat)
    network_config, sensitive_config = configs

    with pytest.raises(GeneNetworkResolveError) as excinfo:
        await resolve_network_user_query(
            "rice plant height",
            network_config=network_config,
            sensitive_config=sensitive_config,
            timeout_seconds=0.05,
        )
    assert "timeout" in str(excinfo.value)


async def test_resolver_rejects_empty_llm_content(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[GeneNetworkConfig, SensitiveConfig],
) -> None:
    """Empty content / null content from the LLM is a definitive error."""

    async def fake_phyto_chat(**_kwargs: Any) -> Dict[str, Any]:
        return {"choices": [{"message": {"content": ""}}]}

    monkeypatch.setattr(nw_module, "phyto_chat", fake_phyto_chat)
    network_config, sensitive_config = configs

    with pytest.raises(GeneNetworkResolveError) as excinfo:
        await resolve_network_user_query(
            "anything",
            network_config=network_config,
            sensitive_config=sensitive_config,
        )
    assert "empty content" in str(excinfo.value)


async def test_resolver_warns_when_picking_upstream_deprecated_id(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[GeneNetworkConfig, SensitiveConfig],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Picking an upstream-deprecated id emits a WARNING log line.

    Pins the Option B observability contract: the catalog still
    accepts the customer's still-in-use ids so the workflow keeps
    running, but server logs surface the drift so operators can
    audit + migrate when ready.
    """

    async def fake_phyto_chat(**_kwargs: Any) -> Dict[str, Any]:
        return _make_response({"to_id": "TO:0000139"})

    monkeypatch.setattr(nw_module, "phyto_chat", fake_phyto_chat)
    network_config, sensitive_config = configs

    with caplog.at_level(
        logging.WARNING,
        logger="mcp_server_phytomni.agents.network.resolve_query",
    ):
        result = await resolve_network_user_query(
            "grains per panicle",
            network_config=network_config,
            sensitive_config=sensitive_config,
        )

    assert result.to_id == "TO:0000139"
    deprecated_warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
        and "deprecated" in record.getMessage()
    ]
    assert deprecated_warnings, (
        f"expected a deprecation warning, got: "
        f"{[r.getMessage() for r in caplog.records]}"
    )
    msg = deprecated_warnings[0].getMessage()
    assert "TO:0000139" in msg
    assert "grains per panicle" in msg


async def test_resolver_warns_on_post_sort_deprecated_winner(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[GeneNetworkConfig, SensitiveConfig],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Multi-candidate path: deprecated id wins by confidence + warning fires.

    Pins that the warning operates on the post-``sort`` winner, not
    on the LLM's top-level ``to_id`` field, by handing the resolver a
    payload where a canonical id appears at the top level but a
    deprecated id wins via higher candidate-list confidence. A
    regression that moved ``_warn_if_deprecated`` above
    ``candidates.sort`` (or that keyed off ``top_to_id`` rather than
    the chosen candidate) would surface here.
    """

    async def fake_phyto_chat(**_kwargs: Any) -> Dict[str, Any]:
        return _make_response(
            {
                "to_id": "TO:0000207",
                "candidates": [
                    {"to_id": "TO:0000207", "confidence": 0.4},
                    {"to_id": "TO:0000139", "confidence": 0.95},
                ],
            }
        )

    monkeypatch.setattr(nw_module, "phyto_chat", fake_phyto_chat)
    network_config, sensitive_config = configs

    with caplog.at_level(
        logging.WARNING,
        logger="mcp_server_phytomni.agents.network.resolve_query",
    ):
        result = await resolve_network_user_query(
            "grains per panicle in rice",
            network_config=network_config,
            sensitive_config=sensitive_config,
        )

    assert result.to_id == "TO:0000139"
    deprecated_warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
        and "deprecated" in record.getMessage()
    ]
    assert deprecated_warnings, (
        f"expected a deprecation warning on post-sort winner, "
        f"got: {[r.getMessage() for r in caplog.records]}"
    )
    assert "TO:0000139" in deprecated_warnings[0].getMessage()


async def test_resolver_does_not_warn_for_canonical_id(
    monkeypatch: pytest.MonkeyPatch,
    configs: tuple[GeneNetworkConfig, SensitiveConfig],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Canonical (upstream-vouched-for) ids must not trigger the warning.

    Locks down the warning's specificity: a false positive on the 541
    canonical ids would drown operators in noise + erode the signal's
    actionability.
    """

    async def fake_phyto_chat(**_kwargs: Any) -> Dict[str, Any]:
        return _make_response({"to_id": "TO:0000207"})

    monkeypatch.setattr(nw_module, "phyto_chat", fake_phyto_chat)
    network_config, sensitive_config = configs

    with caplog.at_level(
        logging.WARNING,
        logger="mcp_server_phytomni.agents.network.resolve_query",
    ):
        result = await resolve_network_user_query(
            "rice plant height",
            network_config=network_config,
            sensitive_config=sensitive_config,
        )

    assert result.to_id == "TO:0000207"
    deprecated_warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
        and "deprecated" in record.getMessage()
    ]
    assert not deprecated_warnings
