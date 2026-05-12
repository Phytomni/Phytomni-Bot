# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Validated loaders for static datasets bundled with the package.

Functions: load_species_data, load_prompt_templates.
"""

from typing import Dict

from yaml import safe_load

from ..common.prompts import load_json_file
from .defaults import (
    PromptLeaf,
    PromptTemplates,
    SpeciesDataIndex,
    SpeciesEntryValue,
)


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


def load_prompt_templates(
    file_path: str,
) -> Dict[str, Dict[str, PromptLeaf]]:
    """Load and validate the prompt templates YAML.

    The file is expected to follow the schema ``{section: {key: leaf}}``
    where ``section`` is one of ``system``, ``template``, ``user``, and
    ``leaf`` is either a prompt string (depth-2 entries) or a sub-dict
    of ``{name: template}`` entries (depth-3 entries used for nested
    prompt paths such as ``user/environment/get_code_query``).

    Args:
        file_path: Path to the .prompts.yaml file.

    Returns:
        Validated mapping equivalent to
        ``PromptTemplates.model_validate`` applied to the parsed YAML,
        unwrapped to the inner ``root`` dict.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        pydantic.ValidationError: If the YAML shape diverges from the
            two-or-three-level nested-dict schema with string leaves.
    """
    with open(file_path, "r", encoding="utf-8") as handle:
        raw = safe_load(handle)
    return PromptTemplates.model_validate(raw).root
