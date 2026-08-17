# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the DeepGenome→subgraph adapter mappers.

Pins the ``user_query`` construction inside
``map_deepgenome_to_knowledge_input`` against the inline shape in
``deep_genome/dispatch.py:_run_knowledge_agent`` so the knowledge
subgraph mounts through the helper without behavior drift.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.deep_genome.formatting import (
    SPECIES_CODE_MAP,
)
from mcp_server_phytomni.agents.knowledge.state import KnowledgeInput
from mcp_server_phytomni.graphs.deep_genome_adapters import (
    map_deepgenome_to_knowledge_input,
)

pytestmark = pytest.mark.agent


def test_knowledge_mapper_uses_dispatch_query_shape() -> None:
    """``user_query`` matches ``{gene_symbol}\\n{species_name}?`` exactly.

    Pins the inline construction in
    :meth:`_run_knowledge_agent` (``user_query =
    f"{gene_symbol}\\n{species_name}?"``). If a future refactor changes
    the join, this test fails first so the dispatch site and the
    mapper stay aligned.
    """
    sample_code = next(iter(SPECIES_CODE_MAP))
    expected_name = SPECIES_CODE_MAP[sample_code]
    result = map_deepgenome_to_knowledge_input(
        gene_symbol="LOC_Os01g01010",
        species_code=sample_code,
        repo_id_dict={"viz-repo": 128},
    )
    assert result["user_query"] == f"LOC_Os01g01010\n{expected_name}?"


def test_knowledge_mapper_falls_back_to_code_when_species_unknown() -> None:
    """Unknown species code passes through as the display name.

    Pins the ``SPECIES_CODE_MAP.get(species_code, species_code)``
    branch in the dispatch site so callers passing an out-of-table
    code still get a deterministic query rather than ``None`` or a
    KeyError.
    """
    result = map_deepgenome_to_knowledge_input(
        gene_symbol="GeneX",
        species_code="UNKNOWN_CODE",
        repo_id_dict={"viz-repo": 128},
    )
    assert result["user_query"] == "GeneX\nUNKNOWN_CODE?"


def test_knowledge_mapper_carries_repo_id_dict_copy() -> None:
    """``repo_id_dict`` is copied so the caller's mapping stays immutable.

    Pins that the mapper returns its own dict, not a reference to
    the caller's map. Without the copy, a later node mutating
    ``state["repo_id_dict"]`` would also rewrite the dispatch
    config's ``REPO_ID_DICT`` across runs.
    """
    source: dict = {"viz-repo": 128}
    result = map_deepgenome_to_knowledge_input(
        gene_symbol="GeneY",
        species_code=next(iter(SPECIES_CODE_MAP)),
        repo_id_dict=source,
    )
    assert result.get("repo_id_dict") == source
    assert result.get("repo_id_dict") is not source


def test_knowledge_mapper_pins_retrieve_only_toggles() -> None:
    """``is_generate`` and ``is_follow_up`` are both False.

    Pins the retrieve-only mode that the DeepGenome literature step
    relies on. If either flag flipped to True the knowledge subgraph
    would issue an LLM completion plus follow-up call, doubling
    latency and breaking the dispatch's ``knowledge_results = ...``
    list-of-docs shape (a generate response is a dict, not a list).
    """
    result = map_deepgenome_to_knowledge_input(
        gene_symbol="GeneZ",
        species_code=next(iter(SPECIES_CODE_MAP)),
        repo_id_dict={"viz-repo": 128},
    )
    assert result.get("is_generate") is False
    assert result.get("is_follow_up") is False


def test_knowledge_mapper_result_satisfies_knowledge_input() -> None:
    """The returned dict's key set matches ``KnowledgeInput``'s field union.

    Pins that the mapper's return value can be fed directly into a
    parent ``StateGraph(input_schema=KnowledgeInput)`` without
    extra keys leaking into the subgraph's working state. Future
    drift between the dispatch wire-up and the IO schema fails
    here first.
    """
    result = map_deepgenome_to_knowledge_input(
        gene_symbol="GeneW",
        species_code=next(iter(SPECIES_CODE_MAP)),
        repo_id_dict={"viz-repo": 128},
    )
    input_keys = getattr(KnowledgeInput, "__required_keys__") | getattr(
        KnowledgeInput, "__optional_keys__"
    )
    assert set(result.keys()) <= input_keys
    assert "user_query" in result
