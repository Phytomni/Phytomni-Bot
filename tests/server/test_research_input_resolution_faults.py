# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Cross-cutting fault boundaries for durable Research admission."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import pytest
from tests.support.research_fakes import research_callbacks_through
from tests.support.sqlite import closed_sqlite_connection

from mcp_server_phytomni.agents.research.input_contracts import (
    ResearchCoordinatorDependencies,
    ResearchCoordinatorRequest,
    ResearchInputFailure,
    research_input_failure,
)
from mcp_server_phytomni.agents.research.input_coordinator import (
    ResearchInputCoordinator,
)
from mcp_server_phytomni.agents.research.input_inventory import (
    ManagedResearchAssetSnapshot,
)
from mcp_server_phytomni.agents.research.input_preparation import (
    PreparedResearchInput,
)
from mcp_server_phytomni.api.agent_capabilities import (
    build_research_input_descriptor,
)
from mcp_server_phytomni.api.attachments import (
    AttachmentContractError,
    validate_research_attachment_bundle,
)
from mcp_server_phytomni.api.lifecycle_contract import SafeApiError
from mcp_server_phytomni.api.research_fingerprint import (
    research_client_fingerprint_for_http_input,
)
from mcp_server_phytomni.api.research_input import (
    ResearchAdmissionRequest,
    ResearchClientFingerprintInput,
    ResearchHttpAdmissionInput,
    ResearchInputStore,
    ResearchRoutePreflight,
    admit_research_request,
    compute_research_client_fingerprint,
    parse_idempotency_identity,
)
from mcp_server_phytomni.api.routes.attachment_inputs import (
    ResolvedAttachmentInput,
    prepare_selected_expert_arguments,
)
from mcp_server_phytomni.api.schemas import (
    AgentRunRequest,
    AttachmentAsset,
    ChatCompletionRequest,
    ChatMessage,
    ExpertQueryRequest,
)
from mcp_server_phytomni.config.api_limits import ApiLimitsConfig
from mcp_server_phytomni.runtime.attachment_assets import (
    ResolvedAsset,
    ResolvedAttachmentBundle,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry

pytestmark = pytest.mark.server


def test_fingerprint_http_input_helper_matches_typed_contract() -> None:
    """The HTTP-input helper preserves the versioned fingerprint shape."""
    query = "summarize rice drought"
    identity = parse_idempotency_identity("fingerprint-values", None)
    expected = compute_research_client_fingerprint(
        ResearchClientFingerprintInput(
            original_query_digest=hashlib.sha256(
                query.encode("utf-8")
            ).hexdigest(),
            original_query_length=len(query),
            managed_asset_ids=("asset-1",),
            locale="en-US",
            interop_mode="off",
            interop_targets=(),
            conversation_identity_digest=identity.canonical_digest,
        )
    )

    assert (
        research_client_fingerprint_for_http_input(
            ResearchHttpAdmissionInput(
                original_query=query,
                original_query_digest=hashlib.sha256(
                    query.encode("utf-8")
                ).hexdigest(),
                original_query_length=len(query),
                managed_asset_ids=("asset-1",),
                locale="en-US",
                interop_mode="off",
                interop_targets=(),
            ),
            identity.canonical_digest,
        )
        == expected
    )


@dataclass(frozen=True, slots=True)
class _FaultCase:
    """One synchronous admission rejection with no durable run."""

    key: str | None
    query: str
    code: str
    status: int


@dataclass(frozen=True, slots=True)
class _PostAcceptanceFault:
    """One classified failure raised by the durable coordinator root."""

    code: str
    status: int
    retryable: bool
    callback: str
    last_stage: str


_SYNC_NO_RUN_CASES = (
    _FaultCase(
        None,
        "summarize rice drought",
        "research_idempotency_key_required",
        400,
    ),
    _FaultCase(
        "", "summarize rice drought", "research_idempotency_key_required", 400
    ),
    _FaultCase("ok-key", "x" * 131_073, "research_input_limit_exceeded", 413),
)

_POST_ACCEPTANCE_FAULTS = (
    _PostAcceptanceFault(
        "research_dataset_not_found",
        422,
        False,
        "metadata",
        "metadata",
    ),
    _PostAcceptanceFault(
        "research_document_extraction_failed",
        503,
        True,
        "extract",
        "extraction",
    ),
    _PostAcceptanceFault(
        "research_input_resolution_unavailable",
        503,
        True,
        "resolve",
        "resolver",
    ),
    _PostAcceptanceFault(
        "research_input_resolution_failed",
        422,
        False,
        "revalidate",
        "revalidation",
    ),
    _PostAcceptanceFault(
        "research_input_resolution_failed",
        422,
        False,
        "validate_native",
        "validate_native",
    ),
)


def _store(tmp_path: Path) -> ResearchInputStore:
    """Create an isolated registry before its Research sidecar tables."""
    database = str(tmp_path / "faults.sqlite")
    RunRegistry(database)
    return ResearchInputStore(database)


def _request(case: _FaultCase) -> ResearchHttpAdmissionInput:
    """Build one caller-owned native request with no managed inputs."""
    return ResearchHttpAdmissionInput(
        owner="fault-owner",
        idempotency_key=case.key,
        conversation=None,
        original_query=case.query,
        managed_asset_ids=(),
        locale="en-US",
        interop_mode="off",
        interop_targets=(),
        route_source="native",
    )


def _managed_snapshot(asset_id: str) -> ManagedResearchAssetSnapshot:
    """Return one valid opaque managed dataset for count-boundary tests."""
    values = {
        "size_bytes": 1,
        "purpose": "dataset",
        "completed": True,
        "state_version": 1,
        "completed_at": "2026-08-09T00:00:00+00:00",
        "etag": None,
        "version_id": None,
        "last_modified": None,
    }
    return ManagedResearchAssetSnapshot(
        asset_id=asset_id,
        exact_reference=f"opaque-{asset_id}",
        snapshot_digest=f"snapshot-{asset_id}",
        **cast(Any, values),
    )


def _count_request(
    *, managed_count: int, pasted_count: int, key: str
) -> ResearchHttpAdmissionInput:
    """Build a strict-grammar request with an exact count in each lane."""
    pasted = "\n".join(
        f"obs://phytomni/research/{index:03d}.tsv\tdataset-{index}"
        for index in range(pasted_count)
    )
    query = "summarize the inputs" if not pasted else f"summarize\n{pasted}"
    return ResearchHttpAdmissionInput(
        owner="fault-owner",
        idempotency_key=key,
        conversation=None,
        original_query=query,
        managed_asset_ids=tuple(
            f"asset-{index:03d}" for index in range(managed_count)
        ),
        locale="en-US",
        interop_mode="off",
        interop_targets=(),
        route_source="native",
    )


def _resolved_dataset_bundle(count: int) -> ResolvedAttachmentBundle:
    """Build completed managed datasets for count-boundary validation."""
    return ResolvedAttachmentBundle(
        assets=tuple(
            ResolvedAsset(
                asset_id=f"asset-{index:03d}",
                reference=f"obs://phytomni/research/{index:03d}.tsv",
                filename=f"{index:03d}.tsv",
                content_type="text/tab-separated-values",
                size_bytes=1,
                purpose="dataset",
                state_version=1,
                completed_at="2026-08-09T00:00:00+00:00",
            )
            for index in range(count)
        )
    )


@pytest.mark.parametrize("case", _SYNC_NO_RUN_CASES)
async def test_sync_rejections_create_no_research_run(
    tmp_path: Path,
    case: _FaultCase,
) -> None:
    """Invalid admission stays synchronous and persists no root work."""
    store = _store(tmp_path)
    preflight = ResearchRoutePreflight(store=store)

    with pytest.raises(ResearchInputFailure) as caught:
        await preflight.admit(_request(case))

    assert caught.value.code == case.code
    assert caught.value.http_status_hint == case.status
    with closed_sqlite_connection(store.db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM runs WHERE agent = 'research'"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM research_work_units"
        ).fetchone() == (0,)


def test_research_limit_schema_accepts_256_but_not_257() -> None:
    """Operator configuration cannot raise any Research input lane past 256."""
    configured = ApiLimitsConfig(
        API_MAX_ATTACHMENTS_PER_REQUEST=256,
        API_MAX_RESEARCH_DATASET_PATHS=256,
        API_MAX_RESEARCH_INPUT_REFERENCES=256,
    )

    assert configured.API_MAX_ATTACHMENTS_PER_REQUEST == 256
    assert configured.API_MAX_RESEARCH_DATASET_PATHS == 256
    assert configured.API_MAX_RESEARCH_INPUT_REFERENCES == 256
    with pytest.raises(ValueError):
        ApiLimitsConfig(API_MAX_ATTACHMENTS_PER_REQUEST=257)


@pytest.mark.parametrize(
    ("managed_count", "pasted_count", "config", "accepted"),
    (
        (11, 0, ApiLimitsConfig(), True),
        (64, 0, ApiLimitsConfig(), True),
        (65, 0, ApiLimitsConfig(), False),
        (64, 64, ApiLimitsConfig(), True),
        (64, 65, ApiLimitsConfig(), False),
        (
            128,
            128,
            ApiLimitsConfig(
                API_MAX_ATTACHMENTS_PER_REQUEST=128,
                API_MAX_RESEARCH_DATASET_PATHS=129,
                API_MAX_RESEARCH_INPUT_REFERENCES=256,
            ),
            True,
        ),
        (
            128,
            129,
            ApiLimitsConfig(
                API_MAX_ATTACHMENTS_PER_REQUEST=128,
                API_MAX_RESEARCH_DATASET_PATHS=129,
                API_MAX_RESEARCH_INPUT_REFERENCES=256,
            ),
            False,
        ),
    ),
    ids=("11", "64", "65", "128", "129", "256", "257"),
)
async def test_research_preflight_enforces_hidden_count_boundaries(
    tmp_path: Path,
    managed_count: int,
    pasted_count: int,
    config: ApiLimitsConfig,
    accepted: bool,
) -> None:
    """No native admission path retains the historical ten-item cap."""
    request = _count_request(
        managed_count=managed_count,
        pasted_count=pasted_count,
        key=f"count-boundary-{managed_count}-{pasted_count}",
    )

    def resolve(
        asset_ids: tuple[str, ...],
    ) -> tuple[ManagedResearchAssetSnapshot, ...]:
        return tuple(_managed_snapshot(asset_id) for asset_id in asset_ids)

    preflight = ResearchRoutePreflight(
        store=_store(tmp_path),
        config=config,
        managed_snapshot_resolver=resolve,
    )

    if accepted:
        outcome = await preflight.admit(request)
        assert outcome.status_code == 202
        assert outcome.worker_owner is True
        return
    with pytest.raises(ValueError) as caught:
        await preflight.admit(request)
    assert (
        getattr(caught.value, "code", None) == "research_input_limit_exceeded"
    )
    assert getattr(caught.value, "http_status_hint", None) == 413


@pytest.mark.parametrize("count", (11, 64, 65, 128, 129, 256, 257))
def test_public_request_schemas_never_restore_the_legacy_ten_asset_cap(
    count: int,
) -> None:
    """HTTP schemas preserve attachment identity for later Research limits."""
    attachments = [
        AttachmentAsset(asset_id=f"asset-{index}") for index in range(count)
    ]
    assert (
        len(AgentRunRequest(arguments={}, attachments=attachments).attachments)
        == count
    )
    assert (
        len(
            ExpertQueryRequest(
                user_query="research",
                allowed_tools=["InSilicoResearchAgent"],
                attachments=attachments,
            ).attachments
        )
        == count
    )
    assert (
        len(
            ChatCompletionRequest(
                model="phyto-chat",
                messages=[ChatMessage(role="user", content="research")],
                attachments=attachments,
            ).attachments
        )
        == count
    )


@pytest.mark.parametrize("count", (11, 64, 65, 128, 129, 256, 257))
def test_expert_attachment_preparation_uses_research_count_contract(
    tmp_path: Path,
    count: int,
) -> None:
    """Expert preparation must not apply the generic ten-file ceiling."""
    database = str(tmp_path / f"expert-{count}.sqlite")
    RunRegistry(database)
    bundle = _resolved_dataset_bundle(count)
    payload = ExpertQueryRequest(
        user_query="research",
        allowed_tools=["InSilicoResearchAgent"],
    )

    if count <= 64:
        arguments, _context = prepare_selected_expert_arguments(
            agent="research",
            selected_arguments={},
            payload=payload,
            resolved_input=ResolvedAttachmentInput("owner", bundle),
            db_path=database,
        )
        assert len(arguments["data_list"]) == count
        return

    with pytest.raises(SafeApiError) as caught:
        prepare_selected_expert_arguments(
            agent="research",
            selected_arguments={},
            payload=payload,
            resolved_input=ResolvedAttachmentInput("owner", bundle),
            db_path=database,
        )
    assert caught.value.code == "attachment_limit_exceeded"


@pytest.mark.parametrize("count", (11, 64, 65, 128, 129, 256, 257))
def test_research_bundle_and_descriptor_share_configured_limits(
    count: int,
) -> None:
    """Managed preparation and capability output use one Research contract."""
    config = ApiLimitsConfig(
        API_MAX_ATTACHMENTS_PER_REQUEST=256,
        API_MAX_RESEARCH_DATASET_PATHS=256,
        API_MAX_RESEARCH_INPUT_REFERENCES=256,
    )
    descriptor = build_research_input_descriptor(config)
    assert descriptor.max_attachments_per_request == 256
    assert descriptor.max_research_dataset_paths == 256
    assert descriptor.max_research_input_references == 256
    bundle = _resolved_dataset_bundle(count)
    if count <= 256:
        snapshots = validate_research_attachment_bundle(bundle, config)
        assert len(snapshots) == count
        return
    with pytest.raises(AttachmentContractError) as caught:
        validate_research_attachment_bundle(bundle, config)
    assert caught.value.code == "attachment_limit_exceeded"


async def test_durable_acceptance_replays_without_a_second_worker_claim(
    tmp_path: Path,
) -> None:
    """An accepted root stays one durable run and one owner on replay."""
    store = _store(tmp_path)
    launched: list[str] = []

    async def launch(_request: Any, outcome: Any) -> None:
        launched.append(outcome.run_id)

    preflight = ResearchRoutePreflight(store=store, worker_launcher=launch)
    case = _FaultCase("durable-key", "summarize rice drought", "", 0)

    first = await preflight.admit(_request(case))
    replay = await preflight.admit(_request(case))

    assert first.worker_owner is True
    assert replay.replay is True
    assert replay.worker_owner is False
    assert replay.run_id == first.run_id
    assert launched == [first.run_id]


def test_direct_admission_query_limit_is_a_no_run_413(tmp_path: Path) -> None:
    """The private admission seam agrees with HTTP on the query-limit hint."""
    query = "x" * 131_073
    identity = parse_idempotency_identity("direct-limit", None)
    fingerprint = compute_research_client_fingerprint(
        ResearchClientFingerprintInput(
            original_query_digest=hashlib.sha256(
                query.encode("utf-8")
            ).hexdigest(),
            original_query_length=len(query),
            managed_asset_ids=(),
            locale="en-US",
            interop_mode="off",
            interop_targets=(),
            conversation_identity_digest=None,
        )
    )
    request = ResearchAdmissionRequest(
        owner="fault-owner",
        identity=identity,
        client_fingerprint=fingerprint,
        original_query=query,
        managed_asset_ids=(),
        locale="en-US",
        interop_mode="off",
        interop_targets=(),
        route_source="native",
        parsed_input=cast(Any, None),
        managed_snapshot=(),
    )
    store = _store(tmp_path)

    with pytest.raises(ResearchInputFailure) as caught:
        admit_research_request(request, store)

    assert caught.value.code == "research_input_limit_exceeded"
    assert caught.value.http_status_hint == 413
    with closed_sqlite_connection(store.db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM runs WHERE agent = 'research'"
        ).fetchone() == (0,)


def _coordinator_failure_launcher(
    case: _PostAcceptanceFault, callbacks: list[str]
) -> Any:
    """Build a root launcher that reaches one real coordinator callback."""
    inventory = object()
    evidence = object()
    resolution = object()

    async def fail_or_return(name: str, value: Any) -> Any:
        callbacks.append(name)
        if name == case.callback:
            raise research_input_failure(
                cast(Any, case.code),
                "Private coordinator detail must never be projected.",
                http_status_hint=case.status,
                retryable=case.retryable,
                stage="input_resolution",
                last_stage=case.last_stage,
            )
        return value

    async def metadata(_request: Any) -> Any:
        return await fail_or_return("metadata", inventory)

    async def extract(_request: Any) -> Any:
        return await fail_or_return("extract", evidence)

    async def resolve(_request: Any) -> Any:
        return await fail_or_return("resolve", resolution)

    async def revalidate(_request: Any) -> Any:
        return await fail_or_return("revalidate", inventory)

    def validate_native(
        prepared: PreparedResearchInput,
    ) -> PreparedResearchInput:
        callbacks.append("validate_native")
        if case.callback == "validate_native":
            raise research_input_failure(
                cast(Any, case.code),
                "Private coordinator detail must never be projected.",
                http_status_hint=case.status,
                retryable=case.retryable,
                stage="input_resolution",
                last_stage=case.last_stage,
            )
        return prepared

    def join(_inventory: Any, _resolution: Any) -> PreparedResearchInput:
        return PreparedResearchInput(
            effective_query="",
            obs_file_list=(),
            data_list=MappingProxyType({}),
            inventory_digest="inventory",
            evidence_digest="evidence",
            execution_fingerprint="execution",
            authority_ids=(),
        )

    async def launch(_request: Any, outcome: Any, _admission: Any) -> bool:
        coordinator_request = ResearchCoordinatorRequest(
            run_id=outcome.run_id,
            inventory_request=inventory,
            evidence=evidence,
            resolution=resolution,
            dependencies=ResearchCoordinatorDependencies(
                build_inventory=metadata,
                extract_evidence=extract,
                resolve_descriptions=resolve,
                revalidate_inventory=revalidate,
                validate_native=validate_native,
                join_prepared=join,
            ),
        )
        await ResearchInputCoordinator(coordinator_request).run(
            outcome.run_id, "coordinator-fault"
        )
        return True

    return launch


def _failed_admission_rows(
    store: ResearchInputStore,
) -> tuple[tuple[Any, ...], tuple[Any, ...] | None]:
    """Load the safe parent and root states after an admitted failure."""
    with closed_sqlite_connection(store.db_path) as connection:
        row = connection.execute(
            "SELECT run_id, status, failure_json FROM runs "
            "WHERE agent = 'research'"
        ).fetchone()
        assert row is not None
        root = connection.execute(
            "SELECT state, failure_retryable FROM research_work_units "
            "WHERE run_id = ? AND kind = 'resolve_root'",
            (row[0],),
        ).fetchone()
    return row, root


@pytest.mark.parametrize("case", _POST_ACCEPTANCE_FAULTS)
async def test_post_acceptance_failures_preserve_safe_replay_contract(
    tmp_path: Path, case: _PostAcceptanceFault
) -> None:
    """Root failures settle durably before public errors reach callers."""
    store = _store(tmp_path)
    callbacks: list[str] = []

    preflight = ResearchRoutePreflight(
        store=store,
        runtime_ready=lambda: True,
        worker_launcher=_coordinator_failure_launcher(case, callbacks),
    )
    request = _request(
        _FaultCase("post-acceptance", "summarize rice drought", "", 0)
    )

    with pytest.raises(ResearchInputFailure) as caught:
        await preflight.admit(request)

    assert (
        caught.value.code,
        caught.value.http_status_hint,
        caught.value.retryable,
        caught.value.stage,
    ) == (case.code, case.status, case.retryable, "input_resolution")
    assert callbacks == research_callbacks_through(case.callback)
    row, root = _failed_admission_rows(store)
    failure = json.loads(row[2])
    assert failure == {
        "code": case.code,
        "http_status_hint": case.status,
        "retryable": case.retryable,
        "stage": "input_resolution",
    }
    assert root == (
        "retryable_failed" if case.retryable else "terminal_failed",
        int(case.retryable),
    )

    if not case.retryable:
        callback_count = len(callbacks)
        replay = await preflight.admit(request)
        assert replay.replay is True
        assert replay.status_code == 200
        assert len(callbacks) == callback_count
        return

    launched: list[str] = []

    async def recover(_request: Any, outcome: Any) -> bool:
        launched.append(outcome.run_id)
        return True

    retried = await ResearchRoutePreflight(
        store=store,
        runtime_ready=lambda: True,
        worker_launcher=recover,
    ).admit(request)
    assert retried.replay is False
    assert retried.worker_owner is True
    assert launched == [row[0]]
