# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Contract tests for scoped Research input relay grant routes."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI
from tests.support.http_fakes import open_asgi_client

from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.api.relay import research_grants, research_input
from mcp_server_phytomni.api.relay.audit import RelayAuditStore
from mcp_server_phytomni.api.relay.research_grants import (
    ResearchGrantStore,
)
from mcp_server_phytomni.api.relay.routes import create_relay_router
from mcp_server_phytomni.storage.research_objects import (
    ResearchObjectAuthority,
    ResearchObjectMetadataError,
    ResearchObjectResolveRequest,
    ResearchObjectRevokeRequest,
    ResearchObjectSnapshot,
    ResearchObjectVerifyRequest,
)

pytestmark = pytest.mark.server

_REFERENCE = "obs://phytomni/research/leaf.tsv"
_PARENT_RUN_ID = "parent-run-001"
_EXECUTION_FINGERPRINT = "execution-fingerprint-001"
_NO_METADATA_OVERRIDE = object()


@dataclass(slots=True)
class _FakeMetadataPort:
    """Head-only metadata seam with explicitly forbidden object operations."""

    calls: list[tuple[str, str]] = field(default_factory=list)
    changed_references: set[str] = field(default_factory=set)
    resolve_override: object = _NO_METADATA_OVERRIDE
    verify_override: object = _NO_METADATA_OVERRIDE
    _records: dict[str, tuple[str, str, str, ResearchObjectAuthority]] = field(
        default_factory=dict
    )
    _sequence: int = 0

    async def resolve(self, request: ResearchObjectResolveRequest) -> Any:
        """Return one opaque authority per exact, non-placeholder object."""
        if self.resolve_override is not _NO_METADATA_OVERRIDE:
            return self.resolve_override
        resolved: list[ResearchObjectAuthority] = []
        for candidate in request.objects:
            self.calls.append(("head", candidate.exact_reference))
            if any(
                marker in candidate.exact_reference
                for marker in ("missing", "placeholder")
            ):
                raise ResearchObjectMetadataError()
            self._sequence += 1
            snapshot = _snapshot(candidate.dataset_id)
            authority = ResearchObjectAuthority(
                dataset_id=candidate.dataset_id,
                authority_id=f"source-authority-{self._sequence}",
                snapshot=snapshot,
            )
            self._records[authority.authority_id] = (
                request.parent_run_id,
                request.execution_fingerprint,
                candidate.exact_reference,
                authority,
            )
            resolved.append(authority)
        return tuple(resolved)

    async def verify(self, request: ResearchObjectVerifyRequest) -> Any:
        """Re-head only a source authority belonging to its original run."""
        if self.verify_override is not _NO_METADATA_OVERRIDE:
            return self.verify_override
        verified: list[ResearchObjectAuthority] = []
        for authority in request.authorities:
            record = self._records.get(authority.authority_id)
            if (
                record is None
                or record[0] != request.parent_run_id
                or record[1] != request.execution_fingerprint
                or record[3] != authority
                or record[2] in self.changed_references
            ):
                raise ResearchObjectMetadataError()
            self.calls.append(("head", record[2]))
            verified.append(authority)
        return tuple(verified)

    async def revoke(self, request: ResearchObjectRevokeRequest) -> None:
        """Drop only matching private source authorities."""
        for authority_id in request.authority_ids:
            record = self._records.get(authority_id)
            if (
                record is not None
                and record[0] == request.parent_run_id
                and record[1] == request.execution_fingerprint
            ):
                self._records.pop(authority_id)

    def recorded_authorities(self) -> tuple[ResearchObjectAuthority, ...]:
        """Expose source authorities without private test-member access."""
        return tuple(record[3] for record in self._records.values())


def _snapshot(dataset_id: str) -> ResearchObjectSnapshot:
    """Build one deterministic safe metadata snapshot."""
    digest = hashlib.sha256(dataset_id.encode("utf-8")).hexdigest()
    return ResearchObjectSnapshot(
        dataset_id=dataset_id,
        size_bytes=73,
        etag="etag-73",
        version_id="version-73",
        last_modified="2026-08-08T00:00:00+00:00",
        placeholder=False,
        snapshot_digest=digest,
    )


