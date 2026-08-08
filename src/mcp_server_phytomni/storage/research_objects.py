# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Private exact-key metadata authority for Research dataset objects."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .obs_relay_ops import (
    ObsObjectMetadataError,
    ObsObjectNotFoundError,
    _ObsObjectMetadata,
    head_object_metadata,
)
from .obs_storage import normalize_obs_object_key

__all__ = [
    "DirectResearchObjectMetadataPort",
    "ResearchObjectAuthority",
    "ResearchObjectCandidate",
    "ResearchObjectMetadataError",
    "ResearchObjectMetadataPort",
    "ResearchObjectResolveRequest",
    "ResearchObjectRevokeRequest",
    "ResearchObjectSnapshot",
    "ResearchObjectVerifyRequest",
]

_SNAPSHOT_SCHEMA = "research-object-snapshot/v1"
_METADATA_FAILURE = "Research object metadata could not be verified."


@dataclass(frozen=True, slots=True)
class ResearchObjectCandidate:
    """One trusted dataset reference awaiting exact-key metadata resolution."""

    dataset_id: str
    exact_reference: str
    compound_suffix: str


@dataclass(frozen=True, slots=True)
class ResearchObjectResolveRequest:
    """One run-scoped request to resolve dataset object metadata."""

    parent_run_id: str
    execution_fingerprint: str
    objects: tuple[ResearchObjectCandidate, ...]


@dataclass(frozen=True, slots=True)
class ResearchObjectSnapshot:
    """Safe, immutable exact-key metadata captured at resolution time."""

    dataset_id: str
    size_bytes: int
    etag: str | None
    version_id: str | None
    last_modified: str | None
    placeholder: bool
    snapshot_digest: str


@dataclass(frozen=True, slots=True)
class ResearchObjectAuthority:
    """Opaque authority token bound to one safe metadata snapshot."""

    dataset_id: str
    authority_id: str
    snapshot: ResearchObjectSnapshot


@dataclass(frozen=True, slots=True)
class ResearchObjectVerifyRequest:
    """One run-scoped request to verify resolved object snapshots."""

    parent_run_id: str
    execution_fingerprint: str
    authorities: tuple[ResearchObjectAuthority, ...]


@dataclass(frozen=True, slots=True)
class ResearchObjectRevokeRequest:
    """One run-scoped request to revoke private object authority tokens."""

    parent_run_id: str
    execution_fingerprint: str
    authority_ids: tuple[str, ...]


class ResearchObjectMetadataError(Exception):
    """Safe failure for unavailable, changed, or out-of-scope metadata."""

    def __init__(self) -> None:
        super().__init__(_METADATA_FAILURE)


