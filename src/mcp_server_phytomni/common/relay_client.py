# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Customer relay client.

Classes: RelayClient.
Functions: build_relay_client.

Lets a relay-mode child Bot reach the operator's upstream relay API:
builds ``/v1/relay/<path>`` URLs, attaches the bearer key (header only,
never logged), and binds each attempt to its final service pool.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlencode

from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData
from pydantic import SecretStr

from ..api.research_capabilities import (
    RESEARCH_RELAY_CAPABILITY_TTL_SECONDS,
    ResearchRelayCapabilities,
)
from ..config.defaults import ServerConfig
from ..config.settings import SensitiveConfig, get_sensitive_config
from ..runtime.outbound import OutboundPoolName, current_outbound_runtime
from ..storage.research_objects import (
    RESEARCH_OBJECT_SNAPSHOT_FIELDS,
    ResearchObjectAuthority,
    ResearchObjectCandidate,
    ResearchObjectResolveRequest,
    ResearchObjectRevokeRequest,
    ResearchObjectSnapshot,
    ResearchObjectVerifyRequest,
    research_object_snapshot_payload,
)
from .http import (
    JsonPostRequest,
    JsonPostRetry,
    post_json_with_retries,
    request_response_with_retries,
)

__all__ = [
    "RelayClient",
    "RelayRequestOptions",
    "ResearchRelayCapabilities",
    "build_relay_client",
    "current_relay_client",
    "is_opaque_relay_text",
]

_RELAY_PREFIX = "v1/relay"
_RESEARCH_PROTOCOL = "research_object_grant_v1"


_RESEARCH_SCHEMA_VERSION = 1
_MAX_RESEARCH_OBJECTS = 256
_MAX_RELAY_RESPONSE_TEXT_LENGTH = 512
_RESEARCH_CAPABILITY_MESSAGE = "relay research capability request failed"
_RESEARCH_GRANT_MESSAGE = "relay research object grant request failed"
_GRANT_FIELDS = frozenset(
    {"dataset_id", "grant_id", "snapshot", "expires_at", "revision"}
)


@dataclass(frozen=True, slots=True)
class RelayRequestOptions:
    """Shared error, header, and timeout options for one relay call.

    Attributes:
        message: Key-free error prefix used when the relay call fails.
        extra_headers: Optional business headers forwarded by the relay.
        request_timeout: Optional per-request timeout override in seconds.
    """

    message: str
    extra_headers: Mapping[str, str] | None = None
    request_timeout: float | None = None


