# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Durable Review pause transitions used by the A2UI HTTP runtime."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunRequestInfo,
    RunSpec,
)
from .lifecycle_contract import (
    SafeApiError,
    SafeErrorCode,
    empty_agent_result,
    run_persistence_error,
)


def review_projection_error() -> SafeApiError:
    """Return the stable public error for Review surface failures."""
    return SafeApiError(
        status_code=500,
        code=SafeErrorCode.PROJECTION_FAILED.value,
        message="review surface projection failed",
        stage="projection",
    )


def review_persistence_error() -> SafeApiError:
    """Return the stable public error for Review pause persistence failures."""
    return run_persistence_error()


def settle_failed_run(
    registry: RunRegistry,
    *,
    run_id: str,
    owner: str,
    result: dict[str, Any],
    error: str | None = None,
    **kwargs: Any,
) -> bool:
    """Apply one owner-scoped failed A2UI run transition."""
    expected_revision = kwargs.pop("expected_revision", None)
    if kwargs:
        raise TypeError("unexpected review settlement keyword")
    return registry.settle_run(
        run_id,
        owner=owner,
        status="failed",
        result=result,
        error=error,
        expected_revision=expected_revision,
    )


def review_run_spec(run_id: str, owner: str) -> RunSpec:
    """Build the shared local Review run identity specification."""
    return RunSpec(
        run_id=run_id,
        user_id=owner,
        agent="review",
        origin="local",
    )


def create_review_pause(
    registry: RunRegistry,
    *,
    run_id: str,
    owner: str,
    request_info: RunRequestInfo,
    result: dict[str, Any],
) -> None:
    """Create a durable Review pause before exposing it to the caller."""
    try:
        registry.create_run(
            review_run_spec(run_id, owner),
            outcome=RunOutcome(status="input_required", result=result),
            request_info=request_info,
        )
    except (sqlite3.Error, OSError) as exc:
        raise review_persistence_error() from exc


def settle_review_pause(
    registry: RunRegistry,
    *,
    run_id: str,
    owner: str,
    result: dict[str, Any],
    **kwargs: Any,
) -> None:
    """Settle a re-interrupted Review run and require durable success."""
    expected_revision = kwargs.pop("expected_revision", None)
    if kwargs:
        raise TypeError("unexpected review settlement keyword")
    try:
        persisted = registry.settle_run(
            run_id,
            owner=owner,
            status="input_required",
            result=result,
            expected_revision=expected_revision,
        )
    except (sqlite3.Error, OSError) as exc:
        raise review_persistence_error() from exc
    if persisted is not True:
        raise review_persistence_error()


def settle_review_projection_failure(
    registry: RunRegistry,
    *,
    run_id: str,
    owner: str,
    request_info: RunRequestInfo | None = None,
    existing: bool,
    **kwargs: Any,
) -> None:
    """Persist a failed row after Review surface projection fails."""
    expected_revision = kwargs.pop("expected_revision", None)
    if kwargs:
        raise TypeError("unexpected review settlement keyword")
    if existing:
        if expected_revision is None:
            raise review_persistence_error()
        try:
            persisted = settle_failed_run(
                registry,
                run_id=run_id,
                owner=owner,
                result=empty_agent_result(),
                error=SafeErrorCode.PROJECTION_FAILED.value,
                expected_revision=expected_revision,
            )
        except (sqlite3.Error, OSError) as exc:
            raise review_persistence_error() from exc
        if persisted is not True:
            raise review_persistence_error()
        return

    if request_info is None:
        raise review_persistence_error()
    try:
        registry.create_run(
            review_run_spec(run_id, owner),
            outcome=RunOutcome(
                status="failed",
                result=empty_agent_result(),
                error=SafeErrorCode.PROJECTION_FAILED.value,
            ),
            request_info=request_info,
        )
    except (sqlite3.Error, OSError) as exc:
        raise review_persistence_error() from exc
