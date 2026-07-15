# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Lock the direct GaussDB query callsite corpus."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.agent]

ROOT = Path(__file__).resolve().parents[3]
CORPUS_PATH = ROOT / "tests/fixtures/gauss_query_corpus.json"

EXPECTED_LABELS = {
    "brief_gene.query_judge.exact_multispecies",
    "brief_gene.query_judge.symbol_multispecies",
    "brief_gene.query_judge.species",
    "brief_gene.annotation.id",
    "brief_gene.annotation.structure",
    "brief_gene.annotation.ontology",
    "brief_gene.annotation.mapman",
    "brief_gene.annotation.interpro",
    "brief_gene.annotation.description",
    "brief_gene.homology.homologs",
    "brief_gene.homology.interactions",
    "deep_genome.profile.id",
    "deep_genome.profile.description",
    "deep_genome.profile.ontology",
    "deep_genome.profile.interpro",
    "deep_genome.profile.mapman",
    "deep_genome.dispatch.rice_remap",
    "deep_genome.dispatch.maize_remap",
}


def load_corpus() -> list[dict[str, Any]]:
    """Load the checked-in callsite corpus as parsed JSON."""
    payload = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    return [case for case in payload if isinstance(case, dict)]


def test_gauss_corpus_locks_all_direct_query_callsites() -> None:
    """Keep all eighteen query labels and ten table names stable."""
    cases = load_corpus()
    assert len(cases) == 18
    assert {case["label"] for case in cases} == EXPECTED_LABELS
    assert len({table for case in cases for table in case["tables"]}) == 10
    for case in cases:
        assert set(case) == {
            "label",
            "source",
            "sql",
            "tables",
            "expected_columns",
        }
        assert isinstance(case["sql"], str) and case["sql"].startswith(
            "SELECT"
        )
        assert isinstance(case["tables"], list) and case["tables"]
        assert isinstance(case["expected_columns"], list)


def test_gauss_corpus_contains_no_rows_or_credentials() -> None:
    """The compatibility fixture contains query shape, never customer data."""
    serialized = CORPUS_PATH.read_text(encoding="utf-8").lower()
    for forbidden in (
        "password",
        "bearer",
        "access_token",
        "customer_row",
        "secret",
    ):
        assert forbidden not in serialized
