# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Scoped, exact-key Research input grant routes for the operator relay.

The public routes accept only opaque dataset identities and grant bindings.
Exact OBS references remain request-local: they are validated before a
metadata-only HEAD, persisted only by ``ResearchGrantStore``, and never
projected into a response or relay audit record.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache, partial
from typing import cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)
from starlette.requests import Request

from ...agents.research.scientific_formats import classify_scientific_reference
from ...config.defaults import ApiConfig, ServerConfig
from ...config.settings import get_sensitive_config
from ...runtime.request_context import current_request_id
from ...storage.obs_client import ObsClient
from ...storage.obs_storage import ObsPathError, normalize_obs_object_key
from ...storage.research_objects import (
    DirectResearchObjectMetadataPort,
    ResearchObjectAuthority,
    ResearchObjectCandidate,
    ResearchObjectMetadataError,
    ResearchObjectMetadataPort,
    ResearchObjectResolveRequest,
    ResearchObjectRevokeRequest,
    ResearchObjectSnapshot,
    ResearchObjectVerifyRequest,
)
from ..auth import RELAY_RESEARCH_INPUT_SERVICE, ApiPrincipal
from .audit import RelayAuditRecord, get_audit_store
from .deps import read_relay_body, require_relay_access
from .research_grants import (
    ResearchGrantError,
    ResearchGrantRecord,
    ResearchGrantResolve,
    ResearchGrantRevoke,
    ResearchGrantStore,
    ResearchGrantVerify,
)

__all__ = [
    "add_research_input_routes",
    "get_research_grant_store",
    "get_research_object_metadata_port",
]

_LOGGER = logging.getLogger(__name__)
_PROTOCOL = "research_object_grant_v1"
_SCHEMA_VERSION = 1
_MAX_OBJECTS = 256
_SAFE_FAILURE = "research object grant could not be verified"
_MAX_IDENTIFIER_LENGTH = 512
_MAX_REFERENCE_LENGTH = 4096
_GRANT_AUTHORITY_BINDINGS: dict[
    tuple[str, str, str, str], ResearchObjectAuthority
] = {}


@dataclass(frozen=True, slots=True)
class _AuditContext:
    """Safe metadata prepared for one relay audit write."""

    principal: ApiPrincipal
    operation: str
    started: float
    dataset_ids: Iterable[str] = ()
    grant_ids: Iterable[str] = ()
    status_code: int = 200


