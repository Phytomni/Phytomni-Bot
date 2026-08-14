# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Brief gene agent package exports.

Re-exports BriefGeneAgent, workflow state, the public brief_gene_function
wrapper, and pipeline helpers (BI query and literature retrieval)
used by MCP handlers and tests.
"""

from .agent import (
    BriefGeneAgent,
    BriefGeneAgentState,
    brief_gene_function,
)
from .pipeline import (
    GeneRetrieveRequest,
    gene_retrieve,
    run_bi_api,
)

__all__ = [
    "BriefGeneAgent",
    "BriefGeneAgentState",
    "GeneRetrieveRequest",
    "brief_gene_function",
    "gene_retrieve",
    "run_bi_api",
]
