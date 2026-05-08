# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Chat agent service exports.

This package exposes chat service helpers for base LLM calls, optional OBS
upload context, and follow-up question generation.
"""

from .service import phyto_chat, phyto_chat_with_follow

__all__ = [
    "phyto_chat",
    "phyto_chat_with_follow",
]