@dataclass(frozen=True)
class RelayClient:
    """Bearer-authenticated client for the upstream relay API.

    Attributes:
        base_url: Relay base URL without a trailing slash.
        api_key: Relay bearer key as a ``SecretStr`` so it is masked in
            repr / model dumps and only revealed when the auth header is
            built.
        timeout: Per-request timeout in seconds.
        max_retries: Maximum retry attempts for retriable failures.
        retriable_codes: HTTP status codes that trigger a retry.
    """

    base_url: str
    api_key: SecretStr
    timeout: float
    max_retries: int
    retriable_codes: tuple[int, ...]

    def relay_url(
        self, relay_path: str, query: Mapping[str, str] | None = None
    ) -> str:
        """Return the absolute relay URL for ``relay_path``.

        Args:
            relay_path: Path under the fixed ``/v1/relay`` prefix, e.g.
                ``"retrieve/search"`` or ``f"analysis/{task_id}"``. A
                leading slash is tolerated.
            query: Optional query parameters to URL-encode onto the URL.

        Returns:
            The fully-qualified relay URL.
        """
        url = f"{self.base_url}/{_RELAY_PREFIX}/{relay_path.lstrip('/')}"
        if query:
            url = f"{url}?{urlencode(dict(query))}"
        return url

    def _auth_headers(
        self, extra: Mapping[str, str] | None = None
    ) -> dict[str, str]:
        """Return the bearer auth header, merged with any extra headers.

        ``extra`` carries business request headers a caller needs the
        relay to forward upstream (e.g. ``X-Workspace-Id`` for NL2SQL);
        the relay strips the caller credential and injects the operator
        one, but forwards other request headers.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key.get_secret_value()}"
        }
        if extra:
            headers.update(extra)
        return headers

    def _retry(
        self, message: str, *, timeout: float | None = None
    ) -> JsonPostRetry:
        """Return the retry policy with an optional per-call timeout."""
        return JsonPostRetry(
            timeout=self.timeout if timeout is None else timeout,
            max_retries=self.max_retries,
            retriable_codes=self.retriable_codes,
            message=message,
        )

    async def _request_json(
        self,
        request: JsonPostRequest,
        message: str,
        *,
        pool: OutboundPoolName,
        request_timeout: float | None = None,
    ) -> Any:
        """Run one relay request through the shared retry/JSON helper.

        ``post_json_with_retries`` honours ``request.method`` (GET as
        well as POST) and returns the parsed JSON body, raising
        ``McpError`` on non-retriable status or retry exhaustion. The
        ``message`` prefix never contains the relay key, so the key
        stays out of logs and raised errors.
        """
        effective_timeout = (
            self.timeout if request_timeout is None else request_timeout
        )
        client = current_outbound_runtime().http.for_pool(pool)
        return await post_json_with_retries(
            client,
            request,
            self._retry(message, timeout=effective_timeout),
        )

    async def post_json(
        self,
        relay_path: str,
        json_body: Any,
        *,
        pool: OutboundPoolName,
        options: RelayRequestOptions,
    ) -> Any:
        """POST ``json_body`` to a relay route and return parsed JSON.

        Args:
            relay_path: Path under the fixed ``/v1/relay`` prefix.
            json_body: JSON-compatible request body.
            pool: Final typed logical pool for this network attempt.
            options: Key-free failure message plus optional business headers
                and per-request timeout.

        Returns:
            The parsed relay JSON response.

        Raises:
            McpError: If the request fails or returns invalid JSON.
        """
        request = JsonPostRequest(
            url=self.relay_url(relay_path),
            method="POST",
            headers=self._auth_headers(options.extra_headers),
            json_body=json_body,
        )
        return await self._request_json(
            request,
            options.message,
            pool=pool,
            request_timeout=options.request_timeout,
        )

    async def get_json(
        self,
        relay_path: str,
        *,
        pool: OutboundPoolName,
        options: RelayRequestOptions,
        query: Mapping[str, str] | None = None,
    ) -> Any:
        """GET a relay route and parse JSON.

        Args:
            relay_path: Path under the fixed ``/v1/relay`` prefix.
            pool: Final typed logical pool for this network attempt.
            options: Key-free failure message plus optional business headers
                and per-request timeout.
            query: Optional query parameters encoded onto the relay URL.

        Returns:
            The parsed relay JSON response.

        Raises:
            McpError: If the request fails or returns invalid JSON.
        """
        request = JsonPostRequest(
            url=self.relay_url(relay_path, query),
            method="GET",
            headers=self._auth_headers(options.extra_headers),
        )
        return await self._request_json(
            request,
            options.message,
            pool=pool,
            request_timeout=options.request_timeout,
        )

    async def get_research_capabilities(self) -> ResearchRelayCapabilities:
        """Fetch and strictly decode the scoped Research relay capability."""
        payload = await self.get_json(
            "capabilities",
            pool=OutboundPoolName.RELAY_CONTROL,
            options=RelayRequestOptions(message=_RESEARCH_CAPABILITY_MESSAGE),
        )
        try:
            return _decode_research_capabilities(payload)
        except (TypeError, ValueError, KeyError):
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=_RESEARCH_CAPABILITY_MESSAGE,
                )
            ) from None

    async def resolve_research_objects(
        self, request: ResearchObjectResolveRequest
    ) -> tuple[ResearchObjectAuthority, ...]:
        """Resolve exact-key Research objects into opaque relay grants."""
        candidates = _resolve_candidates(request)
        payload = {
            "schema_version": _RESEARCH_SCHEMA_VERSION,
            "parent_run_id": request.parent_run_id,
            "execution_fingerprint": request.execution_fingerprint,
            "objects": [
                {
                    "dataset_id": candidate.dataset_id,
                    "exact_reference": candidate.exact_reference,
                }
                for candidate in candidates
            ],
        }
        response = await self.post_json(
            "research-input/object-grants",
            payload,
            pool=OutboundPoolName.RELAY_CONTROL,
            options=RelayRequestOptions(message=_RESEARCH_GRANT_MESSAGE),
        )
        try:
            grants = _decode_grants(response, candidates)
            return tuple(
                ResearchObjectAuthority(
                    dataset_id=cast(str, grant["dataset_id"]),
                    authority_id=cast(str, grant["grant_id"]),
                    snapshot=cast(ResearchObjectSnapshot, grant["snapshot"]),
                )
                for grant in grants
            )
        except (TypeError, ValueError, KeyError):
            raise McpError(
                ErrorData(code=INTERNAL_ERROR, message=_RESEARCH_GRANT_MESSAGE)
            ) from None

    async def verify_research_objects(
        self, request: ResearchObjectVerifyRequest
    ) -> tuple[ResearchObjectAuthority, ...]:
        """Verify Research snapshots and return any rotated grant IDs."""
        authorities = _verify_authorities(request)
        payload = {
            "schema_version": _RESEARCH_SCHEMA_VERSION,
            "parent_run_id": request.parent_run_id,
            "execution_fingerprint": request.execution_fingerprint,
            "grants": [
                {
                    "dataset_id": authority.dataset_id,
                    "grant_id": authority.authority_id,
                    "expected_snapshot": research_object_snapshot_payload(
                        authority.snapshot
                    ),
                }
                for authority in authorities
            ],
        }
        response = await self.post_json(
            "research-input/object-grants/verify",
            payload,
            pool=OutboundPoolName.RELAY_CONTROL,
            options=RelayRequestOptions(message=_RESEARCH_GRANT_MESSAGE),
        )
        try:
            grants = _decode_grants(response, authorities)
            for authority, grant in zip(authorities, grants, strict=True):
                if grant["snapshot"] != authority.snapshot:
                    raise ValueError
            return tuple(
                ResearchObjectAuthority(
                    dataset_id=cast(str, grant["dataset_id"]),
                    authority_id=cast(str, grant["grant_id"]),
                    snapshot=cast(ResearchObjectSnapshot, grant["snapshot"]),
                )
                for grant in grants
            )
        except (TypeError, ValueError, KeyError):
            raise McpError(
                ErrorData(code=INTERNAL_ERROR, message=_RESEARCH_GRANT_MESSAGE)
            ) from None

    async def revoke_research_objects(
        self, request: ResearchObjectRevokeRequest
    ) -> None:
        """Idempotently revoke run-bound Research grants."""
        authority_ids = _revoke_authority_ids(request)
        response = await self.post_json(
            "research-input/object-grants/revoke",
            {
                "schema_version": _RESEARCH_SCHEMA_VERSION,
                "parent_run_id": request.parent_run_id,
                "execution_fingerprint": request.execution_fingerprint,
                "grant_ids": list(authority_ids),
            },
            pool=OutboundPoolName.RELAY_CONTROL,
            options=RelayRequestOptions(message=_RESEARCH_GRANT_MESSAGE),
        )
        try:
            if (
                not isinstance(response, dict)
                or set(response) != {"revoked"}
                or not isinstance(response["revoked"], int)
                or isinstance(response["revoked"], bool)
                or response["revoked"] < 0
            ):
                raise ValueError
        except (TypeError, ValueError, KeyError):
            raise McpError(
                ErrorData(code=INTERNAL_ERROR, message=_RESEARCH_GRANT_MESSAGE)
            ) from None

    async def put_obs_object(
        self, obs_path: str, content: bytes, *, message: str
    ) -> Any:
        """PUT raw object bytes to the OBS upload relay, parse JSON.

        Sends the validated client OBS path as the ``path`` query and the
        object bytes as the request body to ``PUT /v1/relay/obs/object``;
        the operator relay terminates the upload server-side with its own
        OBS credentials and returns the stored ``obs_path``.
        """
        request = JsonPostRequest(
            url=self.relay_url("obs/object", {"path": obs_path}),
            method="PUT",
            headers=self._auth_headers(),
            data=content,
        )
        return await self._request_json(
            request,
            message,
            pool=OutboundPoolName.OBS,
        )

    async def _request_bytes(
        self, request: JsonPostRequest, message: str
    ) -> bytes:
        """Run one relay request and return the raw response bytes.

        Used for the OBS download relay, whose body is the object's raw
        bytes rather than JSON; the shared retry helper still maps a
        non-retriable status / exhaustion to a key-free ``McpError``.
        """
        client = current_outbound_runtime().http.for_pool(OutboundPoolName.OBS)
        response = await request_response_with_retries(
            client, request, self._retry(message)
        )
        return response.content

    async def get_obs_object(self, obs_path: str, *, message: str) -> bytes:
        """GET one object's raw bytes from the OBS download relay.

        Sends the validated client OBS path as the ``path`` query to
        ``GET /v1/relay/obs/object``; the operator relay reads the object
        server-side with its own credentials and streams the bytes back.
        """
        request = JsonPostRequest(
            url=self.relay_url("obs/object", {"path": obs_path}),
            method="GET",
            headers=self._auth_headers(),
        )
        return await self._request_bytes(request, message)

    async def get_obs_object_to_path(
        self, obs_path: str, destination: Path, *, message: str
    ) -> None:
        """Stream one OBS object through the relay straight to a file.

        Avoids buffering the whole object in memory: the operator relay
        streams the bytes and the child writes each chunk to disk. Raises
        a key-free ``McpError`` on a non-2xx status before any file write.
        """
        url = self.relay_url("obs/object", {"path": obs_path})
        http_runtime = current_outbound_runtime().http
        async with http_runtime.stream(
            OutboundPoolName.OBS,
            "GET",
            url,
            headers=self._auth_headers(),
            timeout=self.timeout,
        ) as response:
            if response.status_code >= 400:
                raise McpError(ErrorData(code=INTERNAL_ERROR, message=message))
            destination_opened = False
            download_complete = False
            try:
                with destination.open("wb") as sink:
                    destination_opened = True
                    async for chunk in response.aiter_bytes():
                        sink.write(chunk)
                download_complete = True
            finally:
                if destination_opened and not download_complete:
                    with suppress(OSError):
                        destination.unlink(missing_ok=True)

    async def get_obs_list(
        self, obs_prefix: str, *, message: str
    ) -> list[str]:
        """List object keys under an output-root prefix via the relay.

        ``GET /v1/relay/obs/list?prefix=`` returns ``{"keys": [...]}``; the
        relay rejects a prefix outside the server-owned output root (403).
        """
        result = await self.get_json(
            "obs/list",
            pool=OutboundPoolName.OBS,
            options=RelayRequestOptions(message=message),
            query={"prefix": obs_prefix},
        )
        keys = result.get("keys") if isinstance(result, dict) else None
        return list(keys) if isinstance(keys, list) else []

    async def put_obs_dir(self, obs_path: str, *, message: str) -> Any:
        """Create a zero-byte directory marker via the OBS relay.

        ``PUT /v1/relay/obs/dir?path=`` makes the operator relay mint the
        output-dir marker server-side and return the stored ``obs_path``.
        """
        request = JsonPostRequest(
            url=self.relay_url("obs/dir", {"path": obs_path}),
            method="PUT",
            headers=self._auth_headers(),
        )
        return await self._request_json(
            request,
            message,
            pool=OutboundPoolName.OBS,
        )


def _decode_research_capabilities(
    payload: object,
) -> ResearchRelayCapabilities:
    """Decode the bounded operator capability without trusting extra data."""
    if not isinstance(payload, dict):
        raise TypeError
    allowed_top_level = {
        "protocols",
        "research_object_grant",
        "authorized_scope",
    }
    if not set(payload).issubset(allowed_top_level):
        raise ValueError
    protocols = payload.get("protocols")
    descriptor = payload.get("research_object_grant")
    if (
        not isinstance(protocols, dict)
        or set(protocols) != {_RESEARCH_PROTOCOL}
        or not isinstance(descriptor, dict)
        or not set(descriptor).issubset({"max_objects", "authorized_scope"})
        or "max_objects" not in descriptor
    ):
        raise ValueError
    versions = protocols[_RESEARCH_PROTOCOL]
    if (
        not isinstance(versions, list)
        or not versions
        or len(versions) > 8
        or any(
            not isinstance(version, int) or isinstance(version, bool)
            for version in versions
        )
        or len(set(versions)) != len(versions)
    ):
        raise ValueError
    max_objects = descriptor["max_objects"]
    if (
        not isinstance(max_objects, int)
        or isinstance(max_objects, bool)
        or not 1 <= max_objects <= _MAX_RESEARCH_OBJECTS
    ):
        raise ValueError
    top_scope = payload.get("authorized_scope")
    descriptor_scope = descriptor.get("authorized_scope")
    if (
        top_scope is not None
        and descriptor_scope is not None
        and top_scope != descriptor_scope
    ):
        raise ValueError
    scope = top_scope or descriptor_scope or "relay:research-input"
    if scope not in {"relay:research-input", "relay:*"}:
        raise ValueError
    obtained_at = datetime.now(UTC)
    return ResearchRelayCapabilities(
        protocol_versions=tuple(versions),
        max_objects=max_objects,
        authorized_scope=cast(
            Literal["relay:research-input", "relay:*"], scope
        ),
        obtained_at=obtained_at,
        expires_at=obtained_at
        + timedelta(seconds=RESEARCH_RELAY_CAPABILITY_TTL_SECONDS),
    )


def _decode_grants(
    payload: object,
    expected: (
        tuple[ResearchObjectCandidate, ...]
        | tuple[ResearchObjectAuthority, ...]
    ),
) -> tuple[dict[str, object], ...]:
    """Decode grant records and restore the caller's request ordering."""
    if not isinstance(payload, dict) or set(payload) != {"grants"}:
        raise ValueError
    raw_grants = payload["grants"]
    if not isinstance(raw_grants, list) or len(raw_grants) != len(expected):
        raise ValueError
    expected_ids = tuple(item.dataset_id for item in expected)
    if len(set(expected_ids)) != len(expected_ids):
        raise ValueError
    by_dataset: dict[str, dict[str, object]] = {}
    grant_ids: set[str] = set()
    for raw_grant in raw_grants:
        if not isinstance(raw_grant, dict) or set(raw_grant) != _GRANT_FIELDS:
            raise ValueError
        dataset_id = raw_grant["dataset_id"]
        grant_id = raw_grant["grant_id"]
        expires_at = raw_grant["expires_at"]
        revision = raw_grant["revision"]
        if not _valid_grant_fields(
            dataset_id, grant_id, expires_at, revision, by_dataset
        ):
            raise ValueError
        if grant_id in grant_ids:
            raise ValueError
        parsed_expiry = datetime.fromisoformat(expires_at)
        if parsed_expiry.tzinfo is None or parsed_expiry.utcoffset() is None:
            raise ValueError
        snapshot = _decode_snapshot(raw_grant["snapshot"])
        if snapshot.dataset_id != dataset_id or snapshot.placeholder:
            raise ValueError
        by_dataset[dataset_id] = {
            "dataset_id": dataset_id,
            "grant_id": grant_id,
            "snapshot": snapshot,
        }
        grant_ids.add(grant_id)
    if set(by_dataset) != set(expected_ids):
        raise ValueError
    return tuple(by_dataset[dataset_id] for dataset_id in expected_ids)


