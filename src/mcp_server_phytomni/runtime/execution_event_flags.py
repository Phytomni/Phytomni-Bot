# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Independent production switch for durable execution activity."""

from __future__ import annotations

from ..config.models.api import ApiConfig


def execution_event_production_enabled() -> bool:
    """Read the deploy-time switch without caching rollback state."""
    return ApiConfig().EXECUTION_EVENTS_ENABLED


def execution_log_artifact_enabled() -> bool:
    """Read the independent opt-in switch for downloadable operation logs."""
    return ApiConfig().EXECUTION_LOG_ENABLED
