# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the pure external-capability selection planner."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

import pytest

from mcp_server_phytomni.interop.capabilities import InteropCapability
from mcp_server_phytomni.interop.planner import (
    InteropPlanningError,
    InteropTaskSpec,
    plan_interop_capabilities,
)

pytestmark = pytest.mark.unit


def _capability(
    target_id: str,
    remote_name: str = "search",
    *,
    required: list[str] | None = None,
    kind: str = "mcp",
) -> InteropCapability:
    """Build one metadata-only capability fixture."""
    return InteropCapability(
        target_id=target_id,
        kind=kind,
        remote_name=remote_name,
        qualified_name=f"{target_id}__{remote_name}",
        description="fixture",
        input_schema={
            "type": "object",
            "properties": {key: {"type": "string"} for key in required or []},
            "required": required or [],
        },
    )


def _task(*fields: str, name: str = "search") -> InteropTaskSpec:
    """Build a local task schema with the supplied property names."""
    return InteropTaskSpec(
        name=name,
        input_schema={
            "type": "object",
            "properties": {field: {"type": "string"} for field in fields},
        },
    )


def test_planner_filters_allowlist_and_orders_candidates() -> None:
    """Only requested targets with compatible schemas are selected."""
    plan = plan_interop_capabilities(
        _task("query"),
        mode="auto",
        target_ids=["peer-b", "peer-a"],
        capabilities=[
            _capability("peer-a", required=["query"]),
            _capability("peer-b", required=["query"], kind="a2a"),
            _capability("peer-c", required=["query"]),
            _capability("peer-a", remote_name="other", required=["query"]),
            _capability("peer-a", remote_name="search", required=["missing"]),
        ],
    )

    assert [item.qualified_name for item in plan.candidates] == [
        "peer-b__search",
        "peer-a__search",
    ]
    assert plan.use_local_fallback is False
    assert plan.reason is None


def test_off_mode_does_not_iterate_capabilities() -> None:
    """The off path is pure local and never even inspects discovery data."""

    class ForbiddenCapabilities(Iterable[InteropCapability]):
        """Iterable that fails if the planner inspects it."""

        def __iter__(self) -> Iterator[InteropCapability]:
            raise AssertionError("off mode must not inspect capabilities")

        def __len__(self) -> int:
            """Expose the second protocol method for a complete iterable."""
            return 0

    plan = plan_interop_capabilities(
        _task("query"),
        mode="off",
        target_ids=["peer"],
        capabilities=ForbiddenCapabilities(),
    )

    assert not plan.candidates
    assert plan.use_local_fallback is True
    assert plan.reason == "disabled"


def test_auto_mode_falls_back_when_no_capability_matches() -> None:
    """Auto mode reports a local fallback instead of a pseudo peer result."""
    plan = plan_interop_capabilities(
        _task("query"),
        mode="auto",
        target_ids=["peer"],
        capabilities=[_capability("peer", required=["other"])],
    )

    assert not plan.candidates
    assert plan.use_local_fallback is True
    assert plan.reason == "no_eligible_capability"


def test_required_mode_fails_closed_without_a_candidate() -> None:
    """Required mode raises a stable planning error when selection is empty."""
    with pytest.raises(InteropPlanningError) as exc_info:
        plan_interop_capabilities(
            _task("query"),
            mode="required",
            target_ids=[],
            capabilities=[_capability("peer", required=["query"])],
        )

    assert exc_info.value.code == "no_eligible_capability"
    assert str(exc_info.value) == (
        "required interop request has no eligible capability"
    )


def test_invalid_mode_is_rejected_before_capability_selection() -> None:
    """Unexpected mode values cannot bypass the explicit policy gate."""
    invalid_mode: Any = "always"
    with pytest.raises(InteropPlanningError) as exc_info:
        plan_interop_capabilities(
            _task("query"),
            mode=invalid_mode,
            target_ids=["peer"],
            capabilities=[],
        )

    assert exc_info.value.code == "invalid_mode"
