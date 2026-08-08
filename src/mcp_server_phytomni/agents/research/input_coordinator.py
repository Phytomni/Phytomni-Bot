# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Ordered, dependency-injected Research input preparation coordinator."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, NamedTuple

from ...api.agent_capabilities import agent_supports_attachment_channels
from ...mcp.schemas import InSilicoResearchAgent
from .dispatch_outbox import ResearchDispatchOutbox, persist_plan_and_outbox
from .dispatch_runtime import build_research_dispatch_runtime
from .input_contracts import (
    ResearchCoordinatorDependencies,
    ResearchCoordinatorRequest,
    ResearchErrorCode,
    ResearchFailureStage,
    ResearchInputFailure,
    research_input_failure,
)
from .input_inventory import same_research_inventory_snapshot
from .input_preparation import (
    PreparedResearchInput,
    join_prepared_research_input,
    with_execution_fingerprint,
)
from .recovery import recover_registered_request

__all__ = ["ResearchInputCoordinator", "ResearchInputResumeLoader"]

ResearchInputResumeLoader = Callable[
    [str], Awaitable[ResearchCoordinatorRequest | None]
]

_FAILURE_MESSAGE = "Research input resolution failed."
_UNAVAILABLE_MESSAGE = "Research input resolution is unavailable."


class _DispatchBinding(NamedTuple):
    """Optional recovery service and automatic child-dispatch switch."""

    recovery: Any | None
    auto_dispatch: bool


