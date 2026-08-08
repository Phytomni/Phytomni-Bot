# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the ordered Research input coordinator seam."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import pytest

from mcp_server_phytomni.agents.research import input_coordinator
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
    failure_stage: str | None = None
    failure: Exception | None = None
    snapshot_drift: bool = False
    prepared_query: str = "query"
    values: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self._state: dict[str, Any] = {
            "calls": [],
            "outbox_rows": [],
            "child_submissions": [],
            "metadata_requests": [],
            "extract_requests": [],
            **(self.values or {}),
        }

    @property
    def calls(self) -> list[str]:
        """Return the recorded callback order."""
        return self._state["calls"]

    @property
    def outbox_rows(self) -> list[Any]:
        """Return the recorded outbox rows."""
        return self._state["outbox_rows"]

    @property
    def child_submissions(self) -> list[Any]:
        """Return accidental child submissions."""
        return self._state["child_submissions"]

    @property
    def metadata_requests(self) -> list[Any]:
        """Return requests sent to metadata resolution."""
        return self._state["metadata_requests"]

    @property
    def extract_requests(self) -> list[Any]:
        """Return requests sent to extraction."""
        return self._state["extract_requests"]

    async def metadata(self, request: Any) -> Any:
        """Return the request's metadata input after recording the seam."""
        self.calls.append("metadata")
        self.metadata_requests.append(request)
        if self.failure_stage == "metadata":
            assert self.failure is not None
            raise self.failure
        return (
            self._state.get("metadata_value")
            if self._state.get("metadata_value") is not None
            else request.inventory_request
        )

    async def extract(self, request: Any) -> Any:
        """Return the request's extraction fixture after recording the seam."""
        self.calls.append("extract")
        self.extract_requests.append(request)
        if self.failure_stage == "extract":
            assert self.failure is not None
            raise self.failure
        return (
            self._state.get("extracted_value")
            if self._state.get("extracted_value") is not None
            else request.evidence
        )

    async def resolve(self, request: Any) -> Any:
        """Return the request's resolution fixture after recording the seam."""
        self.calls.append("resolve")
        if self.failure_stage == "resolve":
            assert self.failure is not None
            raise self.failure
        return (
            self._state.get("resolution_value")
            if self._state.get("resolution_value") is not None
            else request.resolution
        )

    async def revalidate(self, request: Any) -> Any:
        """Return the unchanged inventory after recording revalidation."""
        self.calls.append("revalidate")
        if self.failure_stage == "revalidate":
            assert self.failure is not None
            raise self.failure
        if self.snapshot_drift:
            return object()
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
            effective_query=self.prepared_query,
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


@dataclass(frozen=True)
class _FailureCase:
    """One stable callback failure and its public projection."""

    stage: str
    failure: Exception
    code: str
    retryable: bool
    status: int
    last_stage: str


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


