# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Durable Review pause persistence failure mapping."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from mcp_server_phytomni.api.a2ui_review_persistence import (
    create_review_pause,
    review_persistence_error,
    review_projection_error,
    settle_failed_run,
    settle_review_pause,
    settle_review_projection_failure,
)
from mcp_server_phytomni.api.lifecycle_contract import SafeErrorCode
from mcp_server_phytomni.runtime.run_registry import RunRequestInfo

pytestmark = pytest.mark.unit


def _request_info() -> RunRequestInfo:
    """Build one bounded request identity for Review pause tests."""
    return RunRequestInfo(request_json="{}")


def test_review_projection_and_persistence_errors_are_stable() -> None:
    """Public Review errors keep their lifecycle codes."""
    assert (
        review_projection_error().code == SafeErrorCode.PROJECTION_FAILED.value
    )
    assert review_persistence_error().stage == "persistence"


def test_settle_failed_run_rejects_unknown_kwargs() -> None:
    """Unexpected settlement keywords stay TypeError, not persistence."""
    with pytest.raises(
        TypeError, match="unexpected review settlement keyword"
    ):
        settle_failed_run(
            SimpleNamespace(),
            run_id="r1",
            owner="u1",
            result={},
            extra=True,
        )


def test_create_review_pause_maps_sqlite_errors() -> None:
    """Create-time storage failures become persistence errors."""
    registry = SimpleNamespace(
        create_run=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sqlite3.Error("disk")
        )
    )
    with pytest.raises(type(review_persistence_error())):
        create_review_pause(
            registry,
            run_id="r1",
            owner="u1",
            request_info=_request_info(),
            result={},
        )


def test_settle_review_pause_requires_durable_success() -> None:
    """A False settle result is treated as persistence failure."""
    registry = SimpleNamespace(settle_run=lambda *_args, **_kwargs: False)
    with pytest.raises(type(review_persistence_error())):
        settle_review_pause(registry, run_id="r1", owner="u1", result={})


def test_settle_review_projection_failure_creates_failed_row() -> None:
    """New projection failures persist a failed Review run."""
    created: list[object] = []
    registry = SimpleNamespace(
        create_run=lambda spec, **kwargs: created.append((spec, kwargs))
    )
    settle_review_projection_failure(
        registry,
        run_id="r1",
        owner="u1",
        request_info=_request_info(),
        existing=False,
    )
    assert created
    assert created[0][0].agent == "review"