class _StrictGrantModel(BaseModel):
    """Reject coercion and unrecognized fields at the relay boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)


class _ResolveObject(_StrictGrantModel):
    """One opaque dataset identity with its exact object reference."""

    dataset_id: str = Field(min_length=1, max_length=_MAX_IDENTIFIER_LENGTH)
    exact_reference: str = Field(
        min_length=1, max_length=_MAX_REFERENCE_LENGTH
    )

    @field_validator("dataset_id", "exact_reference")
    @classmethod
    def _reject_control_text(cls, value: str) -> str:
        """Reject ambiguous control bytes without normalizing caller input."""
        if any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("control text is invalid")
        return value


class _ResolvePayload(_StrictGrantModel):
    """Bound exact-key metadata resolution request."""

    schema_version: int = Field(ge=_SCHEMA_VERSION, le=_SCHEMA_VERSION)
    parent_run_id: str = Field(min_length=1, max_length=_MAX_IDENTIFIER_LENGTH)
    execution_fingerprint: str = Field(
        min_length=1, max_length=_MAX_IDENTIFIER_LENGTH
    )
    objects: list[_ResolveObject] = Field(
        min_length=1, max_length=_MAX_OBJECTS
    )

    @field_validator("parent_run_id", "execution_fingerprint")
    @classmethod
    def _reject_binding_controls(cls, value: str) -> str:
        """Require opaque non-control binding values."""
        if any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("control text is invalid")
        return value


class _SnapshotPayload(_StrictGrantModel):
    """Safe immutable metadata expected by a grant verification request."""

    dataset_id: str = Field(min_length=1, max_length=_MAX_IDENTIFIER_LENGTH)
    size_bytes: int = Field(ge=0)
    etag: str | None = Field(default=None, max_length=_MAX_IDENTIFIER_LENGTH)
    version_id: str | None = Field(
        default=None, max_length=_MAX_IDENTIFIER_LENGTH
    )
    last_modified: str | None = Field(
        default=None, max_length=_MAX_IDENTIFIER_LENGTH
    )
    placeholder: bool
    snapshot_digest: str = Field(
        min_length=1, max_length=_MAX_IDENTIFIER_LENGTH
    )

    @field_validator(
        "dataset_id",
        "etag",
        "version_id",
        "last_modified",
        "snapshot_digest",
    )
    @classmethod
    def _reject_snapshot_controls(cls, value: str | None) -> str | None:
        """Reject control characters in all supplied metadata text."""
        if value is not None and any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("control text is invalid")
        return value


class _VerifyGrant(_StrictGrantModel):
    """One opaque grant id and its caller-observed snapshot."""

    dataset_id: str = Field(min_length=1, max_length=_MAX_IDENTIFIER_LENGTH)
    grant_id: str = Field(min_length=1, max_length=_MAX_IDENTIFIER_LENGTH)
    expected_snapshot: _SnapshotPayload

    @field_validator("dataset_id", "grant_id")
    @classmethod
    def _reject_grant_controls(cls, value: str) -> str:
        """Require opaque non-control identities at the public boundary."""
        if any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("control text is invalid")
        return value


class _VerifyPayload(_StrictGrantModel):
    """Bound grant revalidation request."""

    schema_version: int = Field(ge=_SCHEMA_VERSION, le=_SCHEMA_VERSION)
    parent_run_id: str = Field(min_length=1, max_length=_MAX_IDENTIFIER_LENGTH)
    execution_fingerprint: str = Field(
        min_length=1, max_length=_MAX_IDENTIFIER_LENGTH
    )
    grants: list[_VerifyGrant] = Field(min_length=1, max_length=_MAX_OBJECTS)

    @field_validator("parent_run_id", "execution_fingerprint")
    @classmethod
    def _reject_verify_binding_controls(cls, value: str) -> str:
        """Keep scope bindings opaque and non-ambiguous."""
        if any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("control text is invalid")
        return value


class _RevokePayload(_StrictGrantModel):
    """Bound idempotent grant revocation request."""

    schema_version: int = Field(ge=_SCHEMA_VERSION, le=_SCHEMA_VERSION)
    parent_run_id: str = Field(min_length=1, max_length=_MAX_IDENTIFIER_LENGTH)
    execution_fingerprint: str = Field(
        min_length=1, max_length=_MAX_IDENTIFIER_LENGTH
    )
    grant_ids: list[str] = Field(min_length=1, max_length=_MAX_OBJECTS)

    @field_validator("parent_run_id", "execution_fingerprint", "grant_ids")
    @classmethod
    def _reject_revoke_controls(
        cls, value: str | list[str]
    ) -> str | list[str]:
        """Reject malformed opaque bindings and ids before any state access."""
        values = [value] if isinstance(value, str) else value
        if any(
            not item
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in item
            )
            for item in values
        ):
            raise ValueError("control text is invalid")
        return value


def _operator_obs_client(obs_server: str) -> ObsClient:
    """Build an operator-only OBS SDK client lazily for metadata HEADs."""
    access_key, secret_key = get_sensitive_config().obs_credentials()
    return ObsClient(
        access_key_id=access_key,
        secret_access_key=secret_key,
        server=obs_server,
    )


@cache
def _cached_metadata_port(
    bucket: str, obs_server: str
) -> DirectResearchObjectMetadataPort:
    """Keep source authority state stable across route calls in one worker."""
    return DirectResearchObjectMetadataPort(
        bucket=bucket,
        client_factory=partial(_operator_obs_client, obs_server),
    )


async def get_research_object_metadata_port() -> ResearchObjectMetadataPort:
    """Return the injected production exact-key metadata authority."""
    config = ServerConfig()
    return _cached_metadata_port(config.BUCKET_NAME, config.OBS_SERVER)


@cache
def _cached_grant_store(database_path: str) -> ResearchGrantStore:
    """Open one durable grant store per configured local database path."""
    return ResearchGrantStore(database_path)


async def get_research_grant_store() -> ResearchGrantStore:
    """Return the injected production grant persistence boundary."""
    return _cached_grant_store(ApiConfig().RELAY_AUDIT_DB_PATH)


def _safe_failure() -> HTTPException:
    """Build the sole non-disclosing business failure for grant operations."""
    return HTTPException(status_code=400, detail=_SAFE_FAILURE)


def _snapshot_from_payload(
    payload: _SnapshotPayload,
) -> ResearchObjectSnapshot:
    """Build the typed immutable snapshot expected by private boundaries."""
    return ResearchObjectSnapshot(
        dataset_id=payload.dataset_id,
        size_bytes=payload.size_bytes,
        etag=payload.etag,
        version_id=payload.version_id,
        last_modified=payload.last_modified,
        placeholder=payload.placeholder,
        snapshot_digest=payload.snapshot_digest,
    )


def _snapshot_payload(snapshot: ResearchObjectSnapshot) -> dict[str, object]:
    """Project safe immutable snapshot fields without an object reference."""
    return {
        "dataset_id": snapshot.dataset_id,
        "size_bytes": snapshot.size_bytes,
        "etag": snapshot.etag,
        "version_id": snapshot.version_id,
        "last_modified": snapshot.last_modified,
        "placeholder": snapshot.placeholder,
        "snapshot_digest": snapshot.snapshot_digest,
    }


def _grant_payload(record: ResearchGrantRecord) -> dict[str, object]:
    """Project one safe opaque grant response without private key material."""
    return {
        "dataset_id": record.dataset_id,
        "grant_id": record.grant_id,
        "snapshot": _snapshot_payload(record.snapshot),
        "expires_at": record.expires_at.isoformat(),
        "revision": record.revision,
    }


def _object_candidates(
    objects: Iterable[_ResolveObject], bucket: str
) -> tuple[ResearchObjectCandidate, ...]:
    """Preflight exact configured-bucket references before metadata I/O."""
    candidates: list[ResearchObjectCandidate] = []
    dataset_ids: set[str] = set()
    references: set[str] = set()
    for item in objects:
        if (
            item.dataset_id in dataset_ids
            or item.exact_reference in references
        ):
            raise ValueError("duplicate opaque object")
        dataset_ids.add(item.dataset_id)
        references.add(item.exact_reference)
        _validate_exact_reference(item.exact_reference, bucket)
        descriptor = classify_scientific_reference(item.exact_reference)
        if descriptor is None:
            raise ValueError("unsupported scientific suffix")
        candidates.append(
            ResearchObjectCandidate(
                dataset_id=item.dataset_id,
                exact_reference=item.exact_reference,
                compound_suffix=descriptor.canonical_suffix,
            )
        )
    return tuple(candidates)


def _validate_exact_reference(reference: str, bucket: str) -> None:
    """Reject non-OBS, wrong-bucket, or non-canonical exact object paths."""
    if reference[:6].casefold() != "obs://":
        raise ValueError("reference is not OBS")
    reference_bucket, separator, raw_key = reference[6:].partition("/")
    if not separator or reference_bucket.casefold() != bucket.casefold():
        raise ValueError("reference is outside configured bucket")
    try:
        normalized = normalize_obs_object_key(raw_key, bucket)
    except (ObsPathError, ValueError):
        raise ValueError("reference key is invalid") from None
    if not normalized:
        raise ValueError("reference key is empty")


def _validate_resolved_authorities(
    candidates: tuple[ResearchObjectCandidate, ...],
    authorities: tuple[ResearchObjectAuthority, ...],
) -> tuple[ResearchObjectAuthority, ...]:
    """Require one non-placeholder, unique authority for every candidate."""
    if len(candidates) != len(authorities):
        raise ResearchObjectMetadataError()
    authority_ids: set[str] = set()
    for candidate, authority in zip(candidates, authorities, strict=True):
        if (
            authority.dataset_id != candidate.dataset_id
            or authority.snapshot.dataset_id != candidate.dataset_id
            or authority.snapshot.placeholder
            or not authority.authority_id
            or authority.authority_id in authority_ids
        ):
            raise ResearchObjectMetadataError()
        authority_ids.add(authority.authority_id)
    return authorities


def _binding_key(
    principal: ApiPrincipal,
    parent_run_id: str,
    execution_fingerprint: str,
    grant_id: str,
) -> tuple[str, str, str, str]:
    """Return an in-worker source-authority lookup key for one grant."""
    return (
        principal.key_prefix,
        parent_run_id,
        execution_fingerprint,
        grant_id,
    )


def _remember_source_authorities(
    principal: ApiPrincipal,
    parent_run_id: str,
    execution_fingerprint: str,
    records: tuple[ResearchGrantRecord, ...],
    authorities: tuple[ResearchObjectAuthority, ...],
) -> None:
    """Bind returned grants to in-process HEAD authorities by dataset order."""
    if len(records) != len(authorities):
        raise ResearchGrantError()
    for record, authority in zip(records, authorities, strict=True):
        if record.dataset_id != authority.dataset_id:
            raise ResearchGrantError()
        _GRANT_AUTHORITY_BINDINGS[
            _binding_key(
                principal,
                parent_run_id,
                execution_fingerprint,
                record.grant_id,
            )
        ] = authority


def _source_authorities_for_verify(
    principal: ApiPrincipal, payload: _VerifyPayload
) -> tuple[ResearchObjectAuthority, ...]:
    """Recover only run/key-bound source authorities for re-HEAD requests."""
    authorities: list[ResearchObjectAuthority] = []
    seen_grant_ids: set[str] = set()
    for grant in payload.grants:
        if grant.grant_id in seen_grant_ids:
            raise ResearchGrantError()
        seen_grant_ids.add(grant.grant_id)
        authority = _GRANT_AUTHORITY_BINDINGS.get(
            _binding_key(
                principal,
                payload.parent_run_id,
                payload.execution_fingerprint,
                grant.grant_id,
            )
        )
        if authority is None or authority.dataset_id != grant.dataset_id:
            raise ResearchGrantError()
        if authority.snapshot != _snapshot_from_payload(
            grant.expected_snapshot
        ):
            raise ResearchGrantError()
        authorities.append(authority)
    return tuple(authorities)


def _grant_authorities(
    grants: list[_VerifyGrant],
    source_authorities: tuple[ResearchObjectAuthority, ...],
) -> tuple[ResearchObjectAuthority, ...]:
    """Map verified source snapshots onto the opaque public grant ids."""
    if len(grants) != len(source_authorities):
        raise ResearchGrantError()
    return tuple(
        ResearchObjectAuthority(
            dataset_id=grant.dataset_id,
            authority_id=grant.grant_id,
            snapshot=authority.snapshot,
        )
        for grant, authority in zip(grants, source_authorities, strict=True)
    )


def _audit_digest(value: str) -> str:
    """Return a stable digest for one opaque audit identifier."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _record_audit(context: _AuditContext) -> None:
    """Persist only grant-operation metadata, never references or payloads."""
    dataset_digests = tuple(
        _audit_digest(value) for value in context.dataset_ids
    )
    grant_digests = tuple(_audit_digest(value) for value in context.grant_ids)
    metadata = {
        "count": max(len(dataset_digests), len(grant_digests)),
        "dataset_digests": dataset_digests,
        "grant_digests": grant_digests,
    }
    entry = RelayAuditRecord(
        request_id=current_request_id() or "",
        user_id=context.principal.user_id,
        key_prefix=context.principal.key_prefix,
        service=RELAY_RESEARCH_INPUT_SERVICE,
        operation=context.operation,
        status_code=context.status_code,
        duration_ms=int((time.monotonic() - context.started) * 1000),
        request_body=json.dumps(
            metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ),
    )
    try:
        get_audit_store(ApiConfig().RELAY_AUDIT_DB_PATH).record(entry)
    except (sqlite3.Error, OSError, RuntimeError):
        _LOGGER.warning("research input relay audit write failed")


