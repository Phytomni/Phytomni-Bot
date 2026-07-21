# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Validated models for bundled reference data and prompt templates."""

from pydantic import RootModel

SpeciesEntryValue = str | dict[str, str]


class SpeciesDataIndex(
    RootModel[dict[str, dict[str, dict[str, SpeciesEntryValue]]]]
):
    """Validation schema for the bundled species data index."""

    def analysis_types(self) -> list[str]:
        """Return the configured analysis-type keys."""
        return list(self.root.keys())

    def species_for(self, analysis_type: str) -> list[str]:
        """Return species keys configured under one analysis type."""
        return list(self.root[analysis_type].keys())


class RegionMap(RootModel[dict[str, dict[str, dict[str, str]]]]):
    """Validation schema for the bundled region map."""

    def provinces(self) -> list[str]:
        """Return the configured province keys."""
        return list(self.root.keys())

    def cities_for(self, province: str) -> list[str]:
        """Return city keys configured under one province."""
        return list(self.root[province].keys())


PromptLeaf = str | dict[str, str]


class PromptTemplates(RootModel[dict[str, dict[str, PromptLeaf]]]):
    """Validation schema for the bundled prompt-template YAML."""

    def sections(self) -> list[str]:
        """Return the configured top-level prompt sections."""
        return list(self.root.keys())

    def keys_for(self, section: str) -> list[str]:
        """Return prompt keys configured under one section."""
        return list(self.root[section].keys())


__all__ = [
    "PromptLeaf",
    "PromptTemplates",
    "RegionMap",
    "SpeciesDataIndex",
    "SpeciesEntryValue",
]
