# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the brief_gene homology + interactions fetch node.

Pins that ``_run_fetch_homology_interactions_node`` issues two BI SQL
queries (``homology_gene`` + ``protein_interaction_col``) and that the
``_homology_gene_lists`` / ``_interaction_gene_list`` helpers project
responses into the ``gene_list`` shape ``orthologs_data`` /
``paralogs_data`` / ``interaction_data`` use, with the count
derivation pinned for the Basic Information bullets.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from mcp_server_phytomni.agents.brief_gene.homology import (
    _run_fetch_homology_interactions_node,
)
from mcp_server_phytomni.agents.brief_gene.interactions import (
    _empty_homology_interactions_result,
    _homology_gene_lists,
    _interaction_gene_list,
)

pytestmark = pytest.mark.agent


def _state(
    gene_id: str = "Os01g0177400",
    species_code: str = "osa",
) -> dict[str, Any]:
    return {"gene_id": gene_id, "species_code": species_code}


def _homology_response() -> dict[str, Any]:
    return {
        "data": [
            {
                "query_gene_id": "Os01g0177400",
                "query_species": "osa",
                "homology_gene_id": "AT1G01010",
                "homology_species": "ath",
            },
            {
                "query_gene_id": "Os01g0177400",
                "query_species": "osa",
                "homology_gene_id": "Zm00001eb000010",
                "homology_species": "zma",
            },
            {
                "query_gene_id": "Os01g0177400",
                "query_species": "osa",
                "homology_gene_id": "Os01g0177500",
                "homology_species": "osa",
            },
        ]
    }


def _interaction_response() -> dict[str, Any]:
    return {
        "data": [
            {
                "query_gene_id": "Os01g0177400",
                "query_protein": "QP1",
                "interact_gene_id": "Os01g0188000",
                "interact_protein": "IP1",
            },
        ]
    }


def test_empty_homology_interactions_result_is_fresh() -> None:
    """The shared empty result keeps independent nested list objects."""
    first = _empty_homology_interactions_result()
    second = _empty_homology_interactions_result()

    assert len(first) == 7
    assert first["orthologs_data"] == {"gene_list": []}
    assert first["paralogs_data"] == {"gene_list": []}
    assert first["interaction_data"] == {"gene_list": []}
    assert first["ortholog_count"] == 0
    assert first["ortholog_species_count"] == 0
    assert first["paralog_count"] == 0
    assert first["interaction_count"] == 0
    assert first == second
    assert first["orthologs_data"] is not second["orthologs_data"]
    first["orthologs_data"]["gene_list"].append({"gene_id": "mutated"})
    assert second["orthologs_data"] == {"gene_list": []}


def test_homology_gene_lists_separates_ortholog_and_paralog() -> None:
    """``_homology_gene_lists`` filters by species_code.

    Entries with ``homology_species == species_code`` belong to
    paralogs (same-species homologs); other entries belong to
    orthologs (cross-species homologs).
    """
    orthologs, paralogs = _homology_gene_lists(_homology_response(), "osa")

    assert len(orthologs) == 2
    assert len(paralogs) == 1
    assert all(item["homology_species"] != "osa" for item in orthologs)
    assert all(item["homology_species"] == "osa" for item in paralogs)


def test_interaction_gene_list_excludes_self_loop() -> None:
    """``_interaction_gene_list`` filters out the query gene self-loop.

    BI returns rows where either ``query_gene_id`` or
    ``interact_gene_id`` matches the input gene. The helper produces
    a list excluding the row where ``interact_gene_id`` equals the
    query ``gene_id`` (self-loop).
    """
    response = {
        "data": [
            {
                "query_gene_id": "Os01g0177400",
                "interact_gene_id": "Os01g0188000",
            },
            {
                "query_gene_id": "Os01g0177400",
                "interact_gene_id": "Os01g0177400",  # self-loop
            },
        ]
    }

    result = _interaction_gene_list(response, "Os01g0177400", "osa")

    assert len(result) == 1
    assert result[0]["interact_gene_id"] == "Os01g0188000"


@pytest.mark.asyncio
async def test_fetch_homology_interactions_projects_state_delta() -> None:
    """Node issues 2 BI queries and projects ``orthologs_data`` etc.

    Mock ``run_bi_api`` to return canned homology + interaction
    responses; assert the returned state delta contains the three
    canonical ``gene_list`` dict shapes plus four count fields the
    Basic Information render later consumes.
    """
    state = _state()

    with patch(
        "mcp_server_phytomni.agents.brief_gene.homology.run_bi_api",
        new=AsyncMock(
            side_effect=[
                _homology_response(),
                _interaction_response(),
            ]
        ),
    ):
        delta = await _run_fetch_homology_interactions_node(cast(Any, state))

    assert "gene_list" in delta["orthologs_data"]
    assert "gene_list" in delta["paralogs_data"]
    assert "gene_list" in delta["interaction_data"]
    assert delta["ortholog_count"] == 2
    assert delta["ortholog_species_count"] == 2  # ath + zma
    assert delta["paralog_count"] == 1
    assert delta["interaction_count"] == 1


@pytest.mark.asyncio
async def test_fetch_homology_uses_relay_aware_seam_in_direct_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In non-relay mode the node routes BI calls through the gated seam.

    ``run_bi_api`` (and the ``bi_query`` it wraps) branch on
    ``relay_mode_enabled()``: a direct GaussDB query when relay is off,
    the relay route when on. The relay-only ``relay_bi_query`` posts to a
    scheme-less ``/v1/relay/bi/query`` URL and raises
    ``httpx.UnsupportedProtocol`` in a direct deployment, so the node
    must never reach it when relay mode is off.
    """
    seen_sql: list[str] = []

    async def fake_bi_query(sql: str, *, retry: Any) -> dict[str, Any]:
        _ = retry
        seen_sql.append(sql)
        return {"data": []}

    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene.pipeline.bi_query",
        fake_bi_query,
    )

    async def _forbidden_relay(*args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        raise AssertionError(
            "homology node used relay_bi_query in non-relay mode"
        )

    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene.homology.relay_bi_query",
        _forbidden_relay,
        raising=False,
    )

    delta = await _run_fetch_homology_interactions_node(cast(Any, _state()))

    assert len(seen_sql) == 2
    assert delta["ortholog_count"] == 0
    assert delta["interaction_count"] == 0


@pytest.mark.asyncio
async def test_fetch_homology_short_circuits_on_empty_gene_id() -> None:
    """Empty gene_id (gene not found) returns empty deltas with no BI call.

    The node runs unconditionally off query_judge so the section
    fan-in never waits on a branch that may not fire; an unresolved
    gene has no homology to fetch, so it short-circuits to empty
    counts without issuing the two BI queries.
    """
    bi_mock = AsyncMock()
    with patch(
        "mcp_server_phytomni.agents.brief_gene.homology.run_bi_api",
        new=bi_mock,
    ):
        delta = await _run_fetch_homology_interactions_node(
            cast(Any, {"gene_id": "", "species_code": ""})
        )

    bi_mock.assert_not_awaited()
    assert delta["orthologs_data"] == {"gene_list": []}
    assert delta["paralog_count"] == 0
    assert delta["interaction_count"] == 0
