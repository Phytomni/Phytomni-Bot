# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Private exact-key metadata authority for Research dataset objects."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

from ..runtime.outbound import ObsProfileName
from .obs_relay_ops import (
    ObsObjectMetadataError,
    ObsObjectNotFoundError,
    _ObsObjectMetadata,
    head_object_metadata,
)
from .obs_storage import normalize_obs_object_key

if TYPE_CHECKING:
    from ..common.relay_client import RelayClient

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
    "RESEARCH_OBJECT_SNAPSHOT_FIELDS",
    "RESEARCH_OBJECT_SNAPSHOT_FIELD_NAMES",
    "RelayResearchObjectMetadataPort",
    "research_object_authority_scope",
    "research_object_snapshot_payload",
]

_SNAPSHOT_SCHEMA = "research-object-snapshot/v1"
_METADATA_FAILURE = "Research object metadata could not be verified."
_MAX_RELAY_TEXT_LENGTH = 512
RESEARCH_OBJECT_SNAPSHOT_FIELD_NAMES = (
    "dataset_id",
    "size_bytes",
    "etag",
    "version_id",
    "last_modified",
    "placeholder",
    "snapshot_digest",
)
RESEARCH_OBJECT_SNAPSHOT_FIELDS = frozenset(
    RESEARCH_OBJECT_SNAPSHOT_FIELD_NAMES
)


class _ObsRuntime(Protocol):
    """Minimal process-owned OBS runtime contract used by this port."""

    async def run(
        self,
        profile: ObsProfileName,
        operation: Any,
    ) -> Any:
        """Run one synchronous OBS operation under the runtime lease."""

    async def aclose(self) -> None:
        """Close the process-owned runtime after outstanding work drains."""


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


