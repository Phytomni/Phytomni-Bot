# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Validated loaders for the static JSON datasets bundled with the package.

Functions: load_species_data.
"""

from typing import Dict

from ..common.prompts import load_json_file
from .defaults import SpeciesDataIndex, SpeciesEntryValue


def load_species_data(
    file_path: str,
) -> Dict[str, Dict[str, Dict[str, SpeciesEntryValue]]]:
    """Load and validate the species data list.

    The file is expected to follow the schema
    ``{analysis_type: {species_name: {key: leaf}}}`` where ``leaf`` is
    either an OBS file description string or a sub-dict of
    ``{file_path: description}`` entries (used by analyses such as
    ``gene_expression_analysis`` that subdivide species into cultivars,
    tissues, or treatments).

    Args:
        file_path: Path to the species data list JSON file.

    Returns:
        Validated mapping equivalent to ``SpeciesDataIndex.model_validate``
        applied to the parsed JSON, unwrapped to the inner ``root`` dict.

    Raises:
        FileNotFoundError: If the JSON file does not exist.
        pydantic.ValidationError: If the JSON shape diverges from the
            three-level nested-dict schema with ``str | dict`` leaves.
    """
    raw = load_json_file(file_path)
    return SpeciesDataIndex.model_validate(raw).root
