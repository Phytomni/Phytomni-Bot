# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Compatibility entrypoint for the Phytomni MCP server.

This module re-exports MCP schemas, dispatch tables, `dispatch_tool`, and
`serve` for clients that import the historical server module. Running it as a
module starts the stdio MCP server.
"""

import asyncio

from .mcp.app import TOOL_ARGUMENT_MODELS, TOOL_HANDLERS, dispatch_tool, serve
from .mcp.schemas import (
    AnalystAgent,
    BriefGeneAgent,
    ChatAgent,
    DataAgent,
    DeepGenomeAgent,
    DigitalDesignAgent,
    GeneNetworkAgent,
    InSilicoResearchAgent,
    KnowledgeAgent,
    PhytomniAgents,
    ReviewAgent,
)

__all__ = [
    "AnalystAgent",
    "BriefGeneAgent",
    "ChatAgent",
    "DataAgent",
    "DeepGenomeAgent",
    "DigitalDesignAgent",
    "GeneNetworkAgent",
    "InSilicoResearchAgent",
    "KnowledgeAgent",
    "PhytomniAgents",
    "ReviewAgent",
    "TOOL_ARGUMENT_MODELS",
    "TOOL_HANDLERS",
    "dispatch_tool",
    "serve",
]


if __name__ == "__main__":
    asyncio.run(serve())
