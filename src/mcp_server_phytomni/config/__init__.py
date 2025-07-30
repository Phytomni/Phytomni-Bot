# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Configuration management module for Phytomni MCP Server.

This module provides comprehensive configuration management for all components
of the Phytomni platform. It includes both default configuration classes and
sensitive configuration handling for secure credential management.

Configuration Classes:
    - ChatConfig: Base chat model and API configuration
    - KnowledgeConfig: Knowledge retrieval and RAG system settings
    - DataConfig: Database connection and query configuration
    - AnalystConfig: Bioinformatics workflow analysis settings
    - ReviewConfig: Literature review and research configuration
    - DeepGenomeConfig: Gene function analysis parameters
    - InSilicoResearchConfig: Computational research settings
    - SensitiveConfig: Secure credential and API key management

Each configuration class follows a hierarchical inheritance pattern, allowing
specialized configurations to build upon base settings while maintaining
consistency across the platform.

Usage:
    from mcp_server_phytomni.config import ChatConfig, SensitiveConfig

    config = ChatConfig()
    sensitive = SensitiveConfig()

Authors:
    xieshang (xieshang0608@gmail.com)
    guxiaofeng (guxiaofeng@caas.cn)

Copyright:
    Biotechnology Research Institute, Chinese Academy of Agricultural Sciences
    2024-2025. All rights reserved.
"""
from .defaults import AnalystConfig, ChatConfig, DataConfig
from .defaults import DeepGenomeConfig, InSilicoResearchConfig
from .defaults import KnowledgeConfig, ReviewConfig
from .settings import SensitiveConfig


__all__ = ['AnalystConfig', 'ChatConfig', 'DataConfig',
           'DeepGenomeConfig', 'InSilicoResearchConfig', 'KnowledgeConfig',
           'ReviewConfig', 'SensitiveConfig']
