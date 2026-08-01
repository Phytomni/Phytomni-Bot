# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Public-safe projections for traced agent and lifecycle failures."""

from __future__ import annotations

from ..runtime.stage_trace import (
    current_stage_trace,
    stage_failure_from_exception,
)
from .lifecycle_contract import (
    LifecycleInvariantError,
    SafeApiError,
    SafeErrorCode,
    run_persistence_error,
)

__all__ = [
    "project_data_stage_error",
    "safe_api_error_for_lifecycle",
]


def project_data_stage_error(exc: BaseException) -> SafeApiError | None:
    """Project the first failed DataAgent stage into a safe API error."""
    failure = stage_failure_from_exception(exc)
    if failure is not None:
        stage, error_code, final_http_status = failure
        status_code = final_http_status or 500
    else:
        event = next(
            (
                candidate
                for candidate in current_stage_trace()
                if candidate.error_code is not None
            ),
            None,
        )
        if event is None:
            return None
        stage = event.stage
        error_code = event.error_code or "internal_invariant_failed"
        status_code = event.final_http_status or 500
    message = {
        400: "invalid request",
        502: "upstream service failed",
        503: "service unavailable",
        504: "upstream service timed out",
    }.get(status_code, "internal server error")
    return SafeApiError(
        status_code=status_code,
        code=error_code,
        message=message,
        stage=stage,
        retryable=status_code in {502, 503, 504},
    )


def safe_api_error_for_lifecycle(
    exc: LifecycleInvariantError,
) -> SafeApiError:
    """Map one internal lifecycle invariant failure to a safe HTTP error."""
    if exc.code is SafeErrorCode.SUCCEEDED_WITHOUT_PERSISTENCE:
        return run_persistence_error()
    if exc.code is SafeErrorCode.INPUT_REQUIRED_WITHOUT_SURFACE:
        return SafeApiError(
            status_code=500,
            code=exc.code.value,
            message="input required response is invalid",
            stage="projection",
        )
    if exc.code is SafeErrorCode.PROJECTION_FAILED:
        return SafeApiError(
            status_code=500,
            code=exc.code.value,
            message="result projection failed",
            stage="projection",
        )
    return SafeApiError(
        status_code=500,
        code=exc.code.value,
        message="agent run response violated lifecycle contract",
        stage="lifecycle",
    )
