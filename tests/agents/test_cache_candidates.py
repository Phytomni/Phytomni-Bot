# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for first-wave low-risk cache integration points."""

import json

import pytest

from mcp_server_phytomni.analyst_agents import get_data_list
from mcp_server_phytomni.deep_genome_agents import network_to_string

pytestmark = pytest.mark.agent


def test_get_data_list_tracks_config_file_changes(tmp_path):
    data_file = tmp_path / "species_data.json"
    data_file.write_text(
        json.dumps({"analysis": {"ath": ["first"]}}),
        encoding="utf-8",
    )

    assert get_data_list(str(data_file), "analysis", "ath") == ["first"]

    data_file.write_text(
        json.dumps({"analysis": {"ath": ["second", "third"]}}),
        encoding="utf-8",
    )

    assert get_data_list(str(data_file), "analysis", "ath") == [
        "second",
        "third",
    ]


def test_network_to_string_uses_cache_for_identical_inputs():
    network_to_string.cache_clear()

    gene_network_list = [("ath", "AT1G01010")]
    species_gene_symbol_dict = {("ath", "AT1G01010"): ["NAC001"]}
    species_gene_anno_dict = {
        ("ath", "AT1G01010"): {
            "description": "NAC domain transcription factor",
            "go": [{"go_id": "GO:0006355", "go_name": "regulation"}],
            "interpro": [
                {
                    "interpro_id": "IPR003441",
                    "interpro_name": "NAC domain",
                }
            ],
            "mapman": [
                {
                    "mapman": "27.3",
                    "mapman_description": "RNA regulation",
                }
            ],
        }
    }

    first = network_to_string(
        gene_network_list,
        species_gene_symbol_dict,
        species_gene_anno_dict,
        "Orthologous",
        top_n=1,
    )
    second = network_to_string(
        gene_network_list,
        species_gene_symbol_dict,
        species_gene_anno_dict,
        "Orthologous",
        top_n=1,
    )

    assert first == second
    assert "NAC domain transcription factor" in first
    assert network_to_string.cache_info() == {
        "hits": 1,
        "misses": 1,
        "count": 1,
    }
