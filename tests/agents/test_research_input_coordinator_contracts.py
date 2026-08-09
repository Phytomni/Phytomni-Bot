# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Focused public-contract tests for the Research coordinator."""

from __future__ import annotations

from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.research.input_contracts import (
    ResearchCoordinatorDependencies,
    ResearchCoordinatorRequest,
)
from mcp_server_phytomni.agents.research.input_coordinator import (
    ResearchInputCoordinator,
)
from tests.agents.test_research_input_coordinator import _Harness, _request

pytestmark = pytest.mark.agent


@pytest.mark.parametrize(
    ("options", "exception"),
    [
        ({"expected_revision": True}, ValueError),
        ({"plan_builder": "not-callable"}, TypeError),
    ],
)
def test_coordinator_rejects_invalid_constructor_controls(
    options: dict[str, object], exception: type[Exception]
) -> None:
    """Coordinator construction rejects invalid planning controls early."""
    with pytest.raises(exception):
        ResearchInputCoordinator(**cast(Any, options))


async def test_coordinator_rejects_invalid_public_run_and_resume_inputs() -> (
    None
):
    """Public run/restart seams reject malformed owners and loaders."""
    coordinator = ResearchInputCoordinator()

    with pytest.raises(Exception) as run_failure:
        await coordinator.run("run-1", "worker")
    assert getattr(run_failure.value, "code", None) == (
        "research_input_resolution_failed"
    )

    async def empty_loader(
        _run_id: str,
    ) -> ResearchCoordinatorRequest | None:
        return None

    with pytest.raises(Exception):
        await coordinator.resume_after_restart("", "worker", empty_loader)


async def test_coordinator_projects_restart_loader_failures() -> None:
    """Restart loader details are redacted as the stable restart failure."""
    coordinator = ResearchInputCoordinator()

    async def broken_loader(_run_id: str) -> ResearchCoordinatorRequest:
        raise RuntimeError("private loader detail")

    with pytest.raises(Exception) as caught:
        await coordinator.resume_after_restart(
            "run-001", "worker", broken_loader
        )

    assert getattr(caught.value, "code", None) == (
        "research_input_resolution_unavailable"
    )
    assert getattr(caught.value, "last_stage", None) == "restart"


async def test_coordinator_rejects_missing_durable_restart_request() -> None:
    """A valid restart identity still requires a durable request payload."""
    coordinator = ResearchInputCoordinator()

    async def missing_loader(
        _run_id: str,
    ) -> ResearchCoordinatorRequest | None:
        return None

    with pytest.raises(Exception) as caught:
        await coordinator.resume_after_restart(
            "run-001", "worker", missing_loader
        )

    assert getattr(caught.value, "code", None) == (
        "research_input_resolution_failed"
    )
    assert getattr(caught.value, "last_stage", None) == "restart"


async def test_coordinator_rejects_missing_metadata_callback() -> None:
    """A coordinator never begins a request without the metadata boundary."""
    request = _request(_Harness())._replace(
        dependencies=ResearchCoordinatorDependencies()
    )

    with pytest.raises(Exception) as caught:
        await ResearchInputCoordinator(request).run("run-001", "worker")

    assert getattr(caught.value, "code", None) == (
        "research_input_resolution_failed"
    )
