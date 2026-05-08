# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Environment agent package exports.

This package exposes `region_vci_analysis` for regional vegetation index
analysis task submission.
"""

from .agent import region_vci_analysis

__all__ = [
    "region_vci_analysis",
]
