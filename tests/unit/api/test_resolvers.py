# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for Expert structured-id resolver flag policy."""

from __future__ import annotations

from typing import Any

import pytest

from mcp_server_phytomni.api.resolvers import (
    apply_expert_structured_resolver_flags,
)


@pytest.mark.parametrize(
    ("agent", "arguments", "flag", "needed"),
    [
        ("deep_genome", {}, "resolve_gene_id", ("gene_id", "species_code")),
        (
            "deep_genome",
            {"gene_id": "AT1G01010"},
            "resolve_gene_id",
            ("gene_id", "species_code"),
        ),
        (
            "design",
            {"species_code": "   "},
            "resolve_gene_id",
            ("gene_id", "species_code"),
        ),
        ("network", {}, "resolve_to_id", ("to_id", "species_code")),
        (
            "network",
            {"to_id": "TO:0000001"},
            "resolve_to_id",
            ("to_id", "species_code"),
        ),
    ],
)
def test_expert_flags_open_when_structured_ids_are_blank(
    agent: str,
    arguments: dict[str, Any],
    flag: str,
    needed: tuple[str, str],
) -> None:
    """Missing or blank structured fields opt into the native resolver."""
    prepared = apply_expert_structured_resolver_flags(
        agent=agent,
        arguments=dict(arguments),
        user_query="rice CAB1 function",
    )

    assert prepared["user_query"] == "rice CAB1 function"
    assert prepared[flag] is True
    assert any(
        name not in arguments or not str(arguments[name]).strip()
        for name in needed
    )


@pytest.mark.parametrize(
    ("agent", "arguments"),
    [
        (
            "deep_genome",
            {"gene_id": "AT1G01010", "species_code": "ath"},
        ),
        (
            "design",
            {"gene_id": "AT1G01010", "species_code": "ath"},
        ),
        (
            "network",
            {"to_id": "TO:0000001", "species_code": "osa"},
        ),
    ],
)
def test_expert_flags_stay_off_when_ids_are_complete(
    agent: str,
    arguments: dict[str, Any],
) -> None:
    """A complete extraction is forwarded without a second resolver hop."""
    prepared = apply_expert_structured_resolver_flags(
        agent=agent,
        arguments=dict(arguments),
        user_query="rice CAB1 function",
    )

    assert prepared == arguments
    assert "resolve_gene_id" not in prepared
    assert "resolve_to_id" not in prepared


@pytest.mark.parametrize(
    "agent",
    ["brief_gene", "analyst", "chat", "research"],
)
def test_expert_flags_ignore_non_structured_agents(agent: str) -> None:
    """BriefGene and other Expert slugs do not open resolver flags."""
    arguments = {"user_query": "rice CAB1 function"}

    prepared = apply_expert_structured_resolver_flags(
        agent=agent,
        arguments=dict(arguments),
        user_query="rice CAB1 function",
    )

    assert prepared == arguments
    assert "resolve_gene_id" not in prepared
    assert "resolve_to_id" not in prepared
