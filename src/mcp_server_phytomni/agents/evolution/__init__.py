# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Evolution agent package exports.

This package exposes `evo_test_analysis` for taxonomy-aware evolution
analysis task submission.
"""

from .agent import evo_test_analysis

__all__ = [
    "evo_test_analysis",
]
