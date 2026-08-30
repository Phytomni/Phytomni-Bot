# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Pure preflight and atomic admission for durable Research input work."""

# pylint: disable=too-many-lines

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal, cast

from ..agents.analyst.agent import AnalystAgent
from ..agents.research.dispatch_runtime import ResearchDispatchRuntime
from ..agents.research.input_contracts import (
    ParsedResearchInput,
    ResearchCoordinatorRequest,
    ResearchInputFailure,
    ResearchInteropMode,
    research_input_failure,
)
from ..agents.research.input_coordinator import ResearchInputCoordinator
from ..agents.research.input_inventory import (
    ManagedResearchAssetSnapshot,
)
from ..agents.research.input_parser import parse_research_input
from ..agents.research.recovery import ResearchPreAcceptanceRejected
from ..agents.research.recovery_support import register_recovery_service
from ..config.api_limits import ApiLimitsConfig
from ..config.defaults import ApiConfig, InSilicoResearchConfig
from ..config.settings import get_sensitive_config
from ..runtime.conversation_context.models import ConversationEnvelopeV1
from ..runtime.locale import SUPPORTED_LOCALES, SupportedLocale
from ..runtime.research_input_store import (
    ResearchAdmissionReservation,
    ResearchInputStore,
)
from ..runtime.task_reconcile import bind_research_relaunch_outbox
from ..storage.path_policy import IdFactory
from .research_launch import launch_worker
from .research_root import bind_default_research_root_request_factory

__all__ = [
    "ResearchAdmissionOutcome",
    "ResearchAdmissionRequest",
    "ResearchClientFingerprintInput",
    "ResearchHttpAdmissionInput",
    "ResearchInputStore",
    "ResearchRoutePreflight",
    "build_research_input_coordinator",
    "clear_research_input_runtime",
    "ensure_research_input_runtime",
    "launch_research_input_worker",
    "research_input_root_worker_ready",
    "ResearchRequestIdentity",
    "admit_research_request",
    "compute_research_client_fingerprint",
    "lookup_research_admission",
    "parse_idempotency_identity",
]

_IDENTITY_VERSION = b"research-idempotency/v1\x00"
_FINGERPRINT_VERSION = "research-client-fingerprint/v1"
_DIGEST_LENGTH = 64


@dataclass(frozen=True, slots=True)
class _ResearchInputRuntime:
    """Process-local production coordinator and its recovery service."""

    coordinator: Any
    recovery: Any
    root_worker: Callable[[str, ResearchAdmissionRequest], Any] | None = None
    root_request_factory: (
        Callable[[ResearchAdmissionRequest], ResearchCoordinatorRequest] | None
    ) = None


_RUNTIME_STATE: dict[str, _ResearchInputRuntime | None] = {"current": None}


def clear_research_input_runtime() -> None:
    """Drop the process-local Research coordinator (tests and shutdown)."""
    _RUNTIME_STATE["current"] = None


def research_input_root_worker_ready(
    *, allow_uninstalled: bool = False
) -> bool:
    """Return production readiness; direct adapters may opt in explicitly."""

    runtime = _RUNTIME_STATE["current"]
    if runtime is None:
        return allow_uninstalled
    return callable(runtime.root_worker) and callable(
        runtime.root_request_factory
    )


class _UnavailableResearchWorkProvider:
    """Safe resolver fallback; child dispatch uses the real Analyst port."""

    async def invoke(self, work_input: object, policy: object) -> object:
        """Never replace an unbound resolver request during recovery."""
        del work_input, policy
        raise ResearchPreAcceptanceRejected()

    async def query(self, request_identity: str) -> object | None:
        """Decline unsupported resolver lookup without making a new call."""
        del request_identity
        return None


