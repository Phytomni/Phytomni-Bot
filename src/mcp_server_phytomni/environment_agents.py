# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Compatibility exports for environment agent workflows."""

from .agents.environment.agent import (
    DEFAULT_ACCESS_KEY_ID,
    DEFAULT_SECRET_ACCESS_KEY,
    ENVIRONMENT_CONFIG,
    SENSITIVE_CONFIG,
    region_vci_analysis,
)

__all__ = [
    "region_vci_analysis",
    "ENVIRONMENT_CONFIG",
    "DEFAULT_ACCESS_KEY_ID",
    "SENSITIVE_CONFIG",
    "DEFAULT_SECRET_ACCESS_KEY",
]
