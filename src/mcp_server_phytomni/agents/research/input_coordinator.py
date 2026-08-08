# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Ordered, dependency-injected Research input preparation coordinator."""

from __future__ import annotations

from typing import Any

from ...api.agent_capabilities import agent_supports_attachment_channels
from ...mcp.schemas import InSilicoResearchAgent
from .input_contracts import (
    ResearchCoordinatorDependencies,
    ResearchCoordinatorRequest,
    ResearchErrorCode,
    ResearchFailureStage,
    ResearchInputFailure,
    research_input_failure,
)
from .input_preparation import (
    PreparedResearchInput,
    join_prepared_research_input,
    with_execution_fingerprint,
)

__all__ = ["ResearchInputCoordinator"]

_FAILURE_MESSAGE = "Research input resolution failed."
_UNAVAILABLE_MESSAGE = "Research input resolution is unavailable."


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
        self.dependencies = dependencies or (
            request.dependencies
            if isinstance(request, ResearchCoordinatorRequest)
            else _dependencies_from_ports(ports)
        )

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
        dependencies = self.dependencies
        context = request
        inventory = await self._metadata(dependencies, context)
        context = context._replace(inventory_request=inventory)
        evidence = await self._extract(dependencies, context)
        context = context._replace(evidence=evidence)
        resolution = await self._resolve(dependencies, context)
        context = context._replace(resolution=resolution)
        prepared = self._join(dependencies, inventory, resolution)
        prepared = _bind_request_identity(prepared, context)
        refreshed = await self._revalidate(dependencies, context)
        if refreshed != inventory:
            raise _failure("input_resolution")
        prepared = await self._validate_native(dependencies, prepared, context)
        await self._persist(dependencies, run_id, prepared, context)

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
            raise _stage_failure("input_resolution", error) from None

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
            raise _stage_failure("input_resolution", error) from None

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
            raise _stage_failure("input_resolution", error) from None

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
        except Exception as error:
            raise _stage_failure("input_resolution", error) from None
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
            raise _stage_failure("input_resolution", error) from None

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
        except Exception:
            raise _failure("input_resolution") from None
        del validated
        del request
        return prepared

    async def _persist(
        self,
        dependencies: ResearchCoordinatorDependencies,
        run_id: str,
        prepared: PreparedResearchInput,
        request: ResearchCoordinatorRequest,
    ) -> None:
        callback = dependencies.persist_planning
        if callback is None:
            raise _failure("planning")
        try:
            result = callback(
                run_id,
                prepared=prepared,
                inventory=request.inventory_request,
                evidence=_persistence_metadata(request.evidence),
                resolution=_persistence_metadata(request.resolution),
                outbox_rows=(),
            )
            if hasattr(result, "__await__"):
                await result
        except ResearchInputFailure as error:
            raise _restate(error, "planning") from None
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


def _failure(stage: ResearchFailureStage) -> ResearchInputFailure:
    return research_input_failure(
        "research_input_resolution_failed",
        _FAILURE_MESSAGE,
        http_status_hint=422,
        retryable=False,
        stage=stage,
    )


def _stage_failure(
    stage: ResearchFailureStage, error: Exception
) -> ResearchInputFailure:
    del error
    return research_input_failure(
        "research_input_resolution_unavailable",
        _UNAVAILABLE_MESSAGE,
        http_status_hint=503,
        retryable=True,
        stage=stage,
    )


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
    )


_restage = _restate