def build_research_input_coordinator(
    request: Any | None = None,
    **ports: Any,
) -> Any:
    """Build and register the production Research admission worker.

    The API admission owner supplies the store, real Analyst configuration,
    metadata port, and resolver provider.  Keeping construction here gives
    HTTP admission and lifespan recovery the same coordinator instance while
    leaving MCP dispatch independent of this HTTP-only path.
    """
    root_worker = ports.pop("root_worker", None)
    root_request_factory = ports.pop("root_request_factory", None)
    coordinator = ResearchInputCoordinator.from_production(
        request,
        **ports,
    )
    bind_research_relaunch_outbox(coordinator.outbox)
    recovery = coordinator.recovery
    if recovery is None:
        raise RuntimeError("Research production runtime has no recovery")
    _RUNTIME_STATE["current"] = _ResearchInputRuntime(
        coordinator, recovery, root_worker, root_request_factory
    )
    register_recovery_service(recovery)
    return coordinator


async def _run_production_coordinator_root(
    run_id: str,
    admission: ResearchAdmissionRequest,
) -> bool:
    """Run the factory-built coordinator root after admission."""

    runtime = _RUNTIME_STATE["current"]
    if runtime is None:
        return False
    coordinator = runtime.coordinator
    factory = runtime.root_request_factory
    request_value = None
    if callable(factory):
        request_value = factory(admission)
        if inspect.isawaitable(request_value):
            request_value = await request_value
    if not isinstance(request_value, ResearchCoordinatorRequest):
        return False
    if request_value.run_id != run_id:
        request_value = request_value._replace(run_id=run_id)
    outbox = getattr(coordinator, "outbox", None)
    recovery = getattr(coordinator, "recovery", None)
    if outbox is None or recovery is None:
        return False
    root_coordinator = ResearchInputCoordinator(
        request_value,
        dispatch_runtime=ResearchDispatchRuntime(outbox, recovery),
    )
    runner = getattr(root_coordinator, "run", None)
    if not callable(runner):
        return False
    result = runner(
        run_id, f"research-http-{IdFactory().new_id('research-worker')}"
    )
    if inspect.isawaitable(result):
        await result
    return True


def ensure_research_input_runtime(
    db_path: str | None = None,
    *,
    root_request_factory: (
        Callable[[ResearchAdmissionRequest], ResearchCoordinatorRequest] | None
    ) = None,
    asset_resolver_factory: Callable[[], Any] | None = None,
) -> Any:
    """Construct the HTTP Research worker before lifespan recovery."""
    runtime = _RUNTIME_STATE["current"]
    if runtime is not None:
        return runtime.coordinator
    sensitive_config = get_sensitive_config()
    research_config = InSilicoResearchConfig()
    analyst_agent = AnalystAgent(
        analyst_config=research_config,
        sensitive_config=sensitive_config,
    )
    coordinator = build_research_input_coordinator(
        store=ResearchInputStore(db_path or ApiConfig().API_TASKS_DB_PATH),
        provider=_UnavailableResearchWorkProvider(),
        analyst_agent=analyst_agent,
        analyst_config=research_config,
        sensitive_config=sensitive_config,
        root_worker=_run_production_coordinator_root,
        root_request_factory=root_request_factory,
    )
    if root_request_factory is None:
        _RUNTIME_STATE["current"] = bind_default_research_root_request_factory(
            coordinator,
            _RUNTIME_STATE["current"],
            asset_resolver_factory=asset_resolver_factory,
        )
    return coordinator


async def launch_research_input_worker(
    request: Any,
    outcome: ResearchAdmissionOutcome,
    admission: ResearchAdmissionRequest | None = None,
) -> bool:
    """Launch the factory-bound root only for the durable owner."""

    del request
    if not outcome.worker_owner:
        return True
    runtime = _RUNTIME_STATE["current"]
    if runtime is None or admission is None or runtime.root_worker is None:
        return False
    launched = runtime.root_worker(outcome.run_id, admission)
    if inspect.isawaitable(launched):
        launched = await launched
    return launched is True


