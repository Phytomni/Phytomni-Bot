# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Pure preflight and atomic admission for durable Research input work."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal
from uuid import uuid4

from ..agents.analyst.agent import AnalystAgent
from ..agents.analyst.defaults import ANALYST_CONFIG
from ..agents.research.input_contracts import (
    ParsedResearchInput,
    ResearchInteropMode,
    research_input_failure,
)
from ..agents.research.input_coordinator import ResearchInputCoordinator
from ..agents.research.input_inventory import ManagedResearchAssetSnapshot
from ..agents.research.recovery import ResearchPreAcceptanceRejected
from ..agents.research.recovery_support import register_recovery_service
from ..config.api_limits import ApiLimitsConfig
from ..config.defaults import ApiConfig
from ..config.settings import get_sensitive_config
from ..runtime.conversation_context.models import ConversationEnvelopeV1
from ..runtime.locale import SUPPORTED_LOCALES, SupportedLocale
from ..runtime.research_input_store import (
    ResearchAdmissionReservation,
    ResearchInputStore,
)

__all__ = [
    "ResearchAdmissionOutcome",
    "ResearchAdmissionRequest",
    "ResearchClientFingerprintInput",
    "ResearchInputStore",
    "build_research_input_coordinator",
    "ensure_research_input_runtime",
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


_RUNTIME_STATE: dict[str, _ResearchInputRuntime | None] = {"current": None}


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
    coordinator = ResearchInputCoordinator.from_production(
        request,
        **ports,
    )
    recovery = coordinator.recovery
    if recovery is None:
        raise RuntimeError("Research production runtime has no recovery")
    _RUNTIME_STATE["current"] = _ResearchInputRuntime(coordinator, recovery)
    register_recovery_service(recovery)
    return coordinator


def ensure_research_input_runtime(
    db_path: str | None = None,
) -> Any:
    """Construct the HTTP Research worker before lifespan recovery."""
    runtime = _RUNTIME_STATE["current"]
    if runtime is not None:
        return runtime.coordinator
    sensitive_config = get_sensitive_config()
    analyst_agent = AnalystAgent(
        analyst_config=ANALYST_CONFIG,
        sensitive_config=sensitive_config,
    )
    return build_research_input_coordinator(
        store=ResearchInputStore(db_path or _default_tasks_db_path()),
        provider=_UnavailableResearchWorkProvider(),
        analyst_agent=analyst_agent,
        analyst_config=ANALYST_CONFIG,
        sensitive_config=sensitive_config,
    )


def _default_tasks_db_path() -> str:
    """Read the API task database without importing the application facade."""
    return ApiConfig().API_TASKS_DB_PATH


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
        run_id=str(uuid4()),
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