async def test_coordinator_runs_bounded_request_recovery_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Request admission invokes recovery before any input side effect."""
    events: list[str] = []

    async def recover() -> None:
        events.append("recover")

    monkeypatch.setattr(
        input_coordinator, "recover_registered_request", recover
    )
    harness = _Harness()
    await ResearchInputCoordinator(_request(harness)).run(
        "run-001", "lease-001"
    )
    assert events == ["recover"]
    assert harness.calls[0] == "metadata"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        _FailureCase(
            "metadata",
            ValueError("placeholder obs://private/path"),
            "research_dataset_not_found",
            False,
            422,
            "metadata",
        ),
        _FailureCase(
            "metadata",
            RuntimeError("OBS endpoint secret/path"),
            "research_input_resolution_unavailable",
            True,
            503,
            "metadata",
        ),
        _FailureCase(
            "extract",
            ValueError("invalid document /private/paper.pdf"),
            "research_document_extraction_failed",
            False,
            422,
            "extraction",
        ),
        _FailureCase(
            "extract",
            RuntimeError("converter endpoint /private/paper.pdf"),
            "research_document_extraction_failed",
            True,
            503,
            "extraction",
        ),
        _FailureCase(
            "resolve",
            TimeoutError("provider request secret-token"),
            "research_input_resolution_unavailable",
            False,
            503,
            "resolver",
        ),
        _FailureCase(
            "resolve",
            ValueError("invalid or incomplete model output /tmp/raw.json"),
            "research_input_resolution_failed",
            False,
            422,
            "resolver",
        ),
        _FailureCase(
            "resolve",
            RuntimeError("ambiguous provider response https://provider"),
            "research_input_resolution_unavailable",
            False,
            503,
            "resolver",
        ),
        _FailureCase(
            "revalidate",
            ValueError("pasted HEAD changed obs://bucket/private.tsv"),
            "research_input_resolution_failed",
            False,
            422,
            "revalidation",
        ),
    ],
)
async def test_durable_input_failure_contract_is_stable_and_redacted(
    case: _FailureCase,
) -> None:
    """Durable failures keep stable safe fields at the coordinator edge."""
    harness = _Harness(
        failure_stage=case.stage,
        failure=case.failure,
    )
    with pytest.raises(Exception) as caught:
        await ResearchInputCoordinator(_request(harness)).run(
            "run-001", "lease-001"
        )
    error = caught.value
    assert getattr(error, "code") == case.code
    assert getattr(error, "retryable") is case.retryable
    assert getattr(error, "http_status_hint") == case.status
    assert getattr(error, "stage") == "input_resolution"
    assert getattr(error, "last_stage") == case.last_stage
    assert str(case.failure) not in str(error)
    assert "obs://" not in str(error)
    assert "https://" not in str(error)
    assert not harness.outbox_rows
    assert not harness.child_submissions


@pytest.mark.asyncio
async def test_final_native_failure_is_sanitized() -> None:
    """Native Pydantic/capability errors are terminal and sanitized."""
    harness = _Harness(final_validation_error=True)
    with pytest.raises(Exception) as caught:
        await ResearchInputCoordinator(_request(harness)).run(
            "run-001", "lease-001"
        )
    error = caught.value
    assert getattr(error, "code") == "research_input_resolution_failed"
    assert getattr(error, "retryable") is False
    assert getattr(error, "http_status_hint") == 422
    assert getattr(error, "last_stage") == "native_validation"
    assert "private native detail" not in str(error)
    assert not harness.outbox_rows
    assert not harness.child_submissions


def _evidence_metadata(content_digest: str = "content-1") -> dict[str, Any]:
    """Build persisted evidence metadata without document plaintext."""
    return {
        "coverage_digest": "coverage-1",
        "units": [
            {
                "evidence_id": "page-1",
                "source_kind": "pdf_page",
                "source_ordinal": 0,
                "source_span": None,
                "content_digest": content_digest,
                "dataset_ids": ("dataset-1",),
            }
        ],
        "document_digests": [
            {
                "document_id": "document-1",
                "content_digest": content_digest,
                "evidence_ids": ("page-1",),
            }
        ],
    }


@pytest.mark.asyncio
async def test_restart_reloads_managed_and_reextracts_all_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restart rebuilds transient evidence before reusing durable work."""
    events: list[str] = []
    persisted = _evidence_metadata()
    fresh = _evidence_metadata()
    harness = _Harness(
        prepared_query="trusted query",
        values={
            "metadata_value": (
                "fresh-inventory-after-managed-reresolve-and-head"
            ),
            "extracted_value": fresh,
        },
    )

    async def loader(run_id: str) -> ResearchCoordinatorRequest:
        events.append(f"load:{run_id}")
        assert "text" not in persisted["units"][0]
        return ResearchCoordinatorRequest(
            run_id=run_id,
            inventory_request="persisted-inventory",
            evidence=persisted,
            resolution=object(),
            effective_query="trusted query",
            dependencies=ResearchCoordinatorDependencies(
                build_inventory=harness.metadata,
                extract_evidence=harness.extract,
                resolve_descriptions=harness.resolve,
                revalidate_inventory=harness.revalidate,
                validate_native=harness.validate_native,
                persist_planning=harness.persist_planning,
                join_prepared=harness.join,
            ),
        )

    async def recover() -> None:
        events.append("recover")

    monkeypatch.setattr(
        input_coordinator, "recover_registered_request", recover
    )
    await ResearchInputCoordinator().resume_after_restart(
        "run-001", "lease-001", loader
    )

    assert events == ["load:run-001", "recover"]
    assert harness.metadata_requests[0].inventory_request == (
        "persisted-inventory"
    )
    assert harness.extract_requests[0].inventory_request == (
        "fresh-inventory-after-managed-reresolve-and-head"
    )
    assert harness.calls == [
        "metadata",
        "extract",
        "resolve",
        "revalidate",
        "validate_native",
        "persist_planning",
    ]


