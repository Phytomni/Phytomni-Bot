# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Neutral contracts shared by server, client, and compatibility helpers."""

from .deep_genome import (
    DEEP_GENOME_PROGRESS_FIELDS,
    DEEP_GENOME_REPORT_FIELDS,
    sanitize_nonnegative_int,
)

__all__ = [
    "DEEP_GENOME_PROGRESS_FIELDS",
    "DEEP_GENOME_REPORT_FIELDS",
    "sanitize_nonnegative_int",
]