async def _read_resolve_payload(request: Request) -> _ResolvePayload:
    """Read a bounded strict resolve request or return one safe failure."""
    return cast(_ResolvePayload, await _read_payload(request, _ResolvePayload))


async def _read_verify_payload(request: Request) -> _VerifyPayload:
    """Read a bounded strict verify request or return one safe failure."""
    return cast(_VerifyPayload, await _read_payload(request, _VerifyPayload))


async def _read_revoke_payload(request: Request) -> _RevokePayload:
    """Read a bounded strict revoke request or return one safe failure."""
    return cast(_RevokePayload, await _read_payload(request, _RevokePayload))


async def _read_payload(
    request: Request,
    payload_type: type[_ResolvePayload | _VerifyPayload | _RevokePayload],
) -> _ResolvePayload | _VerifyPayload | _RevokePayload:
    """Bound raw input before strict JSON schema validation."""
    body = await read_relay_body(request, ApiConfig().RELAY_REQUEST_MAX_BYTES)
    try:
        return payload_type.model_validate_json(body)
    except ValidationError:
        raise _safe_failure() from None


async def _resolve_grants(
    request: Request,
    principal: ApiPrincipal,
    metadata_port: ResearchObjectMetadataPort,
    grant_store: ResearchGrantStore,
) -> dict[str, object]:
    """Resolve exact objects through HEAD and persist their opaque grants."""
    started = time.monotonic()
    payload = await _read_resolve_payload(request)
    try:
        candidates = _object_candidates(
            payload.objects, ServerConfig().BUCKET_NAME
        )
        authorities = _validate_resolved_authorities(
            candidates,
            await metadata_port.resolve(
                ResearchObjectResolveRequest(
                    parent_run_id=payload.parent_run_id,
                    execution_fingerprint=payload.execution_fingerprint,
                    objects=candidates,
                )
            ),
        )
        records = grant_store.resolve_or_replay(
            ResearchGrantResolve(
                principal_key_prefix=principal.key_prefix,
                parent_run_id=payload.parent_run_id,
                execution_fingerprint=payload.execution_fingerprint,
                objects=candidates,
                authorities=authorities,
            ),
            datetime.now(UTC),
        )
        _remember_source_authorities(
            principal,
            payload.parent_run_id,
            payload.execution_fingerprint,
            records,
            authorities,
        )
    except (
        OSError,
        sqlite3.Error,
        ResearchObjectMetadataError,
        ResearchGrantError,
        ValueError,
    ):
        _record_audit(
            _AuditContext(
                principal=principal,
                operation="research_grant_resolve",
                started=started,
                dataset_ids=(item.dataset_id for item in payload.objects),
                status_code=400,
            )
        )
        raise _safe_failure() from None
    _record_audit(
        _AuditContext(
            principal=principal,
            operation="research_grant_resolve",
            started=started,
            dataset_ids=(item.dataset_id for item in payload.objects),
        )
    )
    return {"grants": [_grant_payload(record) for record in records]}