@pytest.mark.asyncio
async def test_restart_rejects_document_digest_drift_before_reusing_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changed page/content coverage fails before planning or child work."""
    persisted = _evidence_metadata("content-1")
    harness = _Harness(
        prepared_query="trusted query",
        values={"extracted_value": _evidence_metadata("content-2")},
    )

    async def loader(run_id: str) -> ResearchCoordinatorRequest:
        return ResearchCoordinatorRequest(
            run_id=run_id,
            inventory_request="persisted-inventory",
            evidence=persisted,
            resolution=object(),
            effective_query="trusted query",
            dependencies=ResearchCoordinatorDependencies(
                build_inventory=harness.metadata,
                extract_evidence=harness.extract,
                resolve_descriptions=harness.resolve,
                revalidate_inventory=harness.revalidate,
                validate_native=harness.validate_native,
                persist_planning=harness.persist_planning,
                join_prepared=harness.join,
            ),
        )

    async def recover() -> None:
        return None

    monkeypatch.setattr(
        input_coordinator, "recover_registered_request", recover
    )
    with pytest.raises(Exception) as caught:
        await ResearchInputCoordinator().resume_after_restart(
            "run-001", "lease-001", loader
        )
    error = caught.value
    assert getattr(error, "code") == "research_input_resolution_failed"
    assert getattr(error, "last_stage") == "revalidation"
    assert "content-1" not in str(error)
    assert "content-2" not in str(error)
    assert "text" not in persisted["units"][0]
    assert "persist_planning" not in harness.calls


@pytest.mark.asyncio
async def test_restart_rejects_pasted_head_snapshot_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changed pasted-object HEAD cannot reuse the old resolution."""
    persisted = _evidence_metadata()
    harness = _Harness(
        prepared_query="trusted query",
        snapshot_drift=True,
    )

    async def loader(run_id: str) -> ResearchCoordinatorRequest:
        return ResearchCoordinatorRequest(
            run_id=run_id,
            inventory_request="persisted-inventory",
            evidence=persisted,
            resolution=object(),
            effective_query="trusted query",
            dependencies=ResearchCoordinatorDependencies(
                build_inventory=harness.metadata,
                extract_evidence=harness.extract,
                resolve_descriptions=harness.resolve,
                revalidate_inventory=harness.revalidate,
                validate_native=harness.validate_native,
                persist_planning=harness.persist_planning,
                join_prepared=harness.join,
            ),
        )

    async def recover() -> None:
        return None

    monkeypatch.setattr(
        input_coordinator, "recover_registered_request", recover
    )
    with pytest.raises(Exception) as caught:
        await ResearchInputCoordinator().resume_after_restart(
            "run-001", "lease-001", loader
        )
    error = caught.value
    assert getattr(error, "code") == "research_input_resolution_failed"
    assert getattr(error, "last_stage") == "revalidation"
    assert "persist_planning" not in harness.calls