def _request_payload(
    reference: str = _REFERENCE,
    *,
    dataset_id: str = "dataset-001",
) -> dict[str, Any]:
    """Build one canonical object-grant resolve payload."""
    return {
        "schema_version": 1,
        "parent_run_id": _PARENT_RUN_ID,
        "execution_fingerprint": _EXECUTION_FINGERPRINT,
        "objects": [{"dataset_id": dataset_id, "exact_reference": reference}],
    }


def _verify_payload(grant: dict[str, Any]) -> dict[str, Any]:
    """Build one bound verify request from a safe grant response."""
    return {
        "schema_version": 1,
        "parent_run_id": _PARENT_RUN_ID,
        "execution_fingerprint": _EXECUTION_FINGERPRINT,
        "grants": [
            {
                "dataset_id": grant["dataset_id"],
                "grant_id": grant["grant_id"],
                "expected_snapshot": grant["snapshot"],
            }
        ],
    }


def _revoke_payload(grant_id: str) -> dict[str, Any]:
    """Build one bound idempotent revoke request."""
    return {
        "schema_version": 1,
        "parent_run_id": _PARENT_RUN_ID,
        "execution_fingerprint": _EXECUTION_FINGERPRINT,
        "grant_ids": [grant_id],
    }


@pytest.fixture(name="metadata_port")
def _metadata_port_fixture() -> _FakeMetadataPort:
    """Provide an injected metadata port that records HEAD calls only."""
    return _FakeMetadataPort()


@pytest.fixture(name="grant_database")
def _grant_database_fixture(tmp_path: Path) -> Path:
    """Return an isolated durable grant-store database path."""
    return tmp_path / "research_grants.sqlite"


@pytest.fixture(name="relay_key")
def _relay_key_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., str]:
    """Mint keys for exact, wildcard, unrelated, and empty scope tests."""
    database = tmp_path / "keys.sqlite"
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(database))
    monkeypatch.setenv("PHYTOMNI_RELAY_ENABLED", "1")
    store = ApiKeyStore(str(database))

    def _mint(*scopes: str, user_id: str = "research-user") -> str:
        created = store.create(
            user_id=user_id,
            scopes=list(scopes) if scopes else None,
        )
        return created.api_key

    return _mint


