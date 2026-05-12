# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for validated static-JSON dataset loaders."""

import json

import pytest
import yaml
from pydantic import ValidationError

from mcp_server_phytomni.config.data_loaders import (
    load_prompt_templates,
    load_species_data,
)
from mcp_server_phytomni.config.defaults import (
    PRE_PREPARED_DATA_PATH,
    PRE_PREPARED_REGION_PATH,
    PROMPT_PATH,
    PromptTemplates,
    RegionMap,
    SpeciesDataIndex,
)

pytestmark = pytest.mark.unit


def test_bundled_species_data_list_passes_schema_validation():
    """Verify the shipped species_data_list.json satisfies SpeciesDataIndex."""
    data = load_species_data(str(PRE_PREPARED_DATA_PATH))

    assert isinstance(data, dict)
    assert data  # non-empty
    for analysis_type, species_map in data.items():
        assert isinstance(analysis_type, str)
        assert isinstance(species_map, dict)
        for species, leaf_map in species_map.items():
            assert isinstance(species, str)
            assert isinstance(leaf_map, dict)
            for key, value in leaf_map.items():
                assert isinstance(key, str)
                # Leaf value is either a description string (3-level
                # entries) or a sub-dict of {file_path: description}
                # (4-level entries such as gene_expression_analysis).
                assert isinstance(value, (str, dict))
                if isinstance(value, dict):
                    for path, desc in value.items():
                        assert isinstance(path, str)
                        assert isinstance(desc, str)


def test_bundled_region_map_passes_schema_validation():
    """Verify the shipped region_map.json satisfies RegionMap."""
    raw = json.loads(PRE_PREPARED_REGION_PATH.read_text(encoding="utf-8"))
    validated = RegionMap.model_validate(raw)

    provinces = validated.provinces()
    assert provinces
    sample = provinces[0]
    assert isinstance(sample, str)
    cities = validated.cities_for(sample)
    assert cities


def test_load_species_data_rejects_string_leaf_instead_of_description_map(
    tmp_path,
):
    """Verify load_species_data raises ValidationError on shape mismatch.

    Args:
        tmp_path: Temporary directory used to write a corrupt JSON fixture.
    """
    corrupt = tmp_path / "species_data.json"
    corrupt.write_text(
        json.dumps(
            {
                "analysis_type": {
                    "species": "expected-dict-got-string",
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_species_data(str(corrupt))


def test_species_data_index_helpers_return_top_level_keys():
    """Verify SpeciesDataIndex.analysis_types and species_for round-trip."""
    index = SpeciesDataIndex.model_validate(
        {
            "evolution_analysis": {
                "ath": {"/obs/data/file.fa": "AthFile"},
                "osa": {"/obs/data/rice.fa": "RiceFile"},
            },
            "deepgo2_analysis": {
                "ath": {"/obs/data/go.tsv": "AthGO"},
            },
        }
    )

    assert set(index.analysis_types()) == {
        "evolution_analysis",
        "deepgo2_analysis",
    }
    assert set(index.species_for("evolution_analysis")) == {"ath", "osa"}
    assert set(index.species_for("deepgo2_analysis")) == {"ath"}


def test_bundled_prompt_templates_pass_schema_validation():
    """Verify the shipped .prompts.yaml satisfies PromptTemplates."""
    templates = load_prompt_templates(str(PROMPT_PATH))

    sections = set(templates.keys())
    assert sections >= {"system", "template", "user"}
    for section, keys in templates.items():
        assert isinstance(section, str)
        assert isinstance(keys, dict)
        for key, leaf in keys.items():
            assert isinstance(key, str)
            assert isinstance(leaf, (str, dict))
            if isinstance(leaf, dict):
                for name, template in leaf.items():
                    assert isinstance(name, str)
                    assert isinstance(template, str)


def test_load_prompt_templates_rejects_non_string_leaf(tmp_path):
    """Verify load_prompt_templates raises on non-string leaves.

    Args:
        tmp_path: Temporary directory used to write a corrupt YAML fixture.
    """
    corrupt = tmp_path / "prompts.yaml"
    corrupt.write_text(
        yaml.safe_dump(
            {
                "system": {
                    "ai4ps": 42,
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_prompt_templates(str(corrupt))


def test_prompt_templates_helpers_return_section_and_key_lists():
    """Verify PromptTemplates.sections and keys_for round-trip cleanly."""
    templates = PromptTemplates.model_validate(
        {
            "system": {"ai4ps": "you are an assistant"},
            "user": {
                "environment": {"get_code": "extract codes"},
                "analysis": "describe the data",
            },
        }
    )

    assert set(templates.sections()) == {"system", "user"}
    assert set(templates.keys_for("user")) == {"environment", "analysis"}