@dataclass(frozen=True, slots=True)
class ResearchRequestIdentity:
    """Digest-only identity for a header or authoritative conversation turn."""

    kind: Literal["header", "conversation"]
    canonical_digest: str
    header_alias_digest: str | None


@dataclass(frozen=True, slots=True)
class _ResearchFingerprintQuery:
    """Query and managed-asset fields in the client fingerprint."""

    original_query_digest: str
    original_query_length: int
    managed_asset_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ResearchFingerprintOptions(_ResearchFingerprintQuery):
    """Locale, interop, and conversation fields in the fingerprint."""

    locale: SupportedLocale
    interop_mode: ResearchInteropMode
    interop_targets: tuple[str, ...]
    conversation_identity_digest: str | None


@dataclass(frozen=True, slots=True)
class ResearchClientFingerprintInput(_ResearchFingerprintOptions):
    """The versioned caller-owned semantics bound to one idempotency key."""

    protocol_version: Literal[1] = 1


@dataclass(frozen=True, slots=True)
class _ResearchAdmissionIdentity:
    """Identity fields shared by an admitted Research request."""

    owner: str
    identity: ResearchRequestIdentity
    client_fingerprint: str


@dataclass(frozen=True, slots=True)
class _ResearchAdmissionSemantics(_ResearchAdmissionIdentity):
    """Caller-owned semantic fields shared by an admitted request."""

    original_query: str
    managed_asset_ids: tuple[str, ...]
    locale: SupportedLocale
    interop_mode: ResearchInteropMode
    interop_targets: tuple[str, ...]
    route_source: str


@dataclass(frozen=True, slots=True)
class ResearchAdmissionRequest(_ResearchAdmissionSemantics):
    """Validated request inputs that may be atomically reserved."""

    parsed_input: ParsedResearchInput
    managed_snapshot: tuple[ManagedResearchAssetSnapshot, ...]


@dataclass(frozen=True, slots=True)
class ResearchAdmissionOutcome:
    """Public admission result after the durable commit succeeds."""

    run_id: str
    replay: bool
    worker_owner: bool
    status_code: Literal[200, 202]


