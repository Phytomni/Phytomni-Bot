# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Resolver JSON schemas are derived from their Candidate models.

Pins that brief_gene and network no longer hand-maintain a second copy
of the candidate confidence constraint: the _RESOLVER_JSON_SCHEMA inner
schema is built from the pydantic model, so the two cannot drift.
"""

# pylint: disable=protected-access

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.brief_gene import resolve_query as bg
from mcp_server_phytomni.agents.network import resolve_query as nw

pytestmark = [pytest.mark.unit, pytest.mark.agent]


def _candidate_confidence(schema: dict) -> dict:
    items = schema["json_schema"]["schema"]["properties"]["candidates"][
        "items"
    ]
    return items["properties"]["confidence"]


def test_brief_gene_schema_confidence_matches_model() -> None:
    """brief_gene candidate confidence bounds come from the model."""
    conf = _candidate_confidence(bg._RESOLVER_JSON_SCHEMA)
    assert conf["minimum"] == 0.0
    assert conf["maximum"] == 1.0


def test_network_schema_confidence_matches_model() -> None:
    """network candidate confidence bounds come from the model."""
    conf = _candidate_confidence(nw._RESOLVER_JSON_SCHEMA)
    assert conf["minimum"] == 0.0
    assert conf["maximum"] == 1.0


def test_brief_gene_schema_outer_name_preserved() -> None:
    """The outer json_schema wrapper name is unchanged (LLM contract)."""
    assert (
        bg._RESOLVER_JSON_SCHEMA["json_schema"]["name"]
        == "BriefGeneResolution"
    )


def test_network_schema_outer_name_preserved() -> None:
    """The outer json_schema wrapper name is unchanged (LLM contract)."""
    assert (
        nw._RESOLVER_JSON_SCHEMA["json_schema"]["name"]
        == "GeneNetworkResolution"
    )


def test_brief_gene_candidate_schema_is_model_derived() -> None:
    """The candidate item schema equals the model's json schema subset."""
    model_schema = bg.BriefGeneIdCandidate.model_json_schema()
    items = bg._RESOLVER_JSON_SCHEMA["json_schema"]["schema"]["properties"][
        "candidates"
    ]["items"]
    assert (
        items["properties"]["confidence"]["maximum"]
        == model_schema["properties"]["confidence"]["maximum"]
    )
    assert (
        items["properties"]["confidence"]["minimum"]
        == model_schema["properties"]["confidence"]["minimum"]
    )


def test_network_candidate_schema_is_model_derived() -> None:
    """The candidate item schema equals the model's json schema subset."""
    model_schema = nw.GeneNetworkToIdCandidate.model_json_schema()
    items = nw._RESOLVER_JSON_SCHEMA["json_schema"]["schema"]["properties"][
        "candidates"
    ]["items"]
    assert (
        items["properties"]["confidence"]["maximum"]
        == model_schema["properties"]["confidence"]["maximum"]
    )
    assert (
        items["properties"]["confidence"]["minimum"]
        == model_schema["properties"]["confidence"]["minimum"]
    )