def _decode_snapshot(payload: object) -> ResearchObjectSnapshot:
    """Decode an immutable metadata snapshot from an untrusted response."""
    if (
        not isinstance(payload, dict)
        or set(payload) != RESEARCH_OBJECT_SNAPSHOT_FIELDS
    ):
        raise ValueError
    dataset_id = payload["dataset_id"]
    size_bytes = payload["size_bytes"]
    etag = payload["etag"]
    version_id = payload["version_id"]
    last_modified = payload["last_modified"]
    placeholder = payload["placeholder"]
    snapshot_digest = payload["snapshot_digest"]
    if not _valid_snapshot_fields(
        (
            dataset_id,
            size_bytes,
            etag,
            version_id,
            last_modified,
            placeholder,
            snapshot_digest,
        )
    ):
        raise ValueError
    return ResearchObjectSnapshot(
        dataset_id=dataset_id,
        size_bytes=size_bytes,
        etag=etag,
        version_id=version_id,
        last_modified=last_modified,
        placeholder=placeholder,
        snapshot_digest=snapshot_digest,
    )


def _valid_grant_fields(
    dataset_id: object,
    grant_id: object,
    expires_at: object,
    revision: object,
    existing: Mapping[str, object],
) -> bool:
    """Validate the scalar fields and uniqueness of one grant DTO."""
    return (
        _valid_response_text(dataset_id)
        and _valid_response_text(grant_id)
        and dataset_id not in existing
        and isinstance(expires_at, str)
        and isinstance(revision, int)
        and not isinstance(revision, bool)
        and revision >= 0
    )