class ResearchInputCoordinator:
    """Run the canonical metadata/evidence/resolution/validation sequence."""

    @property
    def contract_name(self) -> str:
        """Identify the ordered coordinator contract."""
        return "research_input_coordinator"

    def __init__(
        self,
        request: ResearchCoordinatorRequest | None = None,
        *,
        dependencies: ResearchCoordinatorDependencies | None = None,
        **ports: Any,
    ) -> None:
        """Bind immutable request data and injected side-effect ports.

        ``ports`` accepts the descriptive aliases used by transport adapters;
        keeping this seam permissive avoids coupling the coordinator to one
        storage or service implementation while the public routes converge.
        """
        self.request = request
        self.plan = ports.pop("plan", None)
        self.plan_builder = ports.pop("plan_builder", None)
        self.outbox = ports.pop("outbox", None)
        self._dispatch_binding = _DispatchBinding(None, False)
        runtime = ports.pop("dispatch_runtime", None)
        if runtime is not None:
            if self.outbox is not None:
                raise TypeError("dispatch_runtime cannot combine with outbox")
            self.outbox = getattr(runtime, "outbox", None)
            self._dispatch_binding = _DispatchBinding(
                getattr(runtime, "recovery", None), True
            )
            if self.outbox is None:
                raise TypeError("dispatch_runtime must provide an outbox")
            self._dispatch_binding = _DispatchBinding(
                self._dispatch_binding.recovery, True
            )
        self.expected_revision = ports.pop("expected_revision", 0)
        store = ports.pop("store", None)
        if self.outbox is None and store is not None:
            submit = ports.pop("dispatch_submit", None)
            if submit is None:
                submit = ports.pop("child_submit", None)
            if submit is None:
                submit = ports.pop("submit", None)
            if callable(submit):
                self.outbox = ResearchDispatchOutbox(
                    store,
                    submit=submit,
                    remote_query=ports.pop("remote_query", None),
                    local_lookup=ports.pop("local_lookup", None),
                    verify=ports.pop("verify", None),
                    authority_verifier=ports.pop("authority_verifier", None),
                    attach_task=ports.pop("attach_task", None),
                )
                self._dispatch_binding = _DispatchBinding(
                    self._dispatch_binding.recovery, True
                )
        self._validate_optional_ports()
        self.dependencies = dependencies or (
            request.dependencies
            if isinstance(request, ResearchCoordinatorRequest)
            else _dependencies_from_ports(ports)
        )

    @classmethod
    def from_production(
        cls,
        request: ResearchCoordinatorRequest | None = None,
        **ports: Any,
    ) -> ResearchInputCoordinator:
        """Construct the coordinator with real Analyst/recovery providers."""
        runtime = build_research_dispatch_runtime(
            ports.pop("store"),
            ports.pop("provider"),
            analyst_agent=ports.pop("analyst_agent"),
            analyst_config=ports.pop("analyst_config"),
            sensitive_config=ports.pop("sensitive_config"),
            metadata_port=ports.pop("metadata_port", None),
            now=ports.pop("now", None),
            lease_owner=ports.pop("lease_owner", None),
        )
        return cls(request, dispatch_runtime=runtime, **ports)

    @property
    def recovery(self) -> Any | None:
        """Expose the restart recovery service when production-wired."""
        return self._dispatch_binding.recovery

    @property
    def _auto_dispatch(self) -> bool:
        """Whether committed children dispatch immediately after planning."""
        return self._dispatch_binding.auto_dispatch

    def _validate_optional_ports(self) -> None:
        """Validate constructor-only planning/outbox controls."""
        if (
            not isinstance(self.expected_revision, int)
            or isinstance(self.expected_revision, bool)
            or self.expected_revision < 0
        ):
            raise ValueError("expected_revision must be non-negative")
        if self.plan_builder is not None and not callable(self.plan_builder):
            raise TypeError("plan_builder must be callable")

    async def run(self, run_id: str, lease_owner: str) -> None:
        """Prepare, validate, and durably hand off one Research input.

        The method intentionally stops at the ``planning`` transition.  Child
        submission is an outbox concern and cannot begin before this method's
        final native validation and persistence callback complete.
        """
        request = self.request
        if not isinstance(request, ResearchCoordinatorRequest):
            raise _failure("request_validation")
        if request.run_id != run_id or not lease_owner:
            raise _failure("request_validation")
        await self._run_request(request, run_id, lease_owner)

    async def resume_after_restart(
        self,
        run_id: str,
        lease_owner: str,
        loader: ResearchInputResumeLoader,
    ) -> None:
        """Reload only private metadata and rebuild all transient evidence."""
        if not run_id or not lease_owner or not callable(loader):
            raise _failure("request_validation", last_stage="restart")
        try:
            request = await loader(run_id)
        except ResearchInputFailure:
            raise
        except Exception as error:
            raise _stage_failure(
                "input_resolution", error, last_stage="restart"
            ) from None
        if not isinstance(request, ResearchCoordinatorRequest):
            raise _failure("request_validation", last_stage="restart")
        if request.run_id != run_id:
            raise _failure("request_validation", last_stage="restart")
        await self._run_request(request, run_id, lease_owner, resumed=True)

    async def _run_request(
        self,
        request: ResearchCoordinatorRequest,
        run_id: str,
        lease_owner: str,
        *,
        resumed: bool = False,
    ) -> None:
        """Run one fresh or restart-rebuilt preparation sequence."""
        await recover_registered_request()
        dependencies = self.dependencies
        if dependencies == ResearchCoordinatorDependencies():
            dependencies = request.dependencies
            self.dependencies = dependencies
        context = request
        inventory = await self._metadata(dependencies, context)
        context = context._replace(inventory_request=inventory)
        evidence = await self._extract(dependencies, context)
        if resumed:
            _compare_resumed_evidence(request.evidence, evidence)
        context = context._replace(evidence=evidence)
        resolution = await self._resolve(dependencies, context)
        context = context._replace(resolution=resolution)
        prepared = self._join(dependencies, inventory, resolution)
        if request.effective_query and (
            prepared.effective_query != request.effective_query
        ):
            raise _failure("input_resolution", last_stage="join")
        prepared = _bind_request_identity(prepared, context)
        refreshed = await self._revalidate(dependencies, context)
        if not same_research_inventory_snapshot(inventory, refreshed):
            raise _snapshot_drift()
        prepared = await self._validate_native(dependencies, prepared, context)
        await self._persist(run_id, prepared, context, lease_owner)

    async def _metadata(
        self,
        dependencies: ResearchCoordinatorDependencies,
        request: ResearchCoordinatorRequest,
    ) -> Any:
        callback = dependencies.build_inventory
        if callback is None:
            raise _failure("input_resolution")
        try:
            return await callback(request)
        except ResearchInputFailure as error:
            raise _restage(error, "input_resolution") from None
        except Exception as error:
            raise _metadata_failure(error) from None

    async def _extract(
        self,
        dependencies: ResearchCoordinatorDependencies,
        request: ResearchCoordinatorRequest,
    ) -> Any:
        callback = dependencies.extract_evidence
        if callback is None:
            raise _failure("input_resolution")
        try:
            return await callback(request)
        except ResearchInputFailure as error:
            raise _restage(error, "input_resolution") from None
        except Exception as error:
            raise _extraction_failure(error) from None

    async def _resolve(
        self,
        dependencies: ResearchCoordinatorDependencies,
        request: ResearchCoordinatorRequest,
    ) -> Any:
        callback = dependencies.resolve_descriptions
        if callback is None:
            raise _failure("input_resolution")
        try:
            return await callback(request)
        except ResearchInputFailure as error:
            raise _restage(error, "input_resolution") from None
        except Exception as error:
            raise _resolver_failure(error) from None

    def _join(
        self,
        dependencies: ResearchCoordinatorDependencies,
        inventory: Any,
        resolution: Any,
    ) -> PreparedResearchInput:
        callback = dependencies.join_prepared or join_prepared_research_input
        try:
            prepared = callback(inventory, resolution)
        except ResearchInputFailure:
            raise
        except Exception:
            raise _failure("input_resolution", last_stage="join") from None
        if not isinstance(prepared, PreparedResearchInput):
            raise _failure("input_resolution")
        return prepared

    async def _revalidate(
        self,
        dependencies: ResearchCoordinatorDependencies,
        request: ResearchCoordinatorRequest,
    ) -> Any:
        callback = dependencies.revalidate_inventory
        if callback is None:
            raise _failure("input_resolution")
        try:
            return await callback(request)
        except ResearchInputFailure as error:
            raise _restage(error, "input_resolution") from None
        except Exception as error:
            raise _snapshot_failure(error) from None

    async def _validate_native(
        self,
        dependencies: ResearchCoordinatorDependencies,
        prepared: PreparedResearchInput,
        request: ResearchCoordinatorRequest,
    ) -> PreparedResearchInput:
        callback = dependencies.validate_native or _validate_native_payload
        try:
            validated = callback(prepared)
        except ResearchInputFailure as error:
            raise _restage(error, "input_resolution") from None
        except Exception as error:
            raise _native_failure(error) from None
        del validated
        del request
        return prepared

    async def _persist(
        self,
        run_id: str,
        prepared: PreparedResearchInput,
        request: ResearchCoordinatorRequest,
        lease_owner: str,
    ) -> None:
        plan = await self._build_plan(prepared, request)
        dependencies = self.dependencies
        callback = dependencies.persist_planning
        try:
            if self.outbox is not None and plan is not None:
                records = persist_plan_and_outbox(
                    self.outbox.store,
                    run_id,
                    self.expected_revision,
                    prepared,
                    plan,
                )
                if callback is not None:
                    result = callback(
                        run_id,
                        prepared=prepared,
                        inventory=request.inventory_request,
                        evidence=_persistence_metadata(request.evidence),
                        resolution=_persistence_metadata(request.resolution),
                        plan=plan,
                        outbox_rows=records,
                    )
                    if inspect.isawaitable(result):
                        await result
                if self._auto_dispatch:
                    await self._dispatch_records(records, lease_owner)
            else:
                if callback is None:
                    raise _failure("planning")
                result = callback(
                    run_id,
                    prepared=prepared,
                    inventory=request.inventory_request,
                    evidence=_persistence_metadata(request.evidence),
                    resolution=_persistence_metadata(request.resolution),
                    plan=plan,
                    outbox_rows=(),
                )
                if inspect.isawaitable(result):
                    await result
        except ResearchInputFailure as error:
            raise _restate(error, "planning") from None
        except Exception as error:
            raise _stage_failure("planning", error) from None

    async def _dispatch_records(
        self, records: Sequence[Any], lease_owner: str
    ) -> None:
        """Submit committed children only after the enqueue transaction."""
        if self.outbox is None:
            return
        for record in records:
            outcome = await self.outbox.dispatch_once(
                record.dispatch_id, lease_owner
            )
            if outcome.state not in {"accepted", "reconciled"}:
                raise research_input_failure(
                    "research_run_tracking_failed",
                    "Research child tracking failed.",
                    http_status_hint=502,
                    retryable=False,
                    stage="planning",
                    last_stage="planning",
                )

    async def _build_plan(
        self,
        prepared: PreparedResearchInput,
        request: ResearchCoordinatorRequest,
    ) -> Any | None:
        """Build one injected pure plan after native validation."""
        if self.plan is not None:
            return self.plan
        if self.plan_builder is None:
            return None
        try:
            result = self.plan_builder(prepared, request)
            return await result if inspect.isawaitable(result) else result
        except ResearchInputFailure:
            raise
        except Exception as error:
            raise _stage_failure("planning", error) from None


