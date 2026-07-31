# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Private transport types shared by HTTP conversation-context routes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContextAgentRequest:
    """Shared transport fields for context invocation."""

    dialogue_id: str | None
    request_json: str
    debug: bool
    obs_file_list: list[str] | None


__all__ = ["ContextAgentRequest"]
