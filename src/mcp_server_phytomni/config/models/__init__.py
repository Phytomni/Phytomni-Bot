# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Configuration model leaves."""

from .agents import (
    AnalystConfig,
    BriefGeneConfig,
    ChatConfig,
    DataConfig,
    DeepGenomeConfig,
    DigitalDesignConfig,
    EnvironmentConfig,
    GeneNetworkConfig,
    InSilicoResearchConfig,
    KnowledgeConfig,
    ReviewConfig,
    resolve_compute_resource,
)
from .api import ApiConfig
from .base import ServerConfig
from .citation import CitationConfig
from .reference import (
    PromptLeaf,
    PromptTemplates,
    RegionMap,
    SpeciesDataIndex,
    SpeciesEntryValue,
)

__all__ = [
    "AnalystConfig",
    "ApiConfig",
    "BriefGeneConfig",
    "CitationConfig",
    "PromptLeaf",
    "ChatConfig",
    "DataConfig",
    "DeepGenomeConfig",
    "DigitalDesignConfig",
    "SpeciesDataIndex",
    "EnvironmentConfig",
    "GeneNetworkConfig",
    "PromptTemplates",
    "InSilicoResearchConfig",
    "KnowledgeConfig",
    "RegionMap",
    "ReviewConfig",
    "ServerConfig",
    "SpeciesEntryValue",
    "resolve_compute_resource",
]