def _dependencies_from_ports(
    ports: dict[str, Any],
) -> ResearchCoordinatorDependencies:
    """Build the typed dependency bundle from adapter-friendly aliases."""
    aliases = {
        "build_inventory": (
            "build_inventory",
            "inventory_builder",
            "metadata",
        ),
        "extract_evidence": (
            "extract_evidence",
            "evidence_extractor",
            "extract",
        ),
        "resolve_descriptions": (
            "resolve_descriptions",
            "description_resolver",
            "resolve",
        ),
        "revalidate_inventory": (
            "revalidate_inventory",
            "inventory_revalidator",
            "revalidate",
        ),
        "validate_native": ("validate_native", "native_validator"),
        "persist_planning": ("persist_planning", "persist"),
        "join_prepared": ("join_prepared",),
        "submit_children": ("submit_children", "submit"),
    }
    values: dict[str, Any] = {}
    for target, names in aliases.items():
        values[target] = next(
            (ports[name] for name in names if ports.get(name) is not None),
            None,
        )
    unknown = set(ports).difference(
        name for names in aliases.values() for name in names
    )
    if unknown:
        raise TypeError("unexpected Research coordinator ports")
    return ResearchCoordinatorDependencies(**values)


def _bind_request_identity(
    prepared: PreparedResearchInput,
    request: ResearchCoordinatorRequest,
) -> PreparedResearchInput:
    """Bind request identity without changing trusted references."""
    evidence_digest = request.evidence_digest or getattr(
        request.evidence, "coverage_digest", ""
    )
    work_digest = request.work_digest or getattr(
        request.resolution, "work_digest", ""
    )
    policy = request.policy_fingerprint or getattr(
        request.resolution, "policy_fingerprint", ""
    )
    query = request.effective_query or prepared.effective_query
    return with_execution_fingerprint(
        prepared,
        effective_query=query,
        evidence_digest=(
            evidence_digest if isinstance(evidence_digest, str) else ""
        ),
        work_digest=work_digest if isinstance(work_digest, str) else "",
        policy_fingerprint=policy if isinstance(policy, str) else "",
    )