class ResearchObjectMetadataPort(Protocol):
    """Boundary for resolving and checking private object authority."""

    async def resolve(
        self, request: ResearchObjectResolveRequest
    ) -> tuple[ResearchObjectAuthority, ...]:
        """Resolve exact-key snapshots for one parent run."""
        raise NotImplementedError

    async def verify(
        self, request: ResearchObjectVerifyRequest
    ) -> tuple[ResearchObjectAuthority, ...]:
        """Re-read and compare private snapshots for one parent run."""
        raise NotImplementedError

    async def revoke(self, request: ResearchObjectRevokeRequest) -> None:
        """Revoke private authority records for one parent run."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class _AuthorityRecord:
    """In-process state that never crosses the metadata port boundary."""

    parent_run_id: str
    execution_fingerprint: str
    object_key: str
    authority: ResearchObjectAuthority


class DirectResearchObjectMetadataPort:
    """Resolve private authorities through exact-key OBS metadata HEADs."""

    def __init__(self, bucket: str, client_factory: Callable[[], Any]) -> None:
        self._bucket = bucket
        self._client_factory = client_factory
        self._authorities: dict[str, _AuthorityRecord] = {}

    async def resolve(
        self, request: ResearchObjectResolveRequest
    ) -> tuple[ResearchObjectAuthority, ...]:
        """Resolve request objects with one client and no object-body I/O."""
        client = self._client_factory()
        authorities: list[ResearchObjectAuthority] = []
        for candidate in request.objects:
            object_key = _normalized_object_key(candidate, self._bucket)
            snapshot = _read_snapshot(
                candidate.dataset_id, object_key, self._bucket, client
            )
            if snapshot.placeholder:
                raise ResearchObjectMetadataError()
            authority = ResearchObjectAuthority(
                dataset_id=candidate.dataset_id,
                authority_id=secrets.token_urlsafe(24),
                snapshot=snapshot,
            )
            self._authorities[authority.authority_id] = _AuthorityRecord(
                parent_run_id=request.parent_run_id,
                execution_fingerprint=request.execution_fingerprint,
                object_key=object_key,
                authority=authority,
            )
            authorities.append(authority)
        return tuple(authorities)

    async def verify(
        self, request: ResearchObjectVerifyRequest
    ) -> tuple[ResearchObjectAuthority, ...]:
        """Re-HEAD each private authority and reject changed snapshots."""
        client = self._client_factory()
        verified: list[ResearchObjectAuthority] = []
        for authority in request.authorities:
            record = self._matching_record(
                authority,
                request.parent_run_id,
                request.execution_fingerprint,
            )
            current = _read_snapshot(
                authority.dataset_id,
                record.object_key,
                self._bucket,
                client,
            )
            if current != authority.snapshot:
                raise ResearchObjectMetadataError()
            verified.append(record.authority)
        return tuple(verified)

    async def revoke(self, request: ResearchObjectRevokeRequest) -> None:
        """Drop matching in-process authority state without object deletion."""
        for authority_id in request.authority_ids:
            record = self._authorities.get(authority_id)
            if record is not None and _matches_scope(
                record, request.parent_run_id, request.execution_fingerprint
            ):
                self._authorities.pop(authority_id, None)

    def _matching_record(
        self,
        authority: ResearchObjectAuthority,
        parent_run_id: str,
        execution_fingerprint: str,
    ) -> _AuthorityRecord:
        """Return a matching authority record without exposing its identity."""
        record = self._authorities.get(authority.authority_id)
        if (
            record is None
            or record.authority != authority
            or not _matches_scope(record, parent_run_id, execution_fingerprint)
        ):
            raise ResearchObjectMetadataError()
        return record


def _normalized_object_key(
    candidate: ResearchObjectCandidate, bucket: str
) -> str:
    """Validate the configured-bucket reference and retain only its key."""
    reference = candidate.exact_reference
    if reference[:6].casefold() != "obs://":
        raise ResearchObjectMetadataError()
    reference_bucket, separator, raw_key = reference[6:].partition("/")
    if not separator or reference_bucket.casefold() != bucket.casefold():
        raise ResearchObjectMetadataError()
    try:
        object_key = normalize_obs_object_key(raw_key, bucket)
    except ValueError:
        raise ResearchObjectMetadataError() from None
    if (
        not object_key
        or not candidate.compound_suffix
        or not object_key.casefold().endswith(
            candidate.compound_suffix.casefold()
        )
    ):
        raise ResearchObjectMetadataError()
    return object_key


def _read_snapshot(
    dataset_id: str, object_key: str, bucket: str, client: Any
) -> ResearchObjectSnapshot:
    """Build one safe snapshot from a single exact-key metadata HEAD."""
    try:
        metadata = head_object_metadata(
            bucket=bucket, object_key=object_key, client=client
        )
    except (ObsObjectMetadataError, ObsObjectNotFoundError):
        raise ResearchObjectMetadataError() from None
    return _snapshot_from_metadata(dataset_id, metadata)


def _snapshot_from_metadata(
    dataset_id: str, metadata: _ObsObjectMetadata
) -> ResearchObjectSnapshot:
    """Build a versioned canonical digest from safe metadata only."""
    placeholder = metadata.size_bytes == 0
    digest_fields = {
        "dataset_id": dataset_id,
        "etag": metadata.etag,
        "last_modified": metadata.last_modified,
        "placeholder": placeholder,
        "schema": _SNAPSHOT_SCHEMA,
        "size_bytes": metadata.size_bytes,
        "version_id": metadata.version_id,
    }
    encoded = json.dumps(
        digest_fields,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return ResearchObjectSnapshot(
        dataset_id=dataset_id,
        size_bytes=metadata.size_bytes,
        etag=metadata.etag,
        version_id=metadata.version_id,
        last_modified=metadata.last_modified,
        placeholder=placeholder,
        snapshot_digest=hashlib.sha256(encoded).hexdigest(),
    )


def _matches_scope(
    record: _AuthorityRecord,
    parent_run_id: str,
    execution_fingerprint: str,
) -> bool:
    """Return whether the request may operate on one private authority."""
    return (
        record.parent_run_id == parent_run_id
        and record.execution_fingerprint == execution_fingerprint
    )
