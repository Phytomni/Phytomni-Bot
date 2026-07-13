# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""In-silico research agent package exports.

This package exposes `InSilicoResearchAgents` and `in_silico_research` for
paper-driven computational research task submission.
"""

from .agent import (
    InSilicoResearchAgents,
    InSilicoResearchState,
    ResearchTaskContext,
    ResearchTaskInterop,
    in_silico_research,
)
from .interop import (
    ResearchA2APending,
    ResearchA2AResult,
    ResearchEvidence,
    ResearchInteropDependencies,
    collect_research_a2a,
)

__all__ = [
    "InSilicoResearchAgents",
    "InSilicoResearchState",
    "ResearchTaskContext",
    "ResearchTaskInterop",
    "ResearchA2APending",
    "ResearchA2AResult",
    "ResearchEvidence",
    "ResearchInteropDependencies",
    "collect_research_a2a",
    "in_silico_research",
]