async def _verify_grants(
    request: Request,
    principal: ApiPrincipal,
    metadata_port: ResearchObjectMetadataPort,
    grant_store: ResearchGrantStore,
) -> dict[str, object]:
    """Re-HEAD private sources before verifying or rotating grants."""
    started = time.monotonic()
    payload = await _read_verify_payload(request)
    try:
        source_authorities = _source_authorities_for_verify(principal, payload)
        verified_source_authorities = await metadata_port.verify(
            ResearchObjectVerifyRequest(
                parent_run_id=payload.parent_run_id,
                execution_fingerprint=payload.execution_fingerprint,
                authorities=source_authorities,
            )
        )
        if tuple(verified_source_authorities) != source_authorities:
            raise ResearchObjectMetadataError()
        records = grant_store.verify_or_rotate(
            ResearchGrantVerify(
                principal_key_prefix=principal.key_prefix,
                parent_run_id=payload.parent_run_id,
                execution_fingerprint=payload.execution_fingerprint,
                authorities=_grant_authorities(
                    payload.grants, verified_source_authorities
                ),
            ),
            datetime.now(UTC),
        )
        _remember_source_authorities(
            principal,
            payload.parent_run_id,
            payload.execution_fingerprint,
            records,
            verified_source_authorities,
        )
    except (
        OSError,
        sqlite3.Error,
        ResearchObjectMetadataError,
        ResearchGrantError,
        ValueError,
    ):
        _record_audit(
            _AuditContext(
                principal=principal,
                operation="research_grant_verify",
                started=started,
                dataset_ids=(item.dataset_id for item in payload.grants),
                grant_ids=(item.grant_id for item in payload.grants),
                status_code=400,
            )
        )
        raise _safe_failure() from None
    _record_audit(
        _AuditContext(
            principal=principal,
            operation="research_grant_verify",
            started=started,
            dataset_ids=(item.dataset_id for item in payload.grants),
            grant_ids=(item.grant_id for item in payload.grants),
        )
    )
    return {"grants": [_grant_payload(record) for record in records]}