def _compare_resumed_evidence(previous: Any, current: Any) -> None:
    """Compare restart metadata without reading persisted document text."""
    if previous is None:
        return
    before = _evidence_identity(previous)
    after = _evidence_identity(current)
    if before is None or after is None or before != after:
        raise _snapshot_drift()


def _evidence_identity(value: Any) -> tuple[Any, ...] | None:
    """Project only IDs/content/coverage digests from extracted evidence."""
    units = _field(value, "units")
    documents = _field(value, "document_digests")
    coverage = _field(value, "coverage_digest")
    if (
        not isinstance(units, Sequence)
        or isinstance(units, (str, bytes))
        or not isinstance(documents, Sequence)
        or isinstance(documents, (str, bytes))
        or not isinstance(coverage, str)
    ):
        return None
    unit_identity: list[tuple[Any, ...]] = []
    for unit in units:
        span = _field(unit, "source_span")
        span_identity = None
        if span is not None:
            span_identity = (
                _field(span, "start"),
                _field(span, "end"),
                _field(span, "grammar"),
            )
        dataset_ids = _field(unit, "dataset_ids")
        if not isinstance(dataset_ids, Sequence) or isinstance(
            dataset_ids, (str, bytes)
        ):
            return None
        unit_identity.append(
            (
                _field(unit, "evidence_id"),
                _field(unit, "source_kind"),
                _field(unit, "source_ordinal"),
                span_identity,
                _field(unit, "content_digest"),
                tuple(dataset_ids),
            )
        )
    document_identity: list[tuple[Any, ...]] = []
    for document in documents:
        evidence_ids = _field(document, "evidence_ids")
        if not isinstance(evidence_ids, Sequence) or isinstance(
            evidence_ids, (str, bytes)
        ):
            return None
        document_identity.append(
            (
                _field(document, "document_id"),
                _field(document, "content_digest"),
                tuple(evidence_ids),
            )
        )
    return coverage, tuple(unit_identity), tuple(document_identity)


