# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the ordered Research input coordinator seam."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.agents.research import (
    dispatch_runtime,
    input_coordinator,
)
from mcp_server_phytomni.agents.research.dispatch_outbox import (
    persist_plan_and_outbox,
)
from mcp_server_phytomni.agents.research.input_contracts import (
    ResearchCoordinatorDependencies,
    ResearchCoordinatorRequest,
)
from mcp_server_phytomni.agents.research.input_coordinator import (
    ResearchInputCoordinator,
)
from mcp_server_phytomni.agents.research.input_preparation import (
    PreparedResearchAuthority,
    PreparedResearchInput,
)
from mcp_server_phytomni.agents.research.planning import (
    ResearchChildPlan,
    ResearchPlan,
)
from mcp_server_phytomni.runtime.research_input_store import ResearchInputStore
from mcp_server_phytomni.runtime.run_registry import RunRegistry, RunSpec
from mcp_server_phytomni.storage.research_objects import (
    ResearchObjectAuthority,
    ResearchObjectResolveRequest,
    ResearchObjectRevokeRequest,
    ResearchObjectSnapshot,
    ResearchObjectVerifyRequest,
)
from tests.support.research_fakes import research_callbacks_through

pytestmark = pytest.mark.agent


def _runtime_resolution() -> dict[str, Any]:
    """Return the durable resolution fields for runtime fixtures."""
    return {
        "original_query_digest": "q" * 64,
        "original_query_length": 5,
        "effective_query": "query",
        "source_map": {},
        "parsed_candidates": [],
        "managed_snapshot": [],
        "evidence_digest": "e" * 64,
        "work_digest": "w" * 64,
    }


def _runtime_store(tmp_path: Any, name: str) -> ResearchInputStore:
    """Create one durable parent used by the production-runtime test."""
    database = str(tmp_path / f"{name}.db")
    RunRegistry(database).create_run(
        RunSpec(
            run_id="run-runtime",
            user_id="owner",
            agent="research",
            origin="api",
        )
    )
    store = ResearchInputStore(database)
    assert store.persist_resolution("run-runtime", **_runtime_resolution())
    return store


def _runtime_prepared() -> PreparedResearchInput:
    """Build a private prepared input with one exact authority binding."""
    snapshot = ResearchObjectSnapshot(
        "dataset-001",
        17,
        "etag-001",
        "version-001",
        "2026-08-08T00:00:00+00:00",
        False,
        "snapshot-001",
    )
    authority = ResearchObjectAuthority("dataset-001", "grant-000", snapshot)
    return PreparedResearchInput(
        effective_query="query",
        obs_file_list=(),
        data_list=MappingProxyType({"obs://dev-bucket/data.tsv": "dataset"}),
        inventory_digest="i" * 64,
        evidence_digest="e" * 64,
        execution_fingerprint="x" * 64,
        authority_ids=(authority.authority_id,),
        authorities=(
            PreparedResearchAuthority(
                dataset_id="dataset-001",
                exact_reference="obs://dev-bucket/data.tsv",
                compound_suffix=".tsv",
                authority=authority,
            ),
        ),
    )


def _runtime_plan(fingerprint: str = "f" * 64) -> ResearchPlan:
    """Build one result-child plan for the real Analyst adapter seam."""
    child = ResearchChildPlan(
        ordinal=0,
        task_name="research_goal_0",
        goal_description="run the research goal",
        context="context",
        data_list=MappingProxyType({"obs://dev-bucket/data.tsv": "dataset"}),
        output_dir="research/run-runtime/children/part-001",
        thread_id="thread-runtime",
        interop_mode="off",
        interop_targets=(),
        dispatch_fingerprint=fingerprint,
    )
    return ResearchPlan(goals=(), children=(child,), digest="a" * 64)


