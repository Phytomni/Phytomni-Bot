# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Compatibility facade for Phytomni's non-secret configuration models.

The concrete models live under :mod:`mcp_server_phytomni.config.models`.
This module intentionally keeps the historical import paths stable for agent
packages, scripts, and downstream deployments.
"""

from .api_limits import ApiLimitsConfig
from .models.agents import (
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
from .models.api import ApiConfig
from .models.base import (
    DOWNLOAD_PATH,
    PARENT_PATH,
    PRE_PREPARED_DATA_PATH,
    PRE_PREPARED_REGION_PATH,
    PROMPT_PATH,
    TEMP_PATH,
    ServerConfig,
)
from .models.citation import CitationConfig
from .models.reference import (
    PromptLeaf,
    PromptTemplates,
    RegionMap,
    SpeciesDataIndex,
    SpeciesEntryValue,
)
from .required_env import (
    ANALYST_REQUIRED_ENDPOINT_FIELDS,
    DATA_REQUIRED_ENDPOINT_FIELDS,
    DEEP_GENOME_REQUIRED_ENDPOINT_FIELDS,
    SERVER_REQUIRED_ENDPOINT_FIELDS,
)

__all__ = [
    "ANALYST_REQUIRED_ENDPOINT_FIELDS",
    "AnalystConfig",
    "ApiConfig",
    "ApiLimitsConfig",
    "BriefGeneConfig",
    "CitationConfig",
    "ChatConfig",
    "DATA_REQUIRED_ENDPOINT_FIELDS",
    "DataConfig",
    "DEEP_GENOME_REQUIRED_ENDPOINT_FIELDS",
    "DeepGenomeConfig",
    "DigitalDesignConfig",
    "DOWNLOAD_PATH",
    "EnvironmentConfig",
    "GeneNetworkConfig",
    "InSilicoResearchConfig",
    "KnowledgeConfig",
    "PARENT_PATH",
    "PRE_PREPARED_DATA_PATH",
    "PRE_PREPARED_REGION_PATH",
    "PROMPT_PATH",
    "PromptLeaf",
    "PromptTemplates",
    "RegionMap",
    "ReviewConfig",
    "SERVER_REQUIRED_ENDPOINT_FIELDS",
    "ServerConfig",
    "resolve_compute_resource",
    "SpeciesDataIndex",
    "SpeciesEntryValue",
    "TEMP_PATH",
]
