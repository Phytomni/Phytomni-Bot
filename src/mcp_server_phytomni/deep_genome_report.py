# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Compatibility exports for DeepGenome report helpers."""

from .agents.deep_genome.report import (
    DEEP_GENOME_CONFIG,
    DeepGenomeReportMixin,
)

__all__ = [
    "DEEP_GENOME_CONFIG",
    "DeepGenomeReportMixin",
]