async def _revoke_grants(
    request: Request,
    principal: ApiPrincipal,
    metadata_port: ResearchObjectMetadataPort,
    grant_store: ResearchGrantStore,
) -> dict[str, int]:
    """Revoke durable grants and matching in-worker metadata authorities."""
    started = time.monotonic()
    payload = await _read_revoke_payload(request)
    try:
        grant_store.revoke(
            ResearchGrantRevoke(
                principal_key_prefix=principal.key_prefix,
                parent_run_id=payload.parent_run_id,
                execution_fingerprint=payload.execution_fingerprint,
                grant_ids=tuple(payload.grant_ids),
            ),
            datetime.now(UTC),
        )
        source_authority_ids = tuple(
            authority.authority_id
            for grant_id in payload.grant_ids
            if (
                authority := _GRANT_AUTHORITY_BINDINGS.pop(
                    _binding_key(
                        principal,
                        payload.parent_run_id,
                        payload.execution_fingerprint,
                        grant_id,
                    ),
                    None,
                )
            )
            is not None
        )
        await metadata_port.revoke(
            ResearchObjectRevokeRequest(
                parent_run_id=payload.parent_run_id,
                execution_fingerprint=payload.execution_fingerprint,
                authority_ids=source_authority_ids,
            )
        )
    except (
        OSError,
        sqlite3.Error,
        ResearchObjectMetadataError,
        ResearchGrantError,
        ValueError,
    ):
        _record_audit(
            _AuditContext(
                principal=principal,
                operation="research_grant_revoke",
                started=started,
                grant_ids=payload.grant_ids,
                status_code=400,
            )
        )
        raise _safe_failure() from None
    _record_audit(
        _AuditContext(
            principal=principal,
            operation="research_grant_revoke",
            started=started,
            grant_ids=payload.grant_ids,
        )
    )
    return {"revoked": len(payload.grant_ids)}