class _RuntimeMetadataPort:
    """Concrete typed metadata port fake with rotating authority IDs."""

    def __init__(self) -> None:
        self.resolve_calls: list[ResearchObjectResolveRequest] = []
        self.verify_calls: list[ResearchObjectVerifyRequest] = []
        self.revoke_calls: list[ResearchObjectRevokeRequest] = []
        self.generation = 0

    async def resolve(
        self, request: ResearchObjectResolveRequest
    ) -> tuple[ResearchObjectAuthority, ...]:
        """Return the current authority generation for each candidate."""
        self.resolve_calls.append(request)
        self.generation += 1
        return tuple(
            ResearchObjectAuthority(
                candidate.dataset_id,
                f"grant-{self.generation:03d}",
                ResearchObjectSnapshot(
                    candidate.dataset_id,
                    17,
                    "etag-001",
                    "version-001",
                    "2026-08-08T00:00:00+00:00",
                    False,
                    "snapshot-001",
                ),
            )
            for candidate in request.objects
        )

    async def verify(
        self, request: ResearchObjectVerifyRequest
    ) -> tuple[ResearchObjectAuthority, ...]:
        """Preserve the verifier protocol for the typed fake port."""
        self.verify_calls.append(request)
        return tuple(
            ResearchObjectAuthority(
                authority.dataset_id,
                "grant-001",
                authority.snapshot,
            )
            for authority in request.authorities
        )

    async def revoke(self, request: Any) -> None:
        """Accept revocation calls without retaining private state."""
        self.revoke_calls.append(request)


class _RuntimeProvider:
    """Resolver provider that must never receive dispatch rows."""

    async def invoke(self, work_input: object, policy: object) -> object:
        """Reject accidental resolver use during dispatch tests."""
        del work_input, policy
        raise AssertionError("dispatch row entered resolver provider")

    async def query(self, request_identity: str) -> object | None:
        """Return no resolver task because dispatch owns this provider."""
        del request_identity
        return None


def _runtime_analyst(submitted: list[Any]) -> Any:
    """Build the real adapter shape while recording child submissions."""

    async def ainvoke(analyst_input: Any, *, config: Any) -> dict[str, Any]:
        """Record one adapter invocation and return a durable task id."""
        submitted.append((analyst_input, config))
        return {
            "task_id": "analyst-task-001",
            "output_dir": analyst_input["output_dir"],
        }

    return SimpleNamespace(app=SimpleNamespace(ainvoke=ainvoke))


def _runtime(
    store: ResearchInputStore,
    metadata: _RuntimeMetadataPort,
    submitted: list[Any],
    lease_owner: str,
) -> Any:
    """Construct the production dispatch/recovery graph for one test."""
    return dispatch_runtime.build_research_dispatch_runtime(
        store,
        _RuntimeProvider(),
        analyst_agent=_runtime_analyst(submitted),
        analyst_config=type(
            "Config",
            (),
            {"USER_ID": "owner", "COMPUTE_RESOURCE": "medium"},
        )(),
        sensitive_config=object(),
        metadata_port=metadata,
        lease_owner=lease_owner,
    )


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
    def planning_plan(self) -> Any:
        """Return the pure plan passed to planning persistence."""
        return self._state.get("planning_plan")

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
        self._state["planning_plan"] = values.get("plan")
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
    assert harness.calls == research_callbacks_through("validate_native")
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


