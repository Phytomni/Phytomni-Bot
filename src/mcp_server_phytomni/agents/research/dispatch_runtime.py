# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Production construction seam for Research child dispatch and recovery."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, cast

from ...agents.analyst.defaults import ANALYST_CONFIG
from ...agents.analyst.task_ops import task_status
from ...agents.shared.remote_analysis import (
    RemoteAnalysisPrompt,
    RemoteAnalysisRequest,
    ResearchGrantUse,
    submit_remote_analysis,
)
from ...common.relay_client import current_relay_client
from ...config.defaults import ServerConfig
from ...config.relay_mode import relay_mode_enabled
from ...runtime.outbound import current_outbound_runtime
from ...runtime.research_input_store import ResearchInputStore
from ...storage.research_objects import (
    DirectResearchObjectMetadataPort,
    RelayResearchObjectMetadataPort,
    ResearchObjectAuthority,
    ResearchObjectCandidate,
    ResearchObjectMetadataError,
    ResearchObjectMetadataPort,
    ResearchObjectResolveRequest,
    ResearchObjectRevokeRequest,
    ResearchObjectSnapshot,
    ResearchObjectVerifyRequest,
    research_object_authority_scope,
    research_object_snapshot_payload,
)
from .dispatch_outbox import (
    ResearchDispatchOutbox,
    ResearchDispatchRecord,
)
from .recovery import ResearchRecoveryService, ResearchWorkProvider
from .recovery_support import ResearchGrantRevocation

__all__ = [
    "ResearchDispatchRuntime",
    "build_research_dispatch_runtime",
    "build_research_object_metadata_port",
]

Clock = Callable[[], datetime]
_QUERY_FAILURES = (Exception,)


@dataclass(frozen=True, slots=True)
class ResearchDispatchRuntime:
    """Real provider wiring shared by coordinator and restart recovery."""

    outbox: ResearchDispatchOutbox
    recovery: ResearchRecoveryService
    metadata_port: ResearchObjectMetadataPort | None = None


@dataclass(frozen=True, slots=True)
class _RuntimeBindings:
    """Provider objects captured by the durable callback boundary."""

    analyst_agent: Any
    analyst_config: Any
    sensitive_config: Any
    metadata_port: ResearchObjectMetadataPort

    async def submit(self, row: ResearchDispatchRecord) -> object:
        """Submit one child through the real Analyst graph adapter."""
        payload = row.payload
        request = RemoteAnalysisRequest(
            analysis_type="research",
            target_id=str(payload.get("target_id") or row.dispatch_id),
            output_dir=row.output_dir,
            prompt=RemoteAnalysisPrompt(
                goal_description=str(payload.get("goal_description") or ""),
                meta=str(payload.get("context") or ""),
                data_list=_data_list(payload.get("data_list")),
            ),
            compute_resource=str(
                payload.get(
                    "compute_resource",
                    getattr(self.analyst_config, "COMPUTE_RESOURCE", "medium"),
                )
            ),
            dispatch_fingerprint=row.dispatch_fingerprint,
            research_grants=_grant_uses(payload.get("research_grants")),
            parent_run_id=row.run_id,
            obs_file_list=_obs_file_list(payload.get("obs_file_list")),
            output_dir_is_result_child=True,
        )
        return await submit_remote_analysis(
            self.analyst_agent,
            self.analyst_config,
            self.sensitive_config,
            request,
            is_polling=False,
        )

    async def query(self, row: ResearchDispatchRecord) -> object | None:
        """Query the original Analyst task identity.

        A recovery query must never submit a replacement task.
        """
        if not row.remote_task_id:
            return None
        config = self.analyst_config
        try:
            response = await task_status(
                row.remote_task_id,
                analysis_url=getattr(
                    config, "ANALYSIS_URL", ANALYST_CONFIG.ANALYSIS_URL
                ),
                region=getattr(
                    config, "ANALYSIS_REGION", ANALYST_CONFIG.ANALYSIS_REGION
                ),
                timeout=getattr(config, "TIMEOUT", ANALYST_CONFIG.TIMEOUT),
                retriable_codes=getattr(
                    config, "RETRIABLE_CODES", ANALYST_CONFIG.RETRIABLE_CODES
                ),
                max_retries=getattr(
                    config, "MAX_RETRIES", ANALYST_CONFIG.MAX_RETRIES
                ),
            )
        except _QUERY_FAILURES:
            return None
        if not isinstance(response, Mapping):
            return None
        status = str(response.get("status") or "").lower()
        if status in {"failed", "error", "cancelled", "failed_at_agent_level"}:
            return None
        return {"task_id": row.remote_task_id, **dict(response)}

    async def revoke(self, request: ResearchGrantRevocation) -> None:
        """Revoke persisted direct or relay authorities after cancellation."""
        await self.metadata_port.revoke(
            ResearchObjectRevokeRequest(
                parent_run_id=request.parent_run_id,
                execution_fingerprint=request.execution_fingerprint,
                authority_ids=request.grant_ids,
            )
        )

    async def verify(
        self, row: ResearchDispatchRecord
    ) -> ResearchDispatchRecord:
        """Verify persisted grants and rotate only private authority IDs."""
        raw_grants = row.payload.get("research_grants", ())
        if not raw_grants:
            return row
        candidates, expected = _grant_bindings(raw_grants)
        persisted = tuple(
            ResearchObjectAuthority(dataset_id, grant_id, snapshot)
            for dataset_id, _reference, _suffix, grant_id, snapshot in expected
        )
        if row.state in ("pending", "leased"):
            return await self._rebind_provisional(
                row,
                candidates,
                expected,
            )
        request = ResearchObjectVerifyRequest(
            row.run_id, row.dispatch_fingerprint, persisted
        )
        try:
            fresh = await self.metadata_port.verify(request)
        except ResearchObjectMetadataError:
            if not isinstance(
                self.metadata_port, DirectResearchObjectMetadataPort
            ):
                raise
            # Direct ports intentionally keep authority state in memory.  A
            # restart may lose that state, so exact references provide the
            # documented local-only recovery path; snapshots still gate the
            # resulting private-ID rotation below.
            fresh = await self.metadata_port.resolve(
                ResearchObjectResolveRequest(
                    row.run_id, row.dispatch_fingerprint, candidates
                )
            )
        rotated, grant_ids = _validated_rotation(expected, fresh)
        payload = dict(row.payload)
        payload["research_grants"] = rotated
        return replace(row, payload=payload, grant_ids=tuple(grant_ids))

    async def _rebind_provisional(
        self,
        row: ResearchDispatchRecord,
        candidates: tuple[ResearchObjectCandidate, ...],
        expected: tuple[
            tuple[str, str, str, str, ResearchObjectSnapshot], ...
        ],
    ) -> ResearchDispatchRecord:
        """Replace inventory-scoped grants with one child-scoped grant set."""
        fresh = await self.metadata_port.resolve(
            ResearchObjectResolveRequest(
                row.run_id,
                row.dispatch_fingerprint,
                candidates,
            )
        )
        try:
            rotated, grant_ids = _validated_rotation(expected, fresh)
        except ResearchObjectMetadataError:
            await _revoke_best_effort(
                self.metadata_port,
                row.run_id,
                row.dispatch_fingerprint,
                tuple(authority.authority_id for authority in fresh),
            )
            raise
        provisional_scope = research_object_authority_scope(candidates)
        try:
            await self.metadata_port.revoke(
                ResearchObjectRevokeRequest(
                    f"inventory-{provisional_scope}",
                    provisional_scope,
                    tuple(item[3] for item in expected),
                )
            )
        except ResearchObjectMetadataError:
            await _revoke_best_effort(
                self.metadata_port,
                row.run_id,
                row.dispatch_fingerprint,
                tuple(grant_ids),
            )
            raise
        payload = dict(row.payload)
        payload["research_grants"] = rotated
        return replace(row, payload=payload, grant_ids=tuple(grant_ids))