def _field(value: Any, name: str, default: Any = None) -> Any:
    """Read one metadata field from a DTO or a private persisted mapping."""
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _persistence_metadata(value: Any) -> Any:
    """Project evidence/resolution objects before crossing storage boundary."""
    for method_name in ("to_persisted_metadata", "persistence_metadata"):
        method = getattr(value, method_name, None)
        if callable(method):
            return method()
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return value


def _validate_native_payload(prepared: PreparedResearchInput) -> None:
    """Apply the native Pydantic and capability boundary before dispatch."""
    if not prepared.effective_query.strip():
        raise ValueError("Research query is empty")
    payload = {
        "user_query": prepared.effective_query,
        "data_list": dict(prepared.data_list),
        "obs_file_list": list(prepared.obs_file_list),
    }
    InSilicoResearchAgent.model_validate(payload)
    channels = frozenset(
        channel
        for channel, values in (
            ("datasets", prepared.data_list),
            ("documents", prepared.obs_file_list),
        )
        if values
    )
    if not agent_supports_attachment_channels("research", channels):
        raise ValueError(
            "Research input capability does not support references"
        )


def _failure(
    stage: ResearchFailureStage, *, last_stage: str | None = None
) -> ResearchInputFailure:
    return research_input_failure(
        "research_input_resolution_failed",
        _FAILURE_MESSAGE,
        http_status_hint=422,
        retryable=False,
        stage=stage,
        last_stage=last_stage or stage,
    )


def _stage_failure(
    stage: ResearchFailureStage,
    error: Exception,
    *,
    retryable: bool = True,
    last_stage: str | None = None,
) -> ResearchInputFailure:
    del error
    return research_input_failure(
        "research_input_resolution_unavailable",
        _UNAVAILABLE_MESSAGE,
        http_status_hint=503,
        retryable=retryable,
        stage=stage,
        last_stage=last_stage or stage,
    )


def _metadata_failure(error: Exception) -> ResearchInputFailure:
    """Normalize metadata absence/placeholder versus infrastructure errors."""
    if isinstance(error, (FileNotFoundError, LookupError, ValueError)):
        return research_input_failure(
            "research_dataset_not_found",
            "Research dataset metadata could not be verified.",
            http_status_hint=422,
            retryable=False,
            last_stage="metadata",
        )
    return _stage_failure("input_resolution", error, last_stage="metadata")


def _extraction_failure(error: Exception) -> ResearchInputFailure:
    """Normalize user-invalid document input and extractor outages."""
    if isinstance(error, (ValueError, UnicodeError, LookupError)):
        return research_input_failure(
            "research_document_extraction_failed",
            "Research document extraction failed.",
            http_status_hint=422,
            retryable=False,
            last_stage="extraction",
        )
    return research_input_failure(
        "research_document_extraction_failed",
        "Research document extraction failed.",
        http_status_hint=503,
        retryable=True,
        last_stage="extraction",
    )


def _resolver_failure(error: Exception) -> ResearchInputFailure:
    """Normalize provider ambiguity, timeout, and invalid model output."""
    if isinstance(error, (TimeoutError, RuntimeError)):
        return _stage_failure(
            "input_resolution", error, retryable=False, last_stage="resolver"
        )
    if isinstance(error, ValueError):
        return _failure("input_resolution", last_stage="resolver")
    return _stage_failure(
        "input_resolution", error, retryable=False, last_stage="resolver"
    )


def _snapshot_failure(error: Exception) -> ResearchInputFailure:
    """Normalize snapshot drift while retaining safe infrastructure retry."""
    if isinstance(error, ValueError):
        return _snapshot_drift()
    return _stage_failure("input_resolution", error, last_stage="revalidation")


def _snapshot_drift() -> ResearchInputFailure:
    return research_input_failure(
        "research_input_resolution_failed",
        "Research input metadata changed before execution.",
        http_status_hint=422,
        retryable=False,
        last_stage="revalidation",
    )


def _native_failure(error: Exception) -> ResearchInputFailure:
    """Convert native Pydantic/capability details to a safe terminal error."""
    del error
    return _failure("input_resolution", last_stage="native_validation")


def _restate(
    error: ResearchInputFailure, stage: ResearchFailureStage
) -> ResearchInputFailure:
    code: ResearchErrorCode = getattr(
        error, "code", "research_input_resolution_failed"
    )
    return research_input_failure(
        code,
        error.safe_message,
        http_status_hint=error.http_status_hint,
        retryable=error.retryable,
        stage=stage,
        last_stage=getattr(error, "last_stage", stage),
    )


_restage = _restate
