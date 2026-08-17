# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Validation edges for Research relay capability and grant decoding."""

from __future__ import annotations

from typing import Any, cast

import pytest
from mcp.shared.exceptions import McpError
from pydantic import SecretStr
from tests.support.research_fakes import research_relay_snapshot_payload

from mcp_server_phytomni.common import relay_client as rc
from mcp_server_phytomni.runtime.outbound import OutboundPoolName
from mcp_server_phytomni.storage.research_objects import (
    ResearchObjectAuthority,
    ResearchObjectCandidate,
    ResearchObjectResolveRequest,
    ResearchObjectRevokeRequest,
    ResearchObjectSnapshot,
    ResearchObjectVerifyRequest,
)

pytestmark = pytest.mark.unit

_GRANT_MESSAGE = "relay research object grant request failed"
_CAPABILITY_MESSAGE = "relay research capability request failed"


def _client() -> rc.RelayClient:
    """Return a RelayClient with a fixed relay base URL and policy."""
    return rc.RelayClient(
        base_url="https://relay.test",
        api_key=SecretStr("k9"),
        timeout=5.0,
        max_retries=2,
        retriable_codes=(503,),
    )


def _candidate(dataset_id: str) -> ResearchObjectCandidate:
    """Return one resolve candidate with opaque identifiers."""
    return ResearchObjectCandidate(
        dataset_id, f"obs://bucket/{dataset_id}.vcf", ".vcf"
    )


def _snapshot(dataset_id: str, **changes: object) -> ResearchObjectSnapshot:
    """Return one immutable snapshot, optionally overriding fields."""
    payload = research_relay_snapshot_payload(dataset_id)
    payload.update(changes)
    return ResearchObjectSnapshot(
        dataset_id=cast(str, payload["dataset_id"]),
        size_bytes=cast(int, payload["size_bytes"]),
        etag=cast(str | None, payload["etag"]),
        version_id=cast(str | None, payload["version_id"]),
        last_modified=cast(str | None, payload["last_modified"]),
        placeholder=cast(bool, payload["placeholder"]),
        snapshot_digest=cast(str, payload["snapshot_digest"]),
    )


def _authority(
    dataset_id: str, authority_id: str, **snapshot_changes: object
) -> ResearchObjectAuthority:
    """Return one verify authority bound to a safe snapshot."""
    return ResearchObjectAuthority(
        dataset_id=dataset_id,
        authority_id=authority_id,
        snapshot=_snapshot(dataset_id, **snapshot_changes),
    )


def _grant(
    dataset_id: str,
    grant_id: str,
    *,
    expires_at: str = "2026-08-08T03:00:00+00:00",
    revision: object = 0,
    snapshot: dict[str, object] | None = None,
) -> dict[str, object]:
    """Return one grant DTO for decoder tests."""
    return {
        "dataset_id": dataset_id,
        "grant_id": grant_id,
        "snapshot": snapshot or research_relay_snapshot_payload(dataset_id),
        "expires_at": expires_at,
        "revision": revision,
    }


def _valid_capability(**changes: object) -> dict[str, object]:
    """Return a minimal valid capability handshake payload."""
    payload: dict[str, object] = {
        "protocols": {"research_object_grant_v1": [1]},
        "research_object_grant": {"max_objects": 8},
    }
    payload.update(changes)
    return payload


