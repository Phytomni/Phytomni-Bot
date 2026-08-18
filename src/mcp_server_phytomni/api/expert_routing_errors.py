# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Public-safe HTTP mapping for Expert routing-provider faults."""

from __future__ import annotations

import logging

from ..agents.expert import ExpertProviderTimeoutError
from ..runtime.locale import current_effective_locale
from .lifecycle_contract import SafeApiError, SafeErrorCode, expert_safe_error

__all__ = ["expert_routing_provider_error"]

_LOGGER = logging.getLogger(__name__)


def expert_routing_provider_error(exc: Exception) -> SafeApiError:
    """Return the V0 public envelope for a routing-provider fault."""
    _LOGGER.warning(
        "Expert routing provider failed",
        extra={
            "error_class": type(exc).__name__,
            "stage": "routing",
        },
    )
    if isinstance(exc, ExpertProviderTimeoutError):
        return expert_safe_error(
            SafeErrorCode.UPSTREAM_TIMEOUT,
            status_code=504,
            locale=current_effective_locale(),
            stage="routing",
            retryable=True,
        )
    return expert_safe_error(
        SafeErrorCode.ROUTING_UPSTREAM_FAILED,
        status_code=502,
        locale=current_effective_locale(),
        stage="routing",
        retryable=True,
    )