def _valid_snapshot_fields(fields: tuple[object, ...]) -> bool:
    """Validate scalar fields of one immutable snapshot DTO."""
    if len(fields) != 7:
        return False
    (
        dataset_id,
        size_bytes,
        etag,
        version_id,
        last_modified,
        placeholder,
        snapshot_digest,
    ) = fields
    return (
        _valid_response_text(dataset_id)
        and isinstance(size_bytes, int)
        and not isinstance(size_bytes, bool)
        and size_bytes >= 0
        and all(
            _valid_response_text(value, optional=True, allow_empty=True)
            for value in (etag, version_id, last_modified)
        )
        and isinstance(placeholder, bool)
        and _valid_response_text(snapshot_digest)
    )


def _valid_response_text(
    value: object, *, optional: bool = False, allow_empty: bool = False
) -> bool:
    """Validate bounded non-control text in a relay response DTO."""
    if value is None:
        return optional
    return (
        isinstance(value, str)
        and (allow_empty or bool(value))
        and len(value) <= _MAX_RELAY_RESPONSE_TEXT_LENGTH
        and all(
            ord(character) >= 32 and ord(character) != 127
            for character in value
        )
    )


def _resolve_candidates(
    request: ResearchObjectResolveRequest,
) -> tuple[ResearchObjectCandidate, ...]:
    """Validate resolve inputs before serializing opaque references."""
    if (
        not isinstance(request, ResearchObjectResolveRequest)
        or not isinstance(request.objects, tuple)
        or not request.objects
        or not is_opaque_relay_text(request.parent_run_id)
        or not is_opaque_relay_text(request.execution_fingerprint)
    ):
        raise McpError(
            ErrorData(code=INTERNAL_ERROR, message=_RESEARCH_GRANT_MESSAGE)
        )
    candidates: list[ResearchObjectCandidate] = []
    dataset_ids: set[str] = set()
    for candidate in request.objects:
        if not isinstance(candidate, ResearchObjectCandidate):
            raise McpError(
                ErrorData(code=INTERNAL_ERROR, message=_RESEARCH_GRANT_MESSAGE)
            )
        if (
            not all(
                is_opaque_relay_text(value)
                for value in (
                    candidate.dataset_id,
                    candidate.exact_reference,
                    candidate.compound_suffix,
                )
            )
            or candidate.dataset_id in dataset_ids
        ):
            raise McpError(
                ErrorData(code=INTERNAL_ERROR, message=_RESEARCH_GRANT_MESSAGE)
            )
        dataset_ids.add(candidate.dataset_id)
        candidates.append(candidate)
    if len(candidates) > _MAX_RESEARCH_OBJECTS:
        raise McpError(
            ErrorData(code=INTERNAL_ERROR, message=_RESEARCH_GRANT_MESSAGE)
        )
    return tuple(candidates)