def research_object_authority_scope(
    objects: Sequence[ResearchObjectCandidate],
) -> str:
    """Digest exact candidate bindings into one provisional grant scope."""
    coordinates = [
        (candidate.dataset_id, candidate.exact_reference)
        for candidate in objects
    ]
    encoded = (
        json.JSONEncoder(
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        .encode(coordinates)
        .encode("utf-8")
    )
    return hashlib.sha256(encoded).hexdigest()


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


def research_object_snapshot_payload(
    snapshot: ResearchObjectSnapshot,
) -> dict[str, object]:
    """Project one immutable snapshot into a transport-safe DTO."""
    return {
        "dataset_id": snapshot.dataset_id,
        "size_bytes": snapshot.size_bytes,
        "etag": snapshot.etag,
        "version_id": snapshot.version_id,
        "last_modified": snapshot.last_modified,
        "placeholder": snapshot.placeholder,
        "snapshot_digest": snapshot.snapshot_digest,
    }


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


class RelayResearchObjectMetadataPort:
    """Implement the shared metadata port through typed relay grant calls."""

    def __init__(self, client: RelayClient) -> None:
        self._client = client

    async def resolve(
        self, request: ResearchObjectResolveRequest
    ) -> tuple[ResearchObjectAuthority, ...]:
        """Resolve exact references without exposing relay response details."""
        try:
            authorities = await self._client.resolve_research_objects(request)
            return _validated_relay_authorities(
                authorities,
                tuple(candidate.dataset_id for candidate in request.objects),
            )
        except ResearchObjectMetadataError:
            raise
        except Exception:
            raise ResearchObjectMetadataError() from None

    async def verify(
        self, request: ResearchObjectVerifyRequest
    ) -> tuple[ResearchObjectAuthority, ...]:
        """Verify snapshots and return rotated opaque grant IDs."""
        try:
            authorities = await self._client.verify_research_objects(request)
            return _validated_relay_authorities(
                authorities,
                tuple(
                    authority.dataset_id for authority in request.authorities
                ),
            )
        except ResearchObjectMetadataError:
            raise
        except Exception:
            raise ResearchObjectMetadataError() from None

    async def revoke(self, request: ResearchObjectRevokeRequest) -> None:
        """Revoke run-bound grants idempotently through the relay."""
        try:
            result = await cast(Any, self._client).revoke_research_objects(
                request
            )
            if result is not None:
                raise ResearchObjectMetadataError()
        except ResearchObjectMetadataError:
            raise
        except Exception:
            raise ResearchObjectMetadataError() from None


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

    def __init__(self, bucket: str, obs_runtime: _ObsRuntime) -> None:
        self._bucket = bucket
        self._obs_runtime = obs_runtime
        self._authorities: dict[str, _AuthorityRecord] = {}

    async def resolve(
        self, request: ResearchObjectResolveRequest
    ) -> tuple[ResearchObjectAuthority, ...]:
        """Resolve request objects with one client and no object-body I/O."""
        try:
            authorities: list[ResearchObjectAuthority] = []
            records: dict[str, _AuthorityRecord] = {}
            for candidate in request.objects:
                object_key = _normalized_object_key(candidate, self._bucket)
                snapshot = await _read_snapshot(
                    candidate.dataset_id,
                    object_key,
                    self._bucket,
                    self._obs_runtime,
                )
                if snapshot.placeholder:
                    raise ResearchObjectMetadataError()
                authority = ResearchObjectAuthority(
                    dataset_id=candidate.dataset_id,
                    authority_id=secrets.token_urlsafe(24),
                    snapshot=snapshot,
                )
                records[authority.authority_id] = _AuthorityRecord(
                    parent_run_id=request.parent_run_id,
                    execution_fingerprint=request.execution_fingerprint,
                    object_key=object_key,
                    authority=authority,
                )
                authorities.append(authority)
        except ResearchObjectMetadataError:
            raise
        except Exception:
            raise ResearchObjectMetadataError() from None
        self._authorities.update(records)
        return tuple(authorities)

    async def verify(
        self, request: ResearchObjectVerifyRequest
    ) -> tuple[ResearchObjectAuthority, ...]:
        """Re-HEAD each private authority and reject changed snapshots."""
        records = tuple(
            self._matching_record(
                authority,
                request.parent_run_id,
                request.execution_fingerprint,
            )
            for authority in request.authorities
        )
        try:
            current: list[ResearchObjectSnapshot] = []
            for record in records:
                current.append(
                    await _read_snapshot(
                        record.authority.dataset_id,
                        record.object_key,
                        self._bucket,
                        self._obs_runtime,
                    )
                )
        except ResearchObjectMetadataError:
            raise
        except Exception:
            raise ResearchObjectMetadataError() from None
        if any(
            snapshot != record.authority.snapshot
            for snapshot, record in zip(current, records)
        ):
            raise ResearchObjectMetadataError()
        return tuple(record.authority for record in records)

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
    if not object_key or (
        candidate.compound_suffix
        and not object_key.casefold().endswith(
            candidate.compound_suffix.casefold()
        )
    ):
        raise ResearchObjectMetadataError()
    return object_key


async def _read_snapshot(
    dataset_id: str,
    object_key: str,
    bucket: str,
    obs_runtime: _ObsRuntime,
) -> ResearchObjectSnapshot:
    """Build one safe snapshot from one separately leased metadata HEAD."""
    try:
        metadata = await obs_runtime.run(
            ObsProfileName.PRIMARY,
            lambda client: head_object_metadata(
                bucket=bucket,
                object_key=object_key,
                client=client,
            ),
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


def _validated_relay_authorities(
    authorities: object, expected_dataset_ids: tuple[str, ...]
) -> tuple[ResearchObjectAuthority, ...]:
    """Validate a typed relay result while retaining request ordering."""
    if not isinstance(authorities, tuple) or len(authorities) != len(
        expected_dataset_ids
    ):
        raise ResearchObjectMetadataError()
    if len(set(expected_dataset_ids)) != len(expected_dataset_ids) or not all(
        _valid_relay_text(value) for value in expected_dataset_ids
    ):
        raise ResearchObjectMetadataError()
    by_dataset: dict[str, ResearchObjectAuthority] = {}
    authority_ids: set[str] = set()
    for authority in authorities:
        if not isinstance(authority, ResearchObjectAuthority):
            raise ResearchObjectMetadataError()
        if not _valid_relay_authority(authority, by_dataset, authority_ids):
            raise ResearchObjectMetadataError()
        by_dataset[authority.dataset_id] = authority
        authority_ids.add(authority.authority_id)
    if set(by_dataset) != set(expected_dataset_ids):
        raise ResearchObjectMetadataError()
    return tuple(by_dataset[dataset_id] for dataset_id in expected_dataset_ids)


def _valid_relay_authority(
    authority: ResearchObjectAuthority,
    by_dataset: dict[str, ResearchObjectAuthority],
    authority_ids: set[str],
) -> bool:
    """Validate one non-placeholder authority and both identity sets."""
    if not _valid_relay_text(authority.dataset_id) or not _valid_relay_text(
        authority.authority_id
    ):
        return False
    if authority.dataset_id in by_dataset:
        return False
    if authority.authority_id in authority_ids:
        return False
    if not isinstance(authority.snapshot, ResearchObjectSnapshot):
        return False
    return _valid_relay_snapshot(authority.dataset_id, authority.snapshot)


def _valid_relay_snapshot(
    dataset_id: str, snapshot: ResearchObjectSnapshot
) -> bool:
    """Validate every scalar in one typed relay snapshot."""
    return (
        snapshot.dataset_id == dataset_id
        and _valid_relay_text(snapshot.dataset_id)
        and isinstance(snapshot.size_bytes, int)
        and not isinstance(snapshot.size_bytes, bool)
        and snapshot.size_bytes >= 0
        and _valid_relay_text(snapshot.snapshot_digest)
        and _valid_relay_text(snapshot.etag, optional=True)
        and _valid_relay_text(snapshot.version_id, optional=True)
        and _valid_relay_text(snapshot.last_modified, optional=True)
        and isinstance(snapshot.placeholder, bool)
        and not snapshot.placeholder
    )


def _valid_relay_text(value: object, *, optional: bool = False) -> bool:
    """Validate bounded non-control text in a relay metadata DTO."""
    if value is None:
        return optional
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= _MAX_RELAY_TEXT_LENGTH
        and all(
            ord(character) >= 32 and ord(character) != 127
            for character in value
        )
    )