async def test_get_research_capabilities_rejects_non_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-object handshake payload maps to the capability McpError."""

    async def fake_get_json(
        self,
        path: str,
        *,
        pool: OutboundPoolName,
        options: rc.RelayRequestOptions,
        query=None,
    ) -> object:
        del self, path, pool, options, query
        return ["not-a-dict"]

    monkeypatch.setattr(rc.RelayClient, "get_json", fake_get_json)
    with pytest.raises(McpError, match=_CAPABILITY_MESSAGE):
        await _client().get_research_capabilities()


@pytest.mark.parametrize(
    "payload",
    [
        {"unexpected": 1},
        {},
        {
            "protocols": {"other": [1]},
            "research_object_grant": {"max_objects": 1},
        },
        {
            "protocols": {"research_object_grant_v1": [1]},
            "research_object_grant": {"max_objects": 1, "extra": True},
        },
        {
            "protocols": {"research_object_grant_v1": [1]},
            "research_object_grant": {},
        },
        {
            "protocols": {"research_object_grant_v1": 1},
            "research_object_grant": {"max_objects": 1},
        },
        {
            "protocols": {"research_object_grant_v1": []},
            "research_object_grant": {"max_objects": 1},
        },
        {
            "protocols": {"research_object_grant_v1": list(range(9))},
            "research_object_grant": {"max_objects": 1},
        },
        {
            "protocols": {"research_object_grant_v1": [True]},
            "research_object_grant": {"max_objects": 1},
        },
        {
            "protocols": {"research_object_grant_v1": [1, 1]},
            "research_object_grant": {"max_objects": 1},
        },
        {
            "protocols": {"research_object_grant_v1": [1]},
            "research_object_grant": {"max_objects": True},
        },
        {
            "protocols": {"research_object_grant_v1": [1]},
            "research_object_grant": {"max_objects": 0},
        },
        {
            "protocols": {"research_object_grant_v1": [1]},
            "research_object_grant": {"max_objects": 257},
        },
        {
            "protocols": {"research_object_grant_v1": [1]},
            "research_object_grant": {
                "max_objects": 1,
                "authorized_scope": "relay:*",
            },
            "authorized_scope": "relay:research-input",
        },
        {
            "protocols": {"research_object_grant_v1": [1]},
            "research_object_grant": {"max_objects": 1},
            "authorized_scope": "relay:other",
        },
    ],
)
def test_decode_research_capabilities_rejects_invalid_payloads(
    payload: object,
) -> None:
    """Capability handshake decoding stays fail-closed."""
    with pytest.raises((TypeError, ValueError)):
        getattr(rc, "_decode_research_capabilities")(payload)


def test_decode_research_capabilities_accepts_matching_wildcard_scope() -> (
    None
):
    """Matching top-level and descriptor wildcard scopes are accepted."""
    capability = getattr(rc, "_decode_research_capabilities")(
        _valid_capability(
            research_object_grant={
                "max_objects": 4,
                "authorized_scope": "relay:*",
            },
            authorized_scope="relay:*",
        )
    )
    assert capability.authorized_scope == "relay:*"
    assert capability.max_objects == 4


async def test_verify_research_objects_rejects_snapshot_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rotated grant whose snapshot drifted is a grant McpError."""
    authority = _authority("d1", "grant-1")
    drifted = research_relay_snapshot_payload("d1")
    drifted["snapshot_digest"] = "digest-drifted"

    async def fake_post_json(
        self,
        path: str,
        json_body: dict[str, Any],
        *,
        pool: OutboundPoolName,
        options: rc.RelayRequestOptions,
    ) -> dict[str, Any]:
        del self, path, json_body, pool, options
        return {
            "grants": [
                _grant("d1", "grant-1-new", snapshot=drifted),
            ]
        }

    monkeypatch.setattr(rc.RelayClient, "post_json", fake_post_json)
    with pytest.raises(McpError, match=_GRANT_MESSAGE):
        await _client().verify_research_objects(
            ResearchObjectVerifyRequest(
                parent_run_id="run-1",
                execution_fingerprint="execution-1",
                authorities=(authority,),
            )
        )