class ResearchHttpAdmissionInput:
    """Opaque HTTP-owned input passed to Research admission.

    Values stay in one immutable-shaped bag so the flat keyword contract is
    preserved without duplicating projected legacy input fields.
    """

    __slots__ = ("_values",)

    def __init__(self, **values: Any) -> None:
        object.__setattr__(self, "_values", dict(values))

    def __getattr__(self, name: str) -> Any:
        try:
            return self._values[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def as_dict(self) -> dict[str, Any]:
        """Return a detached mapping for diagnostics and tests."""
        return dict(self._values)

    def get(self, name: str, default: Any = None) -> Any:
        """Read one optional value without raising for malformed input."""
        return self._values.get(name, default)


class _ResearchPreflightContext:
    """Internal bag for the adapter's injected seams."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, Any]) -> None:
        self._values = dict(values)

    def __getattr__(self, name: str) -> Any:
        try:
            return self._values[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def get(self, name: str, default: Any = None) -> Any:
        """Read one optional seam without raising for a missing value."""
        return self._values.get(name, default)


type ResearchInputParser = Callable[[str, str], ParsedResearchInput]


type ManagedSnapshotResolver = Callable[
    [tuple[str, ...]], tuple[ManagedResearchAssetSnapshot, ...]
]
type ResearchWorkerLauncher = Callable[..., Any | Awaitable[Any]]
type ResearchInventoryValidator = Callable[
    [ParsedResearchInput, tuple[ManagedResearchAssetSnapshot, ...]],
    Any | Awaitable[Any],
]


class ResearchRoutePreflight:
    """Replay-first HTTP adapter for the durable Research coordinator."""

    def __init__(self, **options: Any) -> None:
        """Bind the durable store and injectable pure/external seams."""
        self._context = _ResearchPreflightContext(
            {
                "store": options.pop("store"),
                "bucket": options.pop("bucket", "phytomni"),
                "config": options.pop("config", None) or ApiLimitsConfig(),
                "parser": options.pop("parser", parse_research_input),
                "managed_snapshot_resolver": options.pop(
                    "managed_snapshot_resolver", None
                ),
                "inventory_validator": options.pop(
                    "inventory_validator", None
                ),
                "worker_launcher": options.pop("worker_launcher", None),
                "runtime_ready": options.pop("runtime_ready", None),
            }
        )
        if options:
            raise TypeError(
                "unexpected Research preflight options: "
                + ", ".join(sorted(options))
            )

    async def admit(
        self, request: ResearchHttpAdmissionInput
    ) -> ResearchAdmissionOutcome:
        """Replay first, then atomically admit fresh work."""
        self._validate_http_request(request)
        context = self._context
        identity = parse_idempotency_identity(
            request.idempotency_key, request.conversation
        )
        query_digest = hashlib.sha256(
            request.original_query.encode("utf-8")
        ).hexdigest()
        query_length = len(request.original_query)
        locale = cast(SupportedLocale, request.locale or "en-US")
        fingerprint = compute_research_client_fingerprint(
            ResearchClientFingerprintInput(
                original_query_digest=query_digest,
                original_query_length=query_length,
                managed_asset_ids=request.managed_asset_ids,
                locale=locale,
                interop_mode=request.interop_mode,
                interop_targets=request.interop_targets,
                conversation_identity_digest=(
                    identity.canonical_digest
                    if identity.kind == "conversation"
                    else None
                ),
            )
        )
        replay = lookup_research_admission(
            owner=request.owner,
            identity=identity,
            client_fingerprint=fingerprint,
            store=context.store,
        )
        retryable = (
            replay is not None
            and context.store.retry_admission_available(
                replay.run_id,
                request.owner,
                identity.canonical_digest,
                fingerprint,
            )
        )
        if replay is not None and not retryable:
            return replay
        self._ensure_runtime_ready()
        if query_length > context.config.API_MAX_USER_QUERY_CHARS:
            raise research_input_failure(
                "research_input_limit_exceeded",
                "Research query exceeds the allowed limit.",
                http_status_hint=413,
            )
        parsed = self._parse(request.original_query)
        managed_snapshot = self._resolve_managed_snapshot(
            request.managed_asset_ids
        )
        self._validate_input_counts(parsed, managed_snapshot)
        await self._validate_inventory(parsed, managed_snapshot)
        retry = None
        if retryable:
            assert replay is not None
            retry = context.store.retry_admission(
                replay.run_id,
                request.owner,
                identity.canonical_digest,
                fingerprint,
            )
            if retry is None:
                return (
                    lookup_research_admission(
                        owner=request.owner,
                        identity=identity,
                        client_fingerprint=fingerprint,
                        store=context.store,
                    )
                    or replay
                )
        admission_request = ResearchAdmissionRequest(
            owner=request.owner,
            identity=identity,
            client_fingerprint=fingerprint,
            original_query=request.original_query,
            managed_asset_ids=request.managed_asset_ids,
            locale=locale,
            interop_mode=request.interop_mode,
            interop_targets=request.interop_targets,
            route_source=request.route_source,
            parsed_input=parsed,
            managed_snapshot=managed_snapshot,
        )
        admitted = (
            _admission_outcome(retry)
            if retry is not None
            else admit_research_request(admission_request, context.store)
        )
        await launch_worker(
            context.worker_launcher,
            request,
            admitted,
            admission_request,
            context.store,
        )
        return admitted

    async def preflight(
        self, request: ResearchHttpAdmissionInput
    ) -> ResearchAdmissionOutcome:
        """Preserve the explicit replay-first preflight entry point."""
        return await self.admit(request)

    def _ensure_runtime_ready(self) -> None:
        runtime_ready = self._context.runtime_ready
        if runtime_ready is not None and not runtime_ready():
            raise research_input_failure(
                "research_input_protocol_unavailable",
                "Research input resolution is unavailable.",
                http_status_hint=503,
                retryable=True,
            )

    def _parse(self, query: str) -> ParsedResearchInput:
        """Parse query text while projecting only stable domain failures."""
        try:
            return self._context.parser(query, self._context.bucket)
        except ResearchInputFailure:
            raise
        except (TypeError, ValueError) as exc:
            raise research_input_failure(
                "research_input_resolution_failed",
                "Research input could not be parsed.",
            ) from exc

    async def _validate_inventory(
        self,
        parsed: ParsedResearchInput,
        managed_snapshot: tuple[ManagedResearchAssetSnapshot, ...],
    ) -> None:
        """Validate pasted references through the configured inventory port."""
        validator = self._context.inventory_validator
        if validator is None:
            return
        try:
            validated = validator(parsed, managed_snapshot)
            if inspect.isawaitable(validated):
                await validated
        except ResearchInputFailure:
            raise
        except Exception as exc:
            raise research_input_failure(
                "research_input_resolution_unavailable",
                "Research input resolution is unavailable.",
                http_status_hint=503,
                retryable=True,
            ) from exc

    def _validate_input_counts(
        self,
        parsed: ParsedResearchInput,
        managed_snapshot: tuple[ManagedResearchAssetSnapshot, ...],
    ) -> None:
        """Apply the same lane and hard limits used by the coordinator."""
        config = self._context.config
        managed_count = len(managed_snapshot)
        pasted_count = len(parsed.candidates)
        total_count = managed_count + pasted_count
        if (
            managed_count > config.API_MAX_ATTACHMENTS_PER_REQUEST
            or pasted_count > config.API_MAX_RESEARCH_DATASET_PATHS
            or total_count > config.API_MAX_RESEARCH_INPUT_REFERENCES
            or total_count > 256
        ):
            raise research_input_failure(
                "research_input_limit_exceeded",
                "Research input count exceeds the allowed limit.",
                http_status_hint=413,
            )

    def _resolve_managed_snapshot(
        self, asset_ids: tuple[str, ...]
    ) -> tuple[ManagedResearchAssetSnapshot, ...]:
        """Resolve owner-qualified snapshots without accepting client paths."""
        if not asset_ids:
            return ()
        resolver = self._context.managed_snapshot_resolver
        if resolver is None:
            raise research_input_failure(
                "research_input_resolution_failed",
                "Research managed attachments could not be verified.",
            )
        try:
            snapshots = resolver(asset_ids)
        except ResearchInputFailure:
            raise
        except (TypeError, ValueError, OSError) as exc:
            raise research_input_failure(
                "research_input_resolution_failed",
                "Research managed attachments could not be verified.",
            ) from exc
        if tuple(item.asset_id for item in snapshots) != asset_ids:
            raise research_input_failure(
                "research_input_resolution_failed",
                "Research managed attachments could not be verified.",
            )
        return tuple(snapshots)

    def _validate_http_request(
        self, request: ResearchHttpAdmissionInput
    ) -> None:
        """Reject malformed opaque HTTP fields before any store lookup."""
        if not isinstance(request, ResearchHttpAdmissionInput):
            raise research_input_failure(
                "research_input_resolution_failed",
                "Research request is invalid.",
            )
        if not isinstance(request.owner, str) or not request.owner.strip():
            raise research_input_failure(
                "research_input_resolution_failed",
                "Research request is invalid.",
            )
        if not isinstance(request.original_query, str):
            raise research_input_failure(
                "research_input_resolution_failed",
                "Research query is invalid.",
            )
        if (
            request.locale is not None
            and request.locale not in SUPPORTED_LOCALES
        ):
            raise research_input_failure(
                "research_input_resolution_failed",
                "Research locale is invalid.",
            )
        if request.interop_mode not in {"off", "auto", "required"}:
            raise research_input_failure(
                "research_input_resolution_failed",
                "Research request is invalid.",
            )
        if request.route_source not in {
            "native",
            "dedicated_web",
            "expert",
        }:
            raise research_input_failure(
                "research_input_resolution_failed",
                "Research request is invalid.",
            )
        if not isinstance(request.managed_asset_ids, tuple) or any(
            not isinstance(asset_id, str) or not asset_id
            for asset_id in request.managed_asset_ids
        ):
            raise research_input_failure(
                "research_input_resolution_failed",
                "Research request is invalid.",
            )
        if tuple(dict.fromkeys(request.managed_asset_ids)) != (
            request.managed_asset_ids
        ):
            raise research_input_failure(
                "research_dataset_duplicate",
                "Research managed assets are invalid.",
            )
        if not isinstance(request.interop_targets, tuple) or any(
            not isinstance(target, str) or not target
            for target in request.interop_targets
        ):
            raise research_input_failure(
                "research_input_resolution_failed",
                "Research request is invalid.",
            )


def parse_idempotency_identity(
    header_value: str | None,
    conversation: ConversationEnvelopeV1 | None,
) -> ResearchRequestIdentity:
    """Validate a header or hash the authoritative conversation tuple."""
    alias = _header_digest(header_value) if header_value is not None else None
    if conversation is None:
        if alias is None:
            raise research_input_failure(
                "research_idempotency_key_required",
                "Research idempotency key is required.",
            )
        return ResearchRequestIdentity("header", alias, None)
    canonical = _digest_parts(
        "conversation",
        str(conversation.conversation_key),
        conversation.turn_id,
        conversation.request_id,
    )
    return ResearchRequestIdentity("conversation", canonical, alias)


def compute_research_client_fingerprint(
    value: ResearchClientFingerprintInput,
) -> str:
    """Hash precisely the versioned caller-owned semantic inputs."""
    return _canonical_digest(
        {
            "version": _FINGERPRINT_VERSION,
            "protocol_version": value.protocol_version,
            "original_query_digest": value.original_query_digest,
            "original_query_length": value.original_query_length,
            "managed_asset_ids": value.managed_asset_ids,
            "locale": value.locale,
            "interop_mode": value.interop_mode,
            "interop_targets": value.interop_targets,
            "conversation_identity_digest": value.conversation_identity_digest,
        }
    )


def lookup_research_admission(
    *,
    owner: str,
    identity: ResearchRequestIdentity,
    client_fingerprint: str,
    store: ResearchInputStore,
) -> ResearchAdmissionOutcome | None:
    """Look up an exact replay using identity fields only.

    The lookup intentionally does not require the original query, parser
    output, managed-asset snapshots, or any other post-identity input.  A
    caller can therefore finish a replay without reopening external I/O
    boundaries.
    """
    if (
        not isinstance(owner, str)
        or not owner.strip()
        or not isinstance(identity, ResearchRequestIdentity)
        or not isinstance(client_fingerprint, str)
        or not client_fingerprint
    ):
        return None
    found, reservation = store.lookup_admission(
        owner=owner,
        identity_digest=identity.canonical_digest,
        header_alias_digest=identity.header_alias_digest,
        client_fingerprint=client_fingerprint,
    )
    if not found:
        return None
    if reservation is None:
        raise research_input_failure(
            "research_idempotency_conflict",
            "Research idempotency key conflicts with this request.",
            http_status_hint=409,
        )
    return _admission_outcome(reservation)


def admit_research_request(
    request: ResearchAdmissionRequest, store: ResearchInputStore
) -> ResearchAdmissionOutcome:
    """Atomically reserve a safe root run or return an exact replay."""
    query_digest, query_length = _validate_caller_preflight(request)
    replay = lookup_research_admission(
        owner=request.owner,
        identity=request.identity,
        client_fingerprint=request.client_fingerprint,
        store=store,
    )
    if replay is not None:
        return replay
    _validate_admission_request(
        request, query_digest=query_digest, query_length=query_length
    )
    parsed = request.parsed_input
    reservation = store.reserve_admission(
        run_id=IdFactory().new_id("run", "research"),
        owner=request.owner,
        identity_digest=request.identity.canonical_digest,
        identity_kind=request.identity.kind,
        header_alias_digest=request.identity.header_alias_digest,
        client_fingerprint=request.client_fingerprint,
        original_query_digest=parsed.original_query_digest,
        original_query_length=parsed.original_query_length,
        effective_query=parsed.effective_query,
        source_map=_source_map(parsed),
        candidates=tuple(asdict(candidate) for candidate in parsed.candidates),
        managed_snapshot=tuple(
            asdict(item) for item in request.managed_snapshot
        ),
        locale=request.locale,
        root_input_digest=parsed.original_query_digest,
    )
    if reservation is None:
        raise research_input_failure(
            "research_idempotency_conflict",
            "Research idempotency key conflicts with this request.",
            http_status_hint=409,
        )
    return _admission_outcome(reservation)


def _admission_outcome(
    reservation: ResearchAdmissionReservation,
) -> ResearchAdmissionOutcome:
    """Project one store reservation into the public status contract."""
    is_running = reservation.status not in {"succeeded", "failed", "cancelled"}
    return ResearchAdmissionOutcome(
        run_id=reservation.run_id,
        replay=reservation.replay,
        worker_owner=not reservation.replay,
        status_code=202 if is_running else 200,
    )


def _validate_caller_preflight(
    request: ResearchAdmissionRequest,
) -> tuple[str, int]:
    """Validate caller fields before binding lookup or parsed-input access."""
    if not isinstance(request, ResearchAdmissionRequest):
        raise research_input_failure(
            "research_input_resolution_failed", "Research request is invalid."
        )
    if not isinstance(request.owner, str) or not request.owner.strip():
        raise research_input_failure(
            "research_input_resolution_failed", "Research request is invalid."
        )
    if not _valid_identity(request.identity):
        raise research_input_failure(
            "research_input_resolution_failed", "Research request is invalid."
        )
    if not _valid_digest(request.client_fingerprint):
        raise research_input_failure(
            "research_input_resolution_failed", "Research request is invalid."
        )
    if not isinstance(request.original_query, str):
        raise research_input_failure(
            "research_input_resolution_failed", "Research query is invalid."
        )
    query_digest = hashlib.sha256(
        request.original_query.encode("utf-8")
    ).hexdigest()
    query_length = len(request.original_query)
    if query_length > _max_query_chars():
        raise research_input_failure(
            "research_input_limit_exceeded",
            "Research query exceeds the allowed limit.",
            http_status_hint=413,
        )
    if not _valid_caller_semantics(request):
        raise research_input_failure(
            "research_input_resolution_failed", "Research request is invalid."
        )
    expected_fingerprint = compute_research_client_fingerprint(
        ResearchClientFingerprintInput(
            original_query_digest=query_digest,
            original_query_length=query_length,
            managed_asset_ids=request.managed_asset_ids,
            locale=request.locale,
            interop_mode=request.interop_mode,
            interop_targets=request.interop_targets,
            conversation_identity_digest=(
                request.identity.canonical_digest
                if request.identity.kind == "conversation"
                else None
            ),
        )
    )
    if request.client_fingerprint != expected_fingerprint:
        raise research_input_failure(
            "research_input_resolution_failed", "Research request is invalid."
        )
    return query_digest, query_length


def _validate_admission_request(
    request: ResearchAdmissionRequest,
    *,
    query_digest: str,
    query_length: int,
) -> None:
    """Reject parser and managed-snapshot data before durable mutation."""
    if (
        request.parsed_input.original_query_digest != query_digest
        or request.parsed_input.original_query_length != query_length
    ):
        raise research_input_failure(
            "research_input_resolution_failed", "Research query is invalid."
        )
    if request.identity.kind not in {"header", "conversation"}:
        raise research_input_failure(
            "research_input_resolution_failed", "Research request is invalid."
        )
    if (
        tuple(dict.fromkeys(request.managed_asset_ids))
        != request.managed_asset_ids
    ):
        raise research_input_failure(
            "research_dataset_duplicate",
            "Research managed assets are invalid.",
        )


def _valid_identity(identity: object) -> bool:
    """Return whether the parsed identity has the digest-only schema."""
    if not isinstance(identity, ResearchRequestIdentity):
        return False
    if identity.kind not in {"header", "conversation"}:
        return False
    if not _valid_digest(identity.canonical_digest):
        return False
    return identity.header_alias_digest is None or _valid_digest(
        identity.header_alias_digest
    )


def _valid_digest(value: object) -> bool:
    """Return whether a persisted identity digest is a SHA-256 hex value."""
    return (
        isinstance(value, str)
        and len(value) == _DIGEST_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_caller_semantics(request: ResearchAdmissionRequest) -> bool:
    """Validate typed semantic fields used by the caller fingerprint."""
    if request.locale not in SUPPORTED_LOCALES:
        return False
    if request.interop_mode not in {"off", "auto", "required"}:
        return False
    if not isinstance(request.managed_asset_ids, tuple) or any(
        not isinstance(asset_id, str) or not asset_id
        for asset_id in request.managed_asset_ids
    ):
        return False
    if not isinstance(request.interop_targets, tuple) or any(
        not isinstance(target, str) or not target
        for target in request.interop_targets
    ):
        return False
    return tuple(dict.fromkeys(request.managed_asset_ids)) == (
        request.managed_asset_ids
    )


def _max_query_chars() -> int:
    """Read the effective API query limit without touching request data."""
    return ApiLimitsConfig().API_MAX_USER_QUERY_CHARS


def _header_digest(value: str) -> str:
    """Return a domain-separated digest for a bounded visible-ASCII key."""
    if not isinstance(value, str):
        raise research_input_failure(
            "research_idempotency_key_required",
            "Research idempotency key is malformed.",
            http_status_hint=422,
        )
    if value == "":
        raise research_input_failure(
            "research_idempotency_key_required",
            "Research idempotency key is required.",
        )
    try:
        encoded = value.encode("ascii", "strict")
    except UnicodeEncodeError as exc:
        raise research_input_failure(
            "research_idempotency_key_required",
            "Research idempotency key is malformed.",
            http_status_hint=422,
        ) from exc
    if not 1 <= len(encoded) <= 255 or any(
        byte < 33 or byte > 126 for byte in encoded
    ):
        raise research_input_failure(
            "research_idempotency_key_required",
            "Research idempotency key is malformed.",
            http_status_hint=422,
        )
    return _digest_parts("header", value)


def _digest_parts(kind: str, *parts: str) -> str:
    """Length-prefix an identity tuple so adjacent values cannot collide."""
    encoded = bytearray(_IDENTITY_VERSION)
    for value in (kind, *parts):
        item = value.encode("utf-8")
        encoded.extend(len(item).to_bytes(4, "big"))
        encoded.extend(item)
    return _digest_bytes(bytes(encoded))


def _canonical_digest(value: object) -> str:
    """Return the SHA-256 of canonical semantic JSON bytes."""
    return _digest_bytes(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )


def _digest_bytes(value: bytes) -> str:
    """Hash one bounded canonical identity payload."""
    return hashlib.sha256(value).hexdigest()


def _source_map(parsed: ParsedResearchInput) -> dict[str, object]:
    """Persist parser provenance without retaining the raw original query."""
    return {
        "version": 1,
        "effective_to_original": parsed.effective_to_original,
        "removed_spans": tuple(asdict(span) for span in parsed.removed_spans),
    }
