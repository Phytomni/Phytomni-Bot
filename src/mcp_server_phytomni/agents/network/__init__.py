# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Gene network agent package exports.

This package exposes `GeneNetworkAgents` and `network_analysis` for
trait-associated gene network task submission, plus the
``network_to_deep_genome_chain`` entry that orchestrates the network
analysis followed by per-gene deepgenome submission.
"""

from .agent import GeneNetworkAgents, GeneNetworkState, network_analysis
from .chain import (
    ChainTop20MissingError,
    network_to_deep_genome_chain,
)

__all__ = [
    "ChainTop20MissingError",
    "GeneNetworkAgents",
    "GeneNetworkState",
    "network_analysis",
    "network_to_deep_genome_chain",
]