@pytest.mark.parametrize(
    "payload",
    [
        "nope",
        {},
        {"revoked": True},
        {"revoked": -1},
        {"revoked": "2"},
        {"revoked": 1, "extra": 1},
    ],
)
async def test_revoke_research_objects_rejects_invalid_response(
    monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    """Revoke responses must be a non-negative integer count."""

    async def fake_post_json(
        self,
        path: str,
        json_body: dict[str, Any],
        *,
        pool: OutboundPoolName,
        options: rc.RelayRequestOptions,
    ) -> object:
        del self, path, json_body, pool, options
        return payload

    monkeypatch.setattr(rc.RelayClient, "post_json", fake_post_json)
    with pytest.raises(McpError, match=_GRANT_MESSAGE):
        await _client().revoke_research_objects(
            ResearchObjectRevokeRequest(
                parent_run_id="run-1",
                execution_fingerprint="execution-1",
                authority_ids=("grant-1",),
            )
        )


def test_decode_grants_rejects_invalid_envelopes() -> None:
    """Grant envelopes must be an exact-length unique list."""
    expected = (_candidate("d1"),)
    with pytest.raises(ValueError):
        getattr(rc, "_decode_grants")("nope", expected)
    with pytest.raises(ValueError):
        getattr(rc, "_decode_grants")({"grants": {}}, expected)
    with pytest.raises(ValueError):
        getattr(rc, "_decode_grants")({"grants": []}, expected)
    with pytest.raises(ValueError):
        getattr(rc, "_decode_grants")(
            {"grants": [_grant("d1", "g1"), _grant("d1", "g2")]},
            (_candidate("d1"), _candidate("d1")),
        )


@pytest.mark.parametrize(
    "raw_grant",
    [
        {"dataset_id": "d1"},
        _grant("d1", "g1", expires_at="2026-08-08T03:00:00"),
        _grant("d1", "g1", revision=True),
        _grant("d1", "g1", revision=-1),
        _grant(
            "d1",
            "g1",
            snapshot=research_relay_snapshot_payload("d2"),
        ),
        _grant(
            "d1",
            "g1",
            snapshot={
                **research_relay_snapshot_payload("d1"),
                "placeholder": True,
            },
        ),
        _grant("d2", "g1"),
    ],
)
def test_decode_grants_rejects_invalid_records(
    raw_grant: dict[str, object],
) -> None:
    """Each grant record is type-checked before it is trusted."""
    with pytest.raises(ValueError):
        getattr(rc, "_decode_grants")(
            {"grants": [raw_grant]}, (_candidate("d1"),)
        )


def test_decode_snapshot_and_optional_text_edges() -> None:
    """Snapshot DTOs reject the wrong shape and accept optional None."""
    assert getattr(rc, "_valid_snapshot_fields")((1, 2, 3)) is False
    with pytest.raises(ValueError):
        getattr(rc, "_decode_snapshot")("nope")
    with pytest.raises(ValueError):
        getattr(rc, "_decode_snapshot")({"dataset_id": "d1"})
    payload = research_relay_snapshot_payload("d1")
    payload["etag"] = None
    payload["version_id"] = None
    payload["last_modified"] = None
    snapshot = getattr(rc, "_decode_snapshot")(payload)
    assert snapshot.etag is None
    assert snapshot.placeholder is False


@pytest.mark.parametrize(
    "request_obj",
    [
        "nope",
        ResearchObjectResolveRequest(
            parent_run_id="run-1",
            execution_fingerprint="execution-1",
            objects=(),
        ),
        ResearchObjectResolveRequest(
            parent_run_id="run\x00",
            execution_fingerprint="execution-1",
            objects=(_candidate("d1"),),
        ),
        ResearchObjectResolveRequest(
            parent_run_id="run-1",
            execution_fingerprint="",
            objects=(_candidate("d1"),),
        ),
        ResearchObjectResolveRequest(
            parent_run_id="run-1",
            execution_fingerprint="execution-1",
            objects=cast(Any, ("not-a-candidate",)),
        ),
        ResearchObjectResolveRequest(
            parent_run_id="run-1",
            execution_fingerprint="execution-1",
            objects=(ResearchObjectCandidate("d1", "", ".vcf"),),
        ),
        ResearchObjectResolveRequest(
            parent_run_id="run-1",
            execution_fingerprint="execution-1",
            objects=(_candidate("d1"), _candidate("d1")),
        ),
        ResearchObjectResolveRequest(
            parent_run_id="run-1",
            execution_fingerprint="execution-1",
            objects=tuple(_candidate(f"d{index}") for index in range(257)),
        ),
    ],
)
async def test_resolve_research_objects_rejects_invalid_requests(
    request_obj: object,
) -> None:
    """Resolve input validation fails closed before any network call."""
    with pytest.raises(McpError, match=_GRANT_MESSAGE):
        await _client().resolve_research_objects(
            cast(ResearchObjectResolveRequest, request_obj)
        )


@pytest.mark.parametrize(
    "request_obj",
    [
        "nope",
        ResearchObjectVerifyRequest(
            parent_run_id="run-1",
            execution_fingerprint="execution-1",
            authorities=(),
        ),
        ResearchObjectVerifyRequest(
            parent_run_id="",
            execution_fingerprint="execution-1",
            authorities=(_authority("d1", "grant-1"),),
        ),
        ResearchObjectVerifyRequest(
            parent_run_id="run-1",
            execution_fingerprint="exec\x7f",
            authorities=(_authority("d1", "grant-1"),),
        ),
        ResearchObjectVerifyRequest(
            parent_run_id="run-1",
            execution_fingerprint="execution-1",
            authorities=cast(Any, ("not-an-authority",)),
        ),
        ResearchObjectVerifyRequest(
            parent_run_id="run-1",
            execution_fingerprint="execution-1",
            authorities=(
                ResearchObjectAuthority(
                    dataset_id="d1",
                    authority_id="grant-1",
                    snapshot=cast(Any, "nope"),
                ),
            ),
        ),
        ResearchObjectVerifyRequest(
            parent_run_id="run-1",
            execution_fingerprint="execution-1",
            authorities=(_authority("d1", "grant-1", size_bytes=-1),),
        ),
        ResearchObjectVerifyRequest(
            parent_run_id="run-1",
            execution_fingerprint="execution-1",
            authorities=(_authority("d1", "grant-1", placeholder=True),),
        ),
        ResearchObjectVerifyRequest(
            parent_run_id="run-1",
            execution_fingerprint="execution-1",
            authorities=(
                ResearchObjectAuthority(
                    dataset_id="d1",
                    authority_id="grant-1",
                    snapshot=_snapshot("other"),
                ),
            ),
        ),
        ResearchObjectVerifyRequest(
            parent_run_id="run-1",
            execution_fingerprint="execution-1",
            authorities=(
                _authority("d1", "grant-1"),
                _authority("d2", "grant-1"),
            ),
        ),
        ResearchObjectVerifyRequest(
            parent_run_id="run-1",
            execution_fingerprint="execution-1",
            authorities=tuple(
                _authority(f"d{index}", f"grant-{index}")
                for index in range(257)
            ),
        ),
    ],
)
async def test_verify_research_objects_rejects_invalid_requests(
    request_obj: object,
) -> None:
    """Verify input validation fails closed before any network call."""
    with pytest.raises(McpError, match=_GRANT_MESSAGE):
        await _client().verify_research_objects(
            cast(ResearchObjectVerifyRequest, request_obj)
        )


@pytest.mark.parametrize(
    "request_obj",
    [
        "nope",
        ResearchObjectRevokeRequest(
            parent_run_id="run-1",
            execution_fingerprint="execution-1",
            authority_ids=(),
        ),
        ResearchObjectRevokeRequest(
            parent_run_id="run-1",
            execution_fingerprint="execution-1",
            authority_ids=cast(Any, ["grant-1"]),
        ),
        ResearchObjectRevokeRequest(
            parent_run_id="",
            execution_fingerprint="execution-1",
            authority_ids=("grant-1",),
        ),
        ResearchObjectRevokeRequest(
            parent_run_id="run-1",
            execution_fingerprint="exec\x00",
            authority_ids=("grant-1",),
        ),
        ResearchObjectRevokeRequest(
            parent_run_id="run-1",
            execution_fingerprint="execution-1",
            authority_ids=("grant-1", "grant-1"),
        ),
        ResearchObjectRevokeRequest(
            parent_run_id="run-1",
            execution_fingerprint="execution-1",
            authority_ids=("bad\x7fid",),
        ),
        ResearchObjectRevokeRequest(
            parent_run_id="run-1",
            execution_fingerprint="execution-1",
            authority_ids=tuple(f"grant-{index}" for index in range(257)),
        ),
    ],
)
async def test_revoke_research_objects_rejects_invalid_requests(
    request_obj: object,
) -> None:
    """Revoke input validation fails closed before any network call."""
    with pytest.raises(McpError, match=_GRANT_MESSAGE):
        await _client().revoke_research_objects(
            cast(ResearchObjectRevokeRequest, request_obj)
        )