async def test_coordinator_passes_pure_plan_to_persistence_seam() -> None:
    """Planning persistence receives the validated plan after native checks."""
    harness = _Harness()
    plan = object()
    coordinator = ResearchInputCoordinator(_request(harness), plan=plan)

    await coordinator.run("run-001", "lease-001")

    assert harness.planning_plan is plan
    assert harness.calls[-1] == "persist_planning"


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
async def test_restart_requires_persisted_evidence_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restart cannot reuse resolver work without the old evidence digest."""
    harness = _Harness(
        prepared_query="trusted query",
        values={"extracted_value": _evidence_metadata("content-1")},
    )

    async def loader(run_id: str) -> ResearchCoordinatorRequest:
        """Return a private request whose evidence was not persisted."""
        return ResearchCoordinatorRequest(
            run_id=run_id,
            inventory_request="persisted-inventory",
            evidence=None,
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
        """Keep the restart test independent of registered recovery state."""
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


@pytest.mark.asyncio
async def test_production_runtime_wires_analyst_and_rotation(
    tmp_path: Any,
) -> None:
    """The production seam submits, attaches, and deduplicates replay."""
    store = _runtime_store(tmp_path, "accepted")
    fingerprint = hashlib.sha256(str(tmp_path).encode()).hexdigest()
    metadata = _RuntimeMetadataPort()
    submitted: list[Any] = []

    runtime = _runtime(store, metadata, submitted, "runtime-worker")
    coordinator = ResearchInputCoordinator(dispatch_runtime=runtime)
    assert coordinator.outbox is runtime.outbox
    assert coordinator.recovery is runtime.recovery
    production_coordinator = ResearchInputCoordinator.from_production(
        store=store,
        provider=_RuntimeProvider(),
        analyst_agent=_runtime_analyst(submitted),
        analyst_config=type(
            "Config",
            (),
            {"USER_ID": "owner", "COMPUTE_RESOURCE": "medium"},
        )(),
        sensitive_config=object(),
        metadata_port=metadata,
        lease_owner="runtime-worker-2",
    )
    assert production_coordinator.outbox is not None
    assert production_coordinator.recovery is not None
    record = persist_plan_and_outbox(
        store,
        "run-runtime",
        0,
        _runtime_prepared(),
        _runtime_plan(fingerprint),
    )[0]
    accepted = await runtime.outbox.dispatch_once(record.dispatch_id, "worker")
    replay = await runtime.outbox.dispatch_once(record.dispatch_id, "worker-2")

    assert accepted.state == "accepted"
    assert replay.state == "accepted"
    assert len(submitted) == 1
    assert len(metadata.resolve_calls) == 1
    assert metadata.resolve_calls[0].parent_run_id == "run-runtime"
    assert metadata.resolve_calls[0].execution_fingerprint == fingerprint
    assert not metadata.verify_calls
    assert len(metadata.revoke_calls) == 1
    assert (
        submitted[0][0]["research_grant_sidecar"]["objects"][0]["grant_id"]
        == "grant-001"
    )
    with sqlite3.connect(store.db_path) as connection:
        task = connection.execute(
            "SELECT run_id, input_fingerprint, status FROM tasks "
            "WHERE task_id='analyst-task-001'"
        ).fetchone()
    assert task == ("run-runtime", fingerprint, "submitted")


@pytest.mark.asyncio
async def test_empty_authority_binding_is_a_metadata_noop(
    tmp_path: Any,
) -> None:
    """Query-only children must not send an invalid empty metadata request."""
    store = _runtime_store(tmp_path, "no-authority")
    metadata = _RuntimeMetadataPort()
    submitted: list[Any] = []
    runtime = _runtime(store, metadata, submitted, "runtime-worker")
    prepared = replace(_runtime_prepared(), authorities=(), authority_ids=())
    record = persist_plan_and_outbox(
        store, "run-runtime", 0, prepared, _runtime_plan()
    )[0]

    disposition = await runtime.outbox.dispatch_once(
        record.dispatch_id, "worker"
    )

    assert disposition.state == "accepted"
    assert not metadata.resolve_calls
    assert not metadata.verify_calls


@pytest.mark.asyncio
async def test_production_runtime_recovers_expired_remote_task(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restart recovery queries an expired sent row without resubmitting."""
    recovery_store = _runtime_store(tmp_path, "recovery")
    submitted: list[Any] = []
    recovery_runtime = _runtime(
        recovery_store,
        _RuntimeMetadataPort(),
        submitted,
        "recovery-worker",
    )
    recovery_record = persist_plan_and_outbox(
        recovery_store,
        "run-runtime",
        0,
        _runtime_prepared(),
        _runtime_plan(
            hashlib.sha256(f"{tmp_path}-recovery".encode()).hexdigest()
        ),
    )[0]
    expired = datetime.now(UTC) - timedelta(minutes=2)
    with sqlite3.connect(recovery_store.db_path) as connection:
        connection.execute(
            "UPDATE research_dispatch_outbox SET state='sent', "
            "lease_owner='dead-worker', remote_task_id='analyst-existing', "
            "lease_expires_at=?, revision=1 "
            "WHERE outbox_id=?",
            (expired.isoformat(), recovery_record.dispatch_id),
        )
        connection.commit()

    async def query(_task_id: str, **_kwargs: Any) -> dict[str, str]:
        """Report a still-running remote task during recovery."""
        return {"status": "RUNNING"}

    monkeypatch.setattr(dispatch_runtime, "task_status", query)
    summary = await recovery_runtime.recovery.recover_once(datetime.now(UTC))

    assert summary.reconciled == 1
    assert not submitted[1:]
    assert recovery_runtime.outbox.load(recovery_record.dispatch_id).state == (
        "accepted"
    )
