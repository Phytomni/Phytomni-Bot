# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the ordered Research input coordinator seam."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import pytest

from mcp_server_phytomni.agents.research.input_contracts import (
    ResearchCoordinatorDependencies,
    ResearchCoordinatorRequest,
)
from mcp_server_phytomni.agents.research.input_coordinator import (
    ResearchInputCoordinator,
)
from mcp_server_phytomni.agents.research.input_preparation import (
    PreparedResearchInput,
)

pytestmark = pytest.mark.agent


@dataclass
class _Harness:
    """Minimal injected ports that record coordinator ordering."""

    final_validation_error: bool = False

    def __post_init__(self) -> None:
        self.calls: list[str] = []
        self.outbox_rows: list[Any] = []
        self.child_submissions: list[Any] = []

    async def metadata(self, request: Any) -> Any:
        """Return the request's metadata input after recording the seam."""
        self.calls.append("metadata")
        return request.inventory_request

    async def extract(self, request: Any) -> Any:
        """Return the request's extraction fixture after recording the seam."""
        self.calls.append("extract")
        return request.evidence

    async def resolve(self, request: Any) -> Any:
        """Return the request's resolution fixture after recording the seam."""
        self.calls.append("resolve")
        return request.resolution

    async def revalidate(self, request: Any) -> Any:
        """Return the unchanged inventory after recording revalidation."""
        self.calls.append("revalidate")
        return request.inventory_request

    def validate_native(self, prepared: Any) -> Any:
        """Raise a private validation error when the harness requests it."""
        self.calls.append("validate_native")
        if self.final_validation_error:
            raise ValueError("private native detail")
        return prepared

    def join(self, inventory: Any, resolution: Any) -> PreparedResearchInput:
        """Keep this ordering test independent of the pure join fixtures."""
        del resolution
        return PreparedResearchInput(
            effective_query="query",
            obs_file_list=(),
            data_list=MappingProxyType({str(inventory): "description"}),
            inventory_digest="inventory",
            evidence_digest="evidence",
            execution_fingerprint="execution",
            authority_ids=(),
        )

    def persist_planning(self, run_id: str, **values: Any) -> None:
        """Record the planning persistence callback without child work."""
        del run_id
        self.calls.append("persist_planning")
        self.outbox_rows.extend(values.get("outbox_rows", ()))

    async def submit_children(self, rows: Any) -> None:
        """Record any accidental child submission attempt."""
        self.calls.append("submit_children")
        self.child_submissions.extend(rows)


def _request(harness: _Harness) -> ResearchCoordinatorRequest:
    """Build a request carrying fake domain values through the ports."""
    return ResearchCoordinatorRequest(
        run_id="run-001",
        inventory_request=object(),
        evidence=object(),
        resolution=object(),
        dependencies=ResearchCoordinatorDependencies(
            build_inventory=harness.metadata,
            extract_evidence=harness.extract,
            resolve_descriptions=harness.resolve,
            revalidate_inventory=harness.revalidate,
            validate_native=harness.validate_native,
            persist_planning=harness.persist_planning,
            submit_children=harness.submit_children,
            join_prepared=harness.join,
        ),
    )


async def test_final_validation_precedes_planning_or_child_work() -> None:
    """No planning/outbox/child call follows a failed native validation."""
    harness = _Harness(final_validation_error=True)
    with pytest.raises(Exception) as caught:
        await ResearchInputCoordinator(_request(harness)).run(
            "run-001", "lease-001"
        )
    assert getattr(caught.value, "code") == "research_input_resolution_failed"
    assert harness.calls == [
        "metadata",
        "extract",
        "resolve",
        "revalidate",
        "validate_native",
    ]
    assert not harness.outbox_rows
    assert not harness.child_submissions


async def test_coordinator_persists_only_after_native_validation() -> None:
    """Planning persistence is the first callback after native validation."""
    harness = _Harness()
    await ResearchInputCoordinator(_request(harness)).run(
        "run-001", "lease-001"
    )
    assert harness.calls == [
        "metadata",
        "extract",
        "resolve",
        "revalidate",
        "validate_native",
        "persist_planning",
    ]
    assert not harness.child_submissions