def _verify_authorities(
    request: ResearchObjectVerifyRequest,
) -> tuple[ResearchObjectAuthority, ...]:
    """Validate verify inputs before serializing grant IDs/snapshots."""
    if (
        not isinstance(request, ResearchObjectVerifyRequest)
        or not isinstance(request.authorities, tuple)
        or not request.authorities
        or not is_opaque_relay_text(request.parent_run_id)
        or not is_opaque_relay_text(request.execution_fingerprint)
    ):
        raise McpError(
            ErrorData(code=INTERNAL_ERROR, message=_RESEARCH_GRANT_MESSAGE)
        )
    authorities: list[ResearchObjectAuthority] = []
    authority_ids: set[str] = set()
    for authority in request.authorities:
        if not _valid_verify_authority(authority, authority_ids):
            raise McpError(
                ErrorData(code=INTERNAL_ERROR, message=_RESEARCH_GRANT_MESSAGE)
            )
        authority_ids.add(authority.authority_id)
        authorities.append(authority)
    if len(authorities) > _MAX_RESEARCH_OBJECTS:
        raise McpError(
            ErrorData(code=INTERNAL_ERROR, message=_RESEARCH_GRANT_MESSAGE)
        )
    return tuple(authorities)


def _revoke_authority_ids(
    request: ResearchObjectRevokeRequest,
) -> tuple[str, ...]:
    """Validate revoke IDs before sending the idempotent request."""
    if not _valid_revoke_request(request):
        raise McpError(
            ErrorData(code=INTERNAL_ERROR, message=_RESEARCH_GRANT_MESSAGE)
        )
    return request.authority_ids