def build_research_dispatch_runtime(
    store: ResearchInputStore,
    provider: ResearchWorkProvider,
    **options: Any,
) -> ResearchDispatchRuntime:
    """Construct one real coordinator/outbox/recovery provider graph."""
    bindings = _RuntimeBindings(
        analyst_agent=options["analyst_agent"],
        analyst_config=options["analyst_config"],
        sensitive_config=options["sensitive_config"],
        metadata_port=(
            options.get("metadata_port")
            or build_research_object_metadata_port()
        ),
    )
    clock = options.get("now") or (lambda: datetime.now(UTC))
    outbox = ResearchDispatchOutbox(
        store,
        submit=bindings.submit,
        remote_query=bindings.query,
        authority_verifier=bindings.verify,
        now=clock,
    )
    recovery = ResearchRecoveryService(
        store,
        provider,
        outbox=outbox,
        now=clock,
        lease_owner=options.get("lease_owner"),
        grant_revoke=bindings.revoke,
    )
    return ResearchDispatchRuntime(
        outbox=outbox,
        recovery=recovery,
        metadata_port=bindings.metadata_port,
    )


def _rotate_grants(
    expected: Sequence[tuple[str, str, str, str, ResearchObjectSnapshot]],
    by_dataset: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Replace private grant ids after the immutable snapshots match."""
    rotated: list[dict[str, Any]] = []
    grant_ids: list[str] = []
    for dataset_id, reference, suffix, _grant_id, snapshot in expected:
        authority = by_dataset[dataset_id]
        if authority.snapshot != snapshot:
            raise ResearchObjectMetadataError()
        grant_ids.append(authority.authority_id)
        rotated.append(
            {
                "dataset_id": dataset_id,
                "exact_reference": reference,
                "compound_suffix": suffix,
                "grant_id": authority.authority_id,
                "snapshot_digest": snapshot.snapshot_digest,
                "snapshot": research_object_snapshot_payload(
                    authority.snapshot
                ),
            }
        )
    return rotated, grant_ids


def _validated_rotation(
    expected: Sequence[tuple[str, str, str, str, ResearchObjectSnapshot]],
    fresh: Sequence[ResearchObjectAuthority],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate one complete authority set before rotating private IDs."""
    if len(fresh) != len(expected):
        raise ResearchObjectMetadataError()
    by_dataset = {authority.dataset_id: authority for authority in fresh}
    if set(by_dataset) != {item[0] for item in expected}:
        raise ResearchObjectMetadataError()
    if len({authority.authority_id for authority in fresh}) != len(fresh):
        raise ResearchObjectMetadataError()
    return _rotate_grants(expected, by_dataset)


async def _revoke_best_effort(
    metadata_port: ResearchObjectMetadataPort,
    parent_run_id: str,
    execution_fingerprint: str,
    authority_ids: tuple[str, ...],
) -> None:
    """Try to release a newly minted grant set after a failed rebind."""
    if not authority_ids:
        return
    try:
        await metadata_port.revoke(
            ResearchObjectRevokeRequest(
                parent_run_id,
                execution_fingerprint,
                authority_ids,
            )
        )
    except ResearchObjectMetadataError:
        return


def build_research_object_metadata_port() -> ResearchObjectMetadataPort:
    """Build the operator direct or customer relay metadata port."""
    if relay_mode_enabled():
        return RelayResearchObjectMetadataPort(current_relay_client())
    config = ServerConfig()
    obs_runtime = current_outbound_runtime().obs
    if obs_runtime is None:
        raise RuntimeError("OBS runtime is unavailable")
    return DirectResearchObjectMetadataPort(
        config.BUCKET_NAME,
        obs_runtime,
    )


def _obs_file_list(value: object) -> tuple[str, ...]:
    """Validate persisted document references before Analyst submission."""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ResearchObjectMetadataError()
    documents = tuple(value)
    if any(
        not isinstance(item, str) or not item.strip() for item in documents
    ):
        raise ResearchObjectMetadataError()
    return documents


def _data_list(value: object) -> dict[str, Any]:
    """Validate the private child data-list projection."""
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return dict(value)
    return {}


def _grant_uses(value: object) -> tuple[ResearchGrantUse, ...]:
    """Convert persisted private grants into the relay-only sidecar type."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    result: list[ResearchGrantUse] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ResearchObjectMetadataError()
        result.append(
            ResearchGrantUse(
                dataset_id=cast(str, item["dataset_id"]),
                exact_reference=cast(str, item["exact_reference"]),
                grant_id=cast(str, item["grant_id"]),
                snapshot_digest=cast(str, item["snapshot_digest"]),
            )
        )
    return tuple(result)


def _grant_bindings(
    value: object,
) -> tuple[
    tuple[ResearchObjectCandidate, ...],
    tuple[tuple[str, str, str, str, ResearchObjectSnapshot], ...],
]:
    """Decode persisted candidate references and expected snapshots."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ResearchObjectMetadataError()
    candidates: list[ResearchObjectCandidate] = []
    expected: list[tuple[str, str, str, str, ResearchObjectSnapshot]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ResearchObjectMetadataError()
        dataset_id = item.get("dataset_id")
        reference = item.get("exact_reference")
        suffix = item.get("compound_suffix")
        grant_id = item.get("grant_id")
        snapshot = _snapshot_from_payload(item.get("snapshot"))
        if not all(
            isinstance(text, str) and (text or name == "compound_suffix")
            for name, text in (
                ("dataset_id", dataset_id),
                ("exact_reference", reference),
                ("compound_suffix", suffix),
                ("grant_id", grant_id),
            )
        ):
            raise ResearchObjectMetadataError()
        dataset_text = cast(str, dataset_id)
        reference_text = cast(str, reference)
        suffix_text = cast(str, suffix)
        grant_text = cast(str, grant_id)
        candidates.append(
            ResearchObjectCandidate(dataset_text, reference_text, suffix_text)
        )
        expected.append(
            (dataset_text, reference_text, suffix_text, grant_text, snapshot)
        )
    return tuple(candidates), tuple(expected)


def _snapshot_from_payload(value: object) -> ResearchObjectSnapshot:
    """Decode one immutable metadata snapshot without trusting its shape."""
    if not isinstance(value, Mapping):
        raise ResearchObjectMetadataError()
    try:
        snapshot = ResearchObjectSnapshot(
            dataset_id=cast(str, value["dataset_id"]),
            size_bytes=cast(int, value["size_bytes"]),
            etag=cast(str | None, value["etag"]),
            version_id=cast(str | None, value["version_id"]),
            last_modified=cast(str | None, value["last_modified"]),
            placeholder=cast(bool, value["placeholder"]),
            snapshot_digest=cast(str, value["snapshot_digest"]),
        )
    except (KeyError, TypeError, ValueError):
        raise ResearchObjectMetadataError() from None
    if not isinstance(snapshot.size_bytes, int) or isinstance(
        snapshot.size_bytes, bool
    ):
        raise ResearchObjectMetadataError()
    return snapshot
