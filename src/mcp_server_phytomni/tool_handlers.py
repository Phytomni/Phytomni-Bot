# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Compatibility exports for MCP tool handlers."""

from .mcp.handlers import (
    handle_analyst_agent,
    handle_brief_gene_agent,
    handle_chat_agent,
    handle_data_agent,
    handle_deep_genome_agent,
    handle_digital_design_agent,
    handle_gene_network_agent,
    handle_in_silico_research_agent,
    handle_knowledge_agent,
    handle_review_agent,
)

__all__ = [
    "handle_analyst_agent",
    "handle_brief_gene_agent",
    "handle_chat_agent",
    "handle_data_agent",
    "handle_deep_genome_agent",
    "handle_digital_design_agent",
    "handle_gene_network_agent",
    "handle_in_silico_research_agent",
    "handle_knowledge_agent",
    "handle_review_agent",
]
