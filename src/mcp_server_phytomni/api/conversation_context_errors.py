# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Public 409 errors for conversation-context HTTP and stream setup."""

from __future__ import annotations

from .lifecycle_contract import SafeApiError

__all__ = [
    "conversation_context_rebuild_required_error",
    "conversation_context_turn_in_progress_error",
]


def conversation_context_rebuild_required_error() -> SafeApiError:
    """Return the retryable public error for a required context rebuild."""
    return SafeApiError(
        status_code=409,
        code="conversation_context_rebuild_required",
        message="conversation context rebuild required",
        stage="context",
        retryable=True,
    )


def conversation_context_turn_in_progress_error() -> SafeApiError:
    """Return the retryable public error for an in-flight context turn."""
    return SafeApiError(
        status_code=409,
        code="conversation_context_turn_in_progress",
        message="conversation context turn in progress",
        stage="context",
        retryable=True,
    )
