# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Expert-mode autonomous routing package exports.

Re-exports the in-process tool selector used by the HTTP
``/v1/query/route`` Expert endpoint to pick one MCP agent for a
natural-language query.
"""

from .router import ToolSelection, select_agent_tool

__all__ = ["ToolSelection", "select_agent_tool"]