def _valid_verify_authority(authority: object, existing_ids: set[str]) -> bool:
    """Validate one verify authority and its immutable snapshot."""
    if not isinstance(authority, ResearchObjectAuthority):
        return False
    valid = is_opaque_relay_text(
        authority.dataset_id
    ) and is_opaque_relay_text(authority.authority_id)
    if valid:
        valid = authority.authority_id not in existing_ids
    if not isinstance(authority.snapshot, ResearchObjectSnapshot):
        return False
    snapshot = authority.snapshot
    if valid:
        valid = (
            snapshot.dataset_id == authority.dataset_id
            and not snapshot.placeholder
        )
    if valid:
        try:
            _decode_snapshot(research_object_snapshot_payload(snapshot))
        except ValueError:
            valid = False
    return valid


def _valid_revoke_request(request: object) -> bool:
    """Validate one opaque grant-revoke request."""
    if not isinstance(request, ResearchObjectRevokeRequest):
        return False
    if (
        not isinstance(request.authority_ids, tuple)
        or not request.authority_ids
    ):
        return False
    if not is_opaque_relay_text(
        request.parent_run_id
    ) or not is_opaque_relay_text(request.execution_fingerprint):
        return False
    return (
        all(is_opaque_relay_text(value) for value in request.authority_ids)
        and len(set(request.authority_ids)) == len(request.authority_ids)
        and len(request.authority_ids) <= _MAX_RESEARCH_OBJECTS
    )


