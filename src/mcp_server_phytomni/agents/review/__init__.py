# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Review agent package exports.

Re-exports DeepResearchAgent, its workflow state, and the deep_research
compatibility wrapper for literature review generation.
"""

from .agent import DeepResearchAgent, DeepResearchState, deep_research

__all__ = [
    "DeepResearchAgent",
    "DeepResearchState",
    "deep_research",
]
