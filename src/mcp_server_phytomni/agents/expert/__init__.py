# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Expert-mode autonomous routing package exports.

Re-exports the in-process tool selector used by the HTTP
``/v1/query/route`` Expert endpoint to pick one MCP agent for a
natural-language query.
"""

from .router import (
    ExpertProviderError,
    ExpertProviderTimeoutError,
    ExpertRoutingContractError,
    ExpertRoutingDeclinedError,
    ExpertRoutingOptions,
    ToolSelection,
    ToolSelectionError,
    complete_expert_routing,
    select_agent_tool,
    select_expert_tool,
)

__all__ = (
    "select_expert_tool",
    "select_agent_tool",
    "complete_expert_routing",
    "ToolSelectionError",
    "ToolSelection",
    "ExpertRoutingOptions",
    "ExpertRoutingContractError",
    "ExpertRoutingDeclinedError",
    "ExpertProviderTimeoutError",
    "ExpertProviderError",
)
