# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Public-safe projections for DataAgent and lifecycle failures."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mcp_server_phytomni.api.lifecycle_contract import (
    LifecycleInvariantError,
    SafeErrorCode,
)
import mcp_server_phytomni.api.stage_errors as stage_errors
from mcp_server_phytomni.api.stage_errors import (
    project_data_stage_error,
    safe_api_error_for_lifecycle,
)

pytestmark = pytest.mark.unit


def test_project_data_stage_error_uses_exception_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A classified stage failure becomes a retryable upstream envelope."""
    monkeypatch.setattr(
        stage_errors,
        "stage_failure_from_exception",
        lambda _exc: ("retrieval", "upstream_failed", 502),
    )
    error = project_data_stage_error(RuntimeError("hidden"))
    assert error is not None
    assert error.status_code == 502
    assert error.code == "upstream_failed"
    assert error.stage == "retrieval"
    assert error.retryable is True


def test_project_data_stage_error_reads_current_trace_when_unclassified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unclassified exceptions still surface the first traced stage error."""
    monkeypatch.setattr(
        stage_errors, "stage_failure_from_exception", lambda _exc: None
    )
    monkeypatch.setattr(
        stage_errors,
        "current_stage_trace",
        lambda: (
            SimpleNamespace(
                error_code=None, stage="start", final_http_status=None
            ),
            SimpleNamespace(
                error_code="upstream_timeout",
                stage="routing",
                final_http_status=504,
            ),
        ),
    )
    error = project_data_stage_error(RuntimeError("hidden"))
    assert error is not None
    assert error.status_code == 504
    assert error.code == "upstream_timeout"
    assert error.retryable is True


def test_project_data_stage_error_returns_none_without_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No classified failure and no traced event stays unprojected."""
    monkeypatch.setattr(
        stage_errors, "stage_failure_from_exception", lambda _exc: None
    )
    monkeypatch.setattr(stage_errors, "current_stage_trace", lambda: ())
    assert project_data_stage_error(RuntimeError("hidden")) is None


def test_safe_api_error_for_lifecycle_maps_known_codes() -> None:
    """Lifecycle invariants keep their public-safe status and stage."""
    persistence = safe_api_error_for_lifecycle(
        LifecycleInvariantError(SafeErrorCode.SUCCEEDED_WITHOUT_PERSISTENCE)
    )
    assert persistence.code == SafeErrorCode.RUN_PERSISTENCE_FAILED.value
    surface = safe_api_error_for_lifecycle(
        LifecycleInvariantError(SafeErrorCode.INPUT_REQUIRED_WITHOUT_SURFACE)
    )
    assert surface.stage == "projection"
    projection = safe_api_error_for_lifecycle(
        LifecycleInvariantError(SafeErrorCode.PROJECTION_FAILED)
    )
    assert projection.message == "result projection failed"
    other = safe_api_error_for_lifecycle(
        LifecycleInvariantError(SafeErrorCode.RUNNING_WITHOUT_WORK)
    )
    assert other.stage == "lifecycle"
    assert other.status_code == 500
