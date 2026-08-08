# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Pure preflight and atomic admission for durable Research input work."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal
from uuid import uuid4

from ..agents.research.input_contracts import (
    ParsedResearchInput,
    ResearchInteropMode,
    research_input_failure,
)
from ..agents.research.input_inventory import ManagedResearchAssetSnapshot
from ..runtime.conversation_context.models import ConversationEnvelopeV1
from ..runtime.locale import SupportedLocale
from ..runtime.research_input_store import ResearchInputStore

__all__ = [
    "ResearchAdmissionOutcome",
    "ResearchAdmissionRequest",
    "ResearchClientFingerprintInput",
    "ResearchInputStore",
    "ResearchRequestIdentity",
    "admit_research_request",
    "compute_research_client_fingerprint",
    "parse_idempotency_identity",
]

_IDENTITY_VERSION = b"research-idempotency/v1\x00"
_FINGERPRINT_VERSION = "research-client-fingerprint/v1"


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


def admit_research_request(
    request: ResearchAdmissionRequest, store: ResearchInputStore
) -> ResearchAdmissionOutcome:
    """Atomically reserve a safe root run or return an exact replay."""
    _validate_admission_request(request)
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
    is_running = reservation.status not in {"succeeded", "failed", "cancelled"}
    return ResearchAdmissionOutcome(
        run_id=reservation.run_id,
        replay=reservation.replay,
        worker_owner=not reservation.replay,
        status_code=202 if is_running else 200,
    )


def _validate_admission_request(request: ResearchAdmissionRequest) -> None:
    """Reject malformed caller-owned data before any durable mutation."""
    if not isinstance(request.owner, str) or not request.owner.strip():
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
    if (
        request.parsed_input.original_query_digest != query_digest
        or request.parsed_input.original_query_length
        != len(request.original_query)
    ):
        raise research_input_failure(
            "research_input_resolution_failed", "Research query is invalid."
        )
    if (
        not isinstance(request.client_fingerprint, str)
        or not request.client_fingerprint
    ):
        raise research_input_failure(
            "research_input_resolution_failed", "Research request is invalid."
        )
    expected_fingerprint = compute_research_client_fingerprint(
        ResearchClientFingerprintInput(
            original_query_digest=query_digest,
            original_query_length=len(request.original_query),
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