@pytest.fixture(name="client")
async def _client_fixture(
    grant_database: Path,
    metadata_port: _FakeMetadataPort,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    """Mount production routes with only the safe storage seams injected."""
    audit_database = grant_database.with_name("relay_audit.sqlite")
    monkeypatch.setenv("PHYTOMNI_RELAY_AUDIT_DB_PATH", str(audit_database))
    app = FastAPI()
    app.include_router(create_relay_router())

    async def _metadata_dependency() -> _FakeMetadataPort:
        """Inject the route's isolated HEAD-only metadata authority."""
        return metadata_port

    async def _store_dependency() -> ResearchGrantStore:
        """Inject the route's isolated durable grant store."""
        return ResearchGrantStore(str(grant_database))

    app.dependency_overrides[
        research_input.get_research_object_metadata_port
    ] = _metadata_dependency
    app.dependency_overrides[research_input.get_research_grant_store] = (
        _store_dependency
    )
    async with open_asgi_client(
        monkeypatch, app, base_url="http://relay.test"
    ) as test_client:
        yield test_client


@pytest.mark.parametrize(
    "scopes",
    [(), ("relay:obs",), ("relay:llm",)],
)
async def test_research_input_capability_denies_nonmatching_scopes(
    client: httpx.AsyncClient,
    relay_key: Callable[..., str],
    scopes: tuple[str, ...],
) -> None:
    """Scope-less, OBS-only, and unrelated relay keys cannot probe grants."""
    response = await client.get(
        "/v1/relay/capabilities",
        headers={"Authorization": f"Bearer {relay_key(*scopes)}"},
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    "scope",
    ["relay:research-input", "relay:*"],
)
async def test_research_input_capability_is_sanitized_and_scoped(
    client: httpx.AsyncClient,
    relay_key: Callable[..., str],
    scope: str,
) -> None:
    """Exact and wildcard scopes see only the public protocol descriptor."""
    response = await client.get(
        "/v1/relay/capabilities",
        headers={"Authorization": f"Bearer {relay_key(scope)}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "protocols": {"research_object_grant_v1": [1]},
        "research_object_grant": {"max_objects": 256},
    }


@pytest.mark.parametrize("scope", ["relay:research-input", "relay:*"])
async def test_resolve_replays_grants_after_exact_head_and_audits_metadata(
    client: httpx.AsyncClient,
    metadata_port: _FakeMetadataPort,
    relay_key: Callable[..., str],
    grant_database: Path,
    scope: str,
) -> None:
    """Resolve HEADs exact keys, replays durable grants, and hides paths."""
    headers = {"Authorization": f"Bearer {relay_key(scope)}"}
    payload = _request_payload()

    first = await client.post(
        "/v1/relay/research-input/object-grants",
        headers=headers,
        json=payload,
    )
    second = await client.post(
        "/v1/relay/research-input/object-grants",
        headers=headers,
        json=payload,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    first_grant = first.json()["grants"][0]
    second_grant = second.json()["grants"][0]
    assert first_grant["grant_id"] == second_grant["grant_id"]
    assert first_grant["dataset_id"] == "dataset-001"
    assert first_grant["snapshot"]["snapshot_digest"]
    assert _REFERENCE not in first.text
    assert metadata_port.calls == [("head", _REFERENCE)] * 2

    rows = RelayAuditStore(
        str(grant_database.with_name("relay_audit.sqlite"))
    ).query()
    row = next(
        row for row in rows if row.operation == "research_grant_resolve"
    )
    assert row.request_body is not None
    assert _REFERENCE not in row.request_body
    assert _PARENT_RUN_ID not in row.request_body
    assert _EXECUTION_FINGERPRINT not in row.request_body
    assert first_grant["grant_id"] not in row.request_body
    metadata = json.loads(row.request_body)
    assert metadata["count"] == 1
    assert len(metadata["dataset_digests"][0]) == 64
    assert metadata["grant_digests"] == []


@pytest.mark.parametrize(
    "reference",
    [
        "obs://phytomni/research/missing.tsv",
        "obs://phytomni/research/placeholder.tsv",
        "obs://other-bucket/research/leaf.tsv",
        "obs://phytomni/research/leaf.unsupported",
    ],
)
async def test_resolve_rejects_untrusted_metadata_or_reference_format(
    client: httpx.AsyncClient,
    metadata_port: _FakeMetadataPort,
    relay_key: Callable[..., str],
    reference: str,
) -> None:
    """Missing, placeholder, foreign-bucket, and bad formats fail closed."""
    response = await client.post(
        "/v1/relay/research-input/object-grants",
        headers={
            "Authorization": f"Bearer {relay_key('relay:research-input')}"
        },
        json=_request_payload(reference),
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "research object grant could not be verified"
    }
    if reference.endswith("unsupported") or "other-bucket" in reference:
        assert metadata_port.calls == []


async def test_resolve_rejects_extra_fields_and_over_limit_before_heads(
    client: httpx.AsyncClient,
    metadata_port: _FakeMetadataPort,
    relay_key: Callable[..., str],
) -> None:
    """The route rejects forbidden DTO fields and a 257th object locally."""
    headers = {"Authorization": f"Bearer {relay_key('relay:research-input')}"}
    extra = _request_payload()
    extra["unexpected"] = "nope"
    extra_response = await client.post(
        "/v1/relay/research-input/object-grants",
        headers=headers,
        json=extra,
    )
    objects = [
        {
            "dataset_id": f"dataset-{index:03d}",
            "exact_reference": f"obs://phytomni/research/{index:03d}.tsv",
        }
        for index in range(257)
    ]
    over_limit = _request_payload()
    over_limit["objects"] = objects
    limit_response = await client.post(
        "/v1/relay/research-input/object-grants",
        headers=headers,
        json=over_limit,
    )

    assert extra_response.status_code == 400
    assert limit_response.status_code == 400
    assert metadata_port.calls == []


async def test_resolve_accepts_the_256_object_boundary(
    client: httpx.AsyncClient,
    metadata_port: _FakeMetadataPort,
    relay_key: Callable[..., str],
) -> None:
    """The operator maximum is inclusive and only resolves exact keys."""
    payload = _request_payload()
    payload["objects"] = [
        {
            "dataset_id": f"dataset-{index:03d}",
            "exact_reference": f"obs://phytomni/research/{index:03d}.tsv",
        }
        for index in range(256)
    ]

    response = await client.post(
        "/v1/relay/research-input/object-grants",
        headers={
            "Authorization": f"Bearer {relay_key('relay:research-input')}"
        },
        json=payload,
    )

    assert response.status_code == 200
    assert len(response.json()["grants"]) == 256
    assert len(metadata_port.calls) == 256
    assert {operation for operation, _ in metadata_port.calls} == {"head"}


@pytest.mark.parametrize(
    "malformed_result",
    [
        None,
        (cast(ResearchObjectAuthority, object()),),
        (
            ResearchObjectAuthority(
                dataset_id="dataset-001",
                authority_id="source-authority-malformed",
                snapshot=cast(ResearchObjectSnapshot, object()),
            ),
        ),
    ],
    ids=["not-a-tuple", "not-an-authority", "not-a-snapshot"],
)
async def test_resolve_rejects_malformed_metadata_port_authorities(
    client: httpx.AsyncClient,
    metadata_port: _FakeMetadataPort,
    relay_key: Callable[..., str],
    malformed_result: object,
) -> None:
    """Malformed exact-key authority output remains a fixed safe failure."""
    metadata_port.resolve_override = malformed_result

    response = await client.post(
        "/v1/relay/research-input/object-grants",
        headers={
            "Authorization": f"Bearer {relay_key('relay:research-input')}"
        },
        json=_request_payload(),
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "research object grant could not be verified"
    }


async def test_verify_rejects_non_tuple_metadata_port_result(
    client: httpx.AsyncClient,
    metadata_port: _FakeMetadataPort,
    relay_key: Callable[..., str],
) -> None:
    """Verification accepts only the metadata port's exact authority tuple."""
    headers = {"Authorization": f"Bearer {relay_key('relay:research-input')}"}
    resolved = await client.post(
        "/v1/relay/research-input/object-grants",
        headers=headers,
        json=_request_payload(),
    )
    grant = resolved.json()["grants"][0]
    metadata_port.verify_override = [metadata_port.recorded_authorities()[0]]

    response = await client.post(
        "/v1/relay/research-input/object-grants/verify",
        headers=headers,
        json=_verify_payload(grant),
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "research object grant could not be verified"
    }


async def test_verify_rotates_grant_after_rehead(
    client: httpx.AsyncClient,
    metadata_port: _FakeMetadataPort,
    relay_key: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify re-HEADs the private authority and safely projects rotation."""
    monkeypatch.setattr(
        research_grants,
        "RESEARCH_GRANT_ROTATE_BEFORE",
        timedelta(hours=4),
    )
    headers = {"Authorization": f"Bearer {relay_key('relay:research-input')}"}
    resolved = await client.post(
        "/v1/relay/research-input/object-grants",
        headers=headers,
        json=_request_payload(),
    )
    original = resolved.json()["grants"][0]
    verified = await client.post(
        "/v1/relay/research-input/object-grants/verify",
        headers=headers,
        json={
            "schema_version": 1,
            "parent_run_id": _PARENT_RUN_ID,
            "execution_fingerprint": _EXECUTION_FINGERPRINT,
            "grants": [
                {
                    "dataset_id": original["dataset_id"],
                    "grant_id": original["grant_id"],
                    "expected_snapshot": original["snapshot"],
                }
            ],
        },
    )

    assert verified.status_code == 200
    rotated = verified.json()["grants"][0]
    assert rotated["grant_id"] != original["grant_id"]
    assert metadata_port.calls == [("head", _REFERENCE)] * 2


async def test_revoking_rotated_predecessor_preserves_replacement_authority(
    client: httpx.AsyncClient,
    metadata_port: _FakeMetadataPort,
    relay_key: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Revoking an old version must leave its active replacement verifiable."""
    monkeypatch.setattr(
        research_grants,
        "RESEARCH_GRANT_ROTATE_BEFORE",
        timedelta(hours=4),
    )
    headers = {"Authorization": f"Bearer {relay_key('relay:research-input')}"}
    resolved = await client.post(
        "/v1/relay/research-input/object-grants",
        headers=headers,
        json=_request_payload(),
    )
    original = resolved.json()["grants"][0]
    rotated_response = await client.post(
        "/v1/relay/research-input/object-grants/verify",
        headers=headers,
        json=_verify_payload(original),
    )
    rotated = rotated_response.json()["grants"][0]
    revoked = await client.post(
        "/v1/relay/research-input/object-grants/revoke",
        headers=headers,
        json=_revoke_payload(original["grant_id"]),
    )
    verified_replacement = await client.post(
        "/v1/relay/research-input/object-grants/verify",
        headers=headers,
        json=_verify_payload(rotated),
    )

    assert rotated_response.status_code == 200
    assert revoked.status_code == 200
    assert verified_replacement.status_code == 200
    assert original["grant_id"] != rotated["grant_id"]
    assert metadata_port.calls == [("head", _REFERENCE)] * 3


async def test_revoke_rejects_grant_id_above_public_identifier_bound(
    client: httpx.AsyncClient,
    relay_key: Callable[..., str],
) -> None:
    """A revoke grant id has the same bounded public contract as verify."""
    response = await client.post(
        "/v1/relay/research-input/object-grants/revoke",
        headers={
            "Authorization": f"Bearer {relay_key('relay:research-input')}"
        },
        json=_revoke_payload("g" * 513),
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "research object grant could not be verified"
    }


async def test_verify_and_revoke_reject_foreign_and_revoked_grants(
    client: httpx.AsyncClient,
    relay_key: Callable[..., str],
) -> None:
    """Foreign and revoked grants fail without exposing grant state."""
    owner_headers = {
        "Authorization": f"Bearer {relay_key('relay:research-input')}"
    }
    resolved = await client.post(
        "/v1/relay/research-input/object-grants",
        headers=owner_headers,
        json=_request_payload(),
    )
    grant = resolved.json()["grants"][0]
    foreign = await client.post(
        "/v1/relay/research-input/object-grants/verify",
        headers={
            "Authorization": (
                f"Bearer {relay_key('relay:research-input', user_id='other')}"
            )
        },
        json=_verify_payload(grant),
    )
    revoked = await client.post(
        "/v1/relay/research-input/object-grants/revoke",
        headers=owner_headers,
        json=_revoke_payload(grant["grant_id"]),
    )
    revoked_again = await client.post(
        "/v1/relay/research-input/object-grants/revoke",
        headers=owner_headers,
        json=_revoke_payload(grant["grant_id"]),
    )
    denied = await client.post(
        "/v1/relay/research-input/object-grants/verify",
        headers=owner_headers,
        json=_verify_payload(grant),
    )

    assert foreign.status_code == 400
    assert revoked.status_code == 200
    assert revoked_again.status_code == 200
    assert denied.status_code == 400
    assert grant["grant_id"] not in foreign.text
    assert grant["grant_id"] not in denied.text


async def test_verify_rejects_expired_grant(
    client: httpx.AsyncClient,
    grant_database: Path,
    relay_key: Callable[..., str],
) -> None:
    """An expired run-bound grant is denied without revealing its id."""
    headers = {"Authorization": f"Bearer {relay_key('relay:research-input')}"}
    resolved = await client.post(
        "/v1/relay/research-input/object-grants",
        headers=headers,
        json=_request_payload(
            "obs://phytomni/research/aged.tsv", dataset_id="dataset-002"
        ),
    )
    grant = resolved.json()["grants"][0]
    with sqlite3.connect(grant_database) as connection:
        connection.execute(
            "UPDATE research_object_grants "
            "SET expires_at = ? WHERE grant_id = ?",
            (
                (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                grant["grant_id"],
            ),
        )
    expired = await client.post(
        "/v1/relay/research-input/object-grants/verify",
        headers=headers,
        json=_verify_payload(grant),
    )

    assert expired.status_code == 400
    assert grant["grant_id"] not in expired.text


async def test_audit_write_failure_never_leaks_request_or_grant(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    relay_key: Callable[..., str],
) -> None:
    """Audit persistence failure preserves the safe successful projection."""
    sentinel = "obs://phytomni/very-private/secret.tsv"

    failing_store = SimpleNamespace(record=Mock(side_effect=OSError(sentinel)))

    monkeypatch.setattr(
        research_input,
        "get_audit_store",
        lambda _path: failing_store,
    )
    payload = _request_payload(sentinel)
    response = await client.post(
        "/v1/relay/research-input/object-grants",
        headers={
            "Authorization": f"Bearer {relay_key('relay:research-input')}"
        },
        json=payload,
    )

    assert response.status_code == 200
    assert sentinel not in response.text