def add_research_input_routes(router: APIRouter) -> None:
    """Register only the scoped capability and grant-operation literals."""

    @router.get("/capabilities")
    async def research_input_capabilities(
        _principal: ApiPrincipal = Depends(
            require_relay_access(RELAY_RESEARCH_INPUT_SERVICE)
        ),
    ) -> dict[str, dict[str, list[int] | int]]:
        """Return the sanitized operator protocol capability descriptor."""
        return {
            "protocols": {_PROTOCOL: [_SCHEMA_VERSION]},
            "research_object_grant": {"max_objects": _MAX_OBJECTS},
        }

    @router.post("/research-input/object-grants")
    async def resolve_research_object_grants(
        request: Request,
        principal: ApiPrincipal = Depends(
            require_relay_access(RELAY_RESEARCH_INPUT_SERVICE)
        ),
        metadata_port: ResearchObjectMetadataPort = Depends(
            get_research_object_metadata_port
        ),
        grant_store: ResearchGrantStore = Depends(get_research_grant_store),
    ) -> dict[str, object]:
        """Resolve exact object metadata and create/replay bound grants."""
        return await _resolve_grants(
            request, principal, metadata_port, grant_store
        )

    @router.post("/research-input/object-grants/verify")
    async def verify_research_object_grants(
        request: Request,
        principal: ApiPrincipal = Depends(
            require_relay_access(RELAY_RESEARCH_INPUT_SERVICE)
        ),
        metadata_port: ResearchObjectMetadataPort = Depends(
            get_research_object_metadata_port
        ),
        grant_store: ResearchGrantStore = Depends(get_research_grant_store),
    ) -> dict[str, object]:
        """Revalidate or rotate exact-key grants for the bound run."""
        return await _verify_grants(
            request, principal, metadata_port, grant_store
        )

    @router.post("/research-input/object-grants/revoke")
    async def revoke_research_object_grants(
        request: Request,
        principal: ApiPrincipal = Depends(
            require_relay_access(RELAY_RESEARCH_INPUT_SERVICE)
        ),
        metadata_port: ResearchObjectMetadataPort = Depends(
            get_research_object_metadata_port
        ),
        grant_store: ResearchGrantStore = Depends(get_research_grant_store),
    ) -> dict[str, int]:
        """Idempotently revoke exact-key grants for the bound run."""
        return await _revoke_grants(
            request, principal, metadata_port, grant_store
        )