def is_opaque_relay_text(value: object) -> bool:
    """Return whether one relay binding is bounded non-control text."""
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 4096
        and all(
            ord(character) >= 32 and ord(character) != 127
            for character in value
        )
    )


def build_relay_client(
    config: ServerConfig, sensitive: SensitiveConfig
) -> RelayClient:
    """Construct a ``RelayClient`` from the relay config and secret.

    Args:
        config: Any ``ServerConfig`` (or subclass) carrying the relay
            ``RELAY_BASE_URL`` plus the shared timeout / retry policy.
        sensitive: The ``SensitiveConfig`` carrying ``RELAY_API_KEY``.

    Returns:
        A ready ``RelayClient``.
    """
    return RelayClient(
        base_url=config.RELAY_BASE_URL,
        api_key=sensitive.RELAY_API_KEY,
        timeout=config.TIMEOUT,
        max_retries=config.MAX_RETRIES,
        retriable_codes=tuple(config.RETRIABLE_CODES),
    )


def current_relay_client() -> RelayClient:
    """Build a ``RelayClient`` from the live relay config and secret.

    The zero-arg seam for relay-mode HTTP boundaries that have no config
    object in scope (knowledge / data / deep_genome / evolution /
    task-manager adapters). Reads a fresh ``ServerConfig()`` so
    ``RELAY_BASE_URL`` reflects the current environment, and the
    process-cached ``SensitiveConfig`` for ``RELAY_API_KEY``.

    Returns:
        A ``RelayClient`` carrying the live relay base URL, key, and the
        shared timeout / retry policy.
    """
    return build_relay_client(ServerConfig(), get_sensitive_config())
