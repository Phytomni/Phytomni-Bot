# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the exact-key Research object metadata authority."""

from __future__ import annotations

from dataclasses import dataclass, fields
from types import SimpleNamespace
from typing import Any

import pytest
from tests.support.outbound_fakes import CountingObsRuntime, InlineObsRuntime
from tests.support.research_fakes import research_relay_snapshot_payload

from mcp_server_phytomni.runtime.outbound import (
    ObsClientRuntime,
    OutboundPoolName,
    OutboundPoolRegistry,
)
from mcp_server_phytomni.storage import (
    research_objects as research_objects_module,
)
from mcp_server_phytomni.storage.research_objects import (
    RESEARCH_OBJECT_SNAPSHOT_FIELDS,
    DirectResearchObjectMetadataPort,
    ResearchObjectAuthority,
    ResearchObjectCandidate,
    ResearchObjectMetadataError,
    ResearchObjectResolveRequest,
    ResearchObjectRevokeRequest,
    ResearchObjectSnapshot,
    ResearchObjectVerifyRequest,
    research_object_snapshot_payload,
)

pytestmark = pytest.mark.unit


def test_snapshot_payload_projects_exact_immutable_fields() -> None:
    """The shared relay projection preserves every safe snapshot field."""
    snapshot = ResearchObjectSnapshot(
        dataset_id="dataset_001",
        size_bytes=17,
        etag="etag-17",
        version_id="version-1",
        last_modified="2026-08-08T00:00:00Z",
        placeholder=False,
        snapshot_digest="digest-001",
    )

    payload = research_object_snapshot_payload(snapshot)
    assert payload == research_relay_snapshot_payload(
        "dataset_001", snapshot_digest="digest-001"
    )
    assert frozenset(payload) == RESEARCH_OBJECT_SNAPSHOT_FIELDS


@dataclass(frozen=True, slots=True)
class _ObjectMetadata:
    """One fake SDK metadata response body."""

    size_bytes: int
    etag: str | None = "etag-17"
    version_id: str | None = "version-1"
    last_modified: str | None = "2026-08-08T00:00:00Z"


class FakeObsClient:
    """HEAD-only OBS fake that records every attempted SDK method."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.metadata: dict[str, _ObjectMetadata] = {
            "a.vcf": _ObjectMetadata(17)
        }
        self.error_status: dict[str, int] = {}
        self.error_message = (
            "https://operator.example/private secret=ak-secret "
            "bucket=dev-bucket key=private/a.vcf"
        )

    def __getattr__(self, name: str) -> object:
        """Expose only the OBS SDK methods the port must not widen."""
        methods = {
            "getObjectMetadata": self.get_object_metadata,
            "getObject": self._get_object,
            "listObjects": self._list_objects,
            "putContent": self._put_content,
            "deleteObject": self._delete_object,
        }
        try:
            return methods[name]
        except KeyError:
            raise AttributeError(name) from None

    def get_object_metadata(self, **kwargs: str) -> SimpleNamespace:
        """Return one seeded metadata HEAD response."""
        bucket = kwargs["bucketName"]
        key = kwargs["objectKey"]
        self.calls.append(("head", bucket, key))
        if key in self.error_status:
            return SimpleNamespace(
                status=self.error_status[key],
                requestId="request-private",
                errorMessage=self.error_message,
            )
        metadata = self.metadata.get(key)
        if metadata is None:
            return SimpleNamespace(
                status=404,
                requestId="request-private",
                errorMessage=self.error_message,
            )
        return SimpleNamespace(
            status=200,
            body=SimpleNamespace(
                contentLength=metadata.size_bytes,
                etag=metadata.etag,
                versionId=metadata.version_id,
                lastModified=metadata.last_modified,
            ),
        )

    def _get_object(self, **kwargs: str) -> None:
        """Fail if the authority tries to read a dataset body."""
        self._forbidden("get", kwargs)

    def _list_objects(self, **kwargs: str) -> None:
        """Fail if the authority tries to enumerate a prefix."""
        self._forbidden("list", kwargs)

    def _put_content(self, **kwargs: str) -> None:
        """Fail if the authority tries to write a dataset object."""
        self._forbidden("put", kwargs)

    def _delete_object(self, **kwargs: str) -> None:
        """Fail if the authority tries to delete a dataset object."""
        self._forbidden("delete", kwargs)

    def _forbidden(self, method: str, kwargs: dict[str, str]) -> None:
        """Record then raise for a disallowed SDK operation."""
        self.calls.append((method, kwargs["bucketName"], kwargs["objectKey"]))
        raise AssertionError(f"Research metadata must not call {method}")


def _pools(*, obs_capacity: int) -> OutboundPoolRegistry:
    """Build fixed outbound capacities with the requested OBS bound."""
    return OutboundPoolRegistry(
        {
            name: (obs_capacity if name is OutboundPoolName.OBS else 0)
            for name in OutboundPoolName
        },
        wait_warn_seconds=1.0,
    )


def _resolve_request(reference: str) -> ResearchObjectResolveRequest:
    """Build one one-object resolve request with a compound-safe suffix."""
    return ResearchObjectResolveRequest(
        parent_run_id="run-parent",
        execution_fingerprint="exec-fingerprint",
        objects=(
            ResearchObjectCandidate(
                dataset_id="dataset-1",
                exact_reference=reference,
                compound_suffix=".vcf",
            ),
        ),
    )


def _two_object_resolve_request() -> ResearchObjectResolveRequest:
    """Build two independent objects so each needs its own metadata HEAD."""
    return ResearchObjectResolveRequest(
        parent_run_id="run-parent",
        execution_fingerprint="exec-fingerprint",
        objects=(
            ResearchObjectCandidate(
                dataset_id="dataset-1",
                exact_reference="obs://dev-bucket/a.vcf",
                compound_suffix=".vcf",
            ),
            ResearchObjectCandidate(
                dataset_id="dataset-2",
                exact_reference="obs://dev-bucket/b.vcf",
                compound_suffix=".vcf",
            ),
        ),
    )


def _port(fake_obs: FakeObsClient) -> DirectResearchObjectMetadataPort:
    """Build the direct port with one injected runtime-owned fake."""
    return DirectResearchObjectMetadataPort(
        bucket="dev-bucket",
        obs_runtime=InlineObsRuntime(lambda: fake_obs),
    )


@pytest.mark.asyncio
async def test_direct_resolve_accepts_slash_obs_exact_reference() -> None:
    """Pasted /obs/bucket/key must HEAD the same object as obs://."""
    fake_obs = FakeObsClient()
    authorities = await _port(fake_obs).resolve(
        _resolve_request("/obs/dev-bucket/a.vcf")
    )
    assert tuple(authorities)[0].dataset_id == "dataset-1"
    assert fake_obs.calls == [("head", "dev-bucket", "a.vcf")]


async def test_direct_resolve_leases_each_metadata_head_separately() -> None:
    """Two exact-key HEAD attempts request two independent OBS leases."""
    fake_obs = FakeObsClient()
    fake_obs.metadata["b.vcf"] = _ObjectMetadata(23)
    runtime = CountingObsRuntime(fake_obs)
    port = DirectResearchObjectMetadataPort(
        bucket="dev-bucket",
        obs_runtime=runtime,
    )

    resolved = await port.resolve(_two_object_resolve_request())

    assert [item.dataset_id for item in resolved] == ["dataset-1", "dataset-2"]
    assert runtime.calls == 2
    assert fake_obs.calls == [
        ("head", "dev-bucket", "a.vcf"),
        ("head", "dev-bucket", "b.vcf"),
    ]


async def test_direct_resolve_releases_capacity_before_local_snapshot_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capacity-one OBS slots are free while local snapshot work runs."""
    fake_obs = FakeObsClient()
    pools = _pools(obs_capacity=1)
    runtime = ObsClientRuntime(pools, fake_obs)
    port = DirectResearchObjectMetadataPort(
        bucket="dev-bucket",
        obs_runtime=runtime,
    )
    observed_in_use: list[int] = []

    def observe_snapshot_work(
        dataset_id: str,
        metadata: Any,
    ) -> ResearchObjectSnapshot:
        observed_in_use.append(pools.snapshot(OutboundPoolName.OBS).in_use)
        return ResearchObjectSnapshot(
            dataset_id,
            metadata.size_bytes,
            metadata.etag,
            metadata.version_id,
            metadata.last_modified,
            metadata.size_bytes == 0,
            "observed-during-test",
        )

    monkeypatch.setattr(
        research_objects_module,
        "_snapshot_from_metadata",
        observe_snapshot_work,
    )
    try:
        await port.resolve(_resolve_request("obs://dev-bucket/a.vcf"))
    finally:
        await runtime.aclose()

    assert observed_in_use == [0]


def _assert_no_factory_details(
    error: ResearchObjectMetadataError,
    caplog: pytest.LogCaptureFixture,
    sentinel: str,
) -> None:
    """Assert the public failure projection suppresses factory internals."""
    assert str(error) != sentinel
    assert sentinel not in str(error)
    assert sentinel not in caplog.text
    assert error.__cause__ is None
    assert error.__suppress_context__ is True


async def test_direct_resolve_uses_exact_metadata_head_only() -> None:
    """Resolve normalizes only for HEAD and captures its stable snapshot."""
    fake_obs = FakeObsClient()

    resolved = await _port(fake_obs).resolve(
        _resolve_request("obs://dev-bucket/a.vcf")
    )

    snapshot = resolved[0].snapshot
    assert fake_obs.calls == [("head", "dev-bucket", "a.vcf")]
    assert snapshot.dataset_id == "dataset-1"
    assert snapshot.size_bytes == 17
    assert snapshot.etag == "etag-17"
    assert snapshot.version_id == "version-1"
    assert snapshot.last_modified == "2026-08-08T00:00:00Z"
    assert snapshot.placeholder is False
    assert len(snapshot.snapshot_digest) == 64
    assert "a.vcf" not in resolved[0].authority_id


async def test_direct_resolve_accepts_opaque_managed_key_without_suffix() -> (
    None
):
    """Trusted managed objects may have no client-visible filename suffix."""
    fake_obs = FakeObsClient()
    fake_obs.metadata["opaque-key"] = _ObjectMetadata(17)
    request = ResearchObjectResolveRequest(
        parent_run_id="run-parent",
        execution_fingerprint="exec-fingerprint",
        objects=(
            ResearchObjectCandidate(
                dataset_id="dataset-opaque",
                exact_reference="obs://dev-bucket/opaque-key",
                compound_suffix="",
            ),
        ),
    )

    resolved = await _port(fake_obs).resolve(request)

    assert [item.dataset_id for item in resolved] == ["dataset-opaque"]
    assert fake_obs.calls == [("head", "dev-bucket", "opaque-key")]


async def test_direct_resolve_sanitizes_client_factory_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Resolve maps every client-factory failure to its safe public error."""
    sentinel = (
        "https://endpoint.example token=credential "
        "bucket=dev-bucket key=private/a.vcf"
    )

    def raise_factory_error() -> FakeObsClient:
        """Simulate a credentialed client factory failure."""
        raise RuntimeError(sentinel)

    port = DirectResearchObjectMetadataPort(
        bucket="dev-bucket",
        obs_runtime=InlineObsRuntime(raise_factory_error),
    )

    with pytest.raises(ResearchObjectMetadataError) as captured:
        await port.resolve(_resolve_request("obs://dev-bucket/a.vcf"))

    _assert_no_factory_details(captured.value, caplog, sentinel)


async def test_direct_verify_sanitizes_client_factory_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify maps every client-factory failure to its safe public error."""
    fake_obs = FakeObsClient()
    fail_factory = False
    sentinel = (
        "https://endpoint.example token=credential "
        "bucket=dev-bucket key=private/a.vcf"
    )

    def client_factory() -> FakeObsClient:
        """Return a fake once, then simulate a credentialed factory failure."""
        if fail_factory:
            raise RuntimeError(sentinel)
        return fake_obs

    port = DirectResearchObjectMetadataPort(
        bucket="dev-bucket",
        obs_runtime=InlineObsRuntime(client_factory),
    )
    request = _resolve_request("obs://dev-bucket/a.vcf")
    authority = (await port.resolve(request))[0]
    fail_factory = True

    with pytest.raises(ResearchObjectMetadataError) as captured:
        await port.verify(
            ResearchObjectVerifyRequest(
                parent_run_id=request.parent_run_id,
                execution_fingerprint=request.execution_fingerprint,
                authorities=(authority,),
            )
        )

    _assert_no_factory_details(captured.value, caplog, sentinel)


async def test_direct_resolve_rejects_missing_or_placeholder_objects() -> None:
    """Missing and zero-byte placeholders never become authorities."""
    missing_fake = FakeObsClient()
    placeholder_fake = FakeObsClient()
    placeholder_fake.metadata["a.vcf"] = _ObjectMetadata(0)

    with pytest.raises(ResearchObjectMetadataError):
        await _port(missing_fake).resolve(
            _resolve_request("obs://dev-bucket/missing.vcf")
        )
    with pytest.raises(ResearchObjectMetadataError):
        await _port(placeholder_fake).resolve(
            _resolve_request("obs://dev-bucket/a.vcf")
        )

    assert missing_fake.calls == [("head", "dev-bucket", "missing.vcf")]
    assert placeholder_fake.calls == [("head", "dev-bucket", "a.vcf")]


async def test_direct_resolve_rolls_back_later_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later candidate failure atomically rolls back earlier authorities."""
    monkeypatch.setattr(
        research_objects_module.secrets,
        "token_urlsafe",
        lambda _size: "authority-first",
    )
    expected_authority = (
        await _port(FakeObsClient()).resolve(
            _resolve_request("obs://dev-bucket/a.vcf")
        )
    )[0]
    fake_obs = FakeObsClient()
    port = _port(fake_obs)
    request = ResearchObjectResolveRequest(
        parent_run_id="run-parent",
        execution_fingerprint="exec-fingerprint",
        objects=(
            ResearchObjectCandidate(
                dataset_id="dataset-1",
                exact_reference="obs://dev-bucket/a.vcf",
                compound_suffix=".vcf",
            ),
            ResearchObjectCandidate(
                dataset_id="dataset-2",
                exact_reference="obs://dev-bucket/missing.vcf",
                compound_suffix=".vcf",
            ),
        ),
    )

    with pytest.raises(ResearchObjectMetadataError):
        await port.resolve(request)

    assert fake_obs.calls == [
        ("head", "dev-bucket", "a.vcf"),
        ("head", "dev-bucket", "missing.vcf"),
    ]
    with pytest.raises(ResearchObjectMetadataError):
        await port.verify(
            ResearchObjectVerifyRequest(
                parent_run_id=request.parent_run_id,
                execution_fingerprint=request.execution_fingerprint,
                authorities=(expected_authority,),
            )
        )
    assert fake_obs.calls == [
        ("head", "dev-bucket", "a.vcf"),
        ("head", "dev-bucket", "missing.vcf"),
    ]


async def test_direct_digest_is_deterministic_with_missing_identity() -> None:
    """Canonical snapshots encode absent identity fields as explicit nulls."""
    first_fake = FakeObsClient()
    second_fake = FakeObsClient()
    first_fake.metadata["a.vcf"] = _ObjectMetadata(17, None, None, None)
    second_fake.metadata["a.vcf"] = _ObjectMetadata(17, None, None, None)

    first = (
        await _port(first_fake).resolve(
            _resolve_request("obs://dev-bucket/a.vcf")
        )
    )[0]
    second = (
        await _port(second_fake).resolve(
            _resolve_request("obs://dev-bucket/a.vcf")
        )
    )[0]

    assert first.snapshot.etag is None
    assert first.snapshot.version_id is None
    assert first.snapshot.last_modified is None
    assert first.snapshot.snapshot_digest == second.snapshot.snapshot_digest


async def test_direct_verify_rejects_a_changed_snapshot() -> None:
    """Verify re-HEADs the private key and rejects a changed identity."""
    fake_obs = FakeObsClient()
    port = _port(fake_obs)
    request = _resolve_request("obs://dev-bucket/a.vcf")
    authority = (await port.resolve(request))[0]
    fake_obs.metadata["a.vcf"] = _ObjectMetadata(18)

    with pytest.raises(ResearchObjectMetadataError):
        await port.verify(
            ResearchObjectVerifyRequest(
                parent_run_id=request.parent_run_id,
                execution_fingerprint=request.execution_fingerprint,
                authorities=(authority,),
            )
        )

    assert fake_obs.calls == [
        ("head", "dev-bucket", "a.vcf"),
        ("head", "dev-bucket", "a.vcf"),
    ]


async def test_direct_revoke_is_idempotent_and_never_deletes_objects() -> None:
    """Revoke clears private authority state without touching the dataset."""
    fake_obs = FakeObsClient()
    port = _port(fake_obs)
    request = _resolve_request("obs://dev-bucket/a.vcf")
    authority = (await port.resolve(request))[0]
    revoke = ResearchObjectRevokeRequest(
        parent_run_id=request.parent_run_id,
        execution_fingerprint=request.execution_fingerprint,
        authority_ids=(authority.authority_id,),
    )

    await port.revoke(revoke)
    await port.revoke(revoke)

    assert fake_obs.calls == [("head", "dev-bucket", "a.vcf")]
    with pytest.raises(ResearchObjectMetadataError):
        await port.verify(
            ResearchObjectVerifyRequest(
                parent_run_id=request.parent_run_id,
                execution_fingerprint=request.execution_fingerprint,
                authorities=(authority,),
            )
        )
    assert fake_obs.calls == [("head", "dev-bucket", "a.vcf")]


async def test_direct_failure_projection_never_discloses_storage_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Public authority results and failures contain no storage secrets."""
    fake_obs = FakeObsClient()
    port = _port(fake_obs)
    reference = "obs://dev-bucket/private/a.vcf"
    fake_obs.error_status["private/a.vcf"] = 503

    with pytest.raises(ResearchObjectMetadataError) as captured:
        await port.resolve(_resolve_request(reference))

    projected = str(captured.value)
    recorded = caplog.text
    for forbidden in (
        "operator.example",
        "ak-secret",
        "dev-bucket",
        "private/a.vcf",
        reference,
        fake_obs.error_message,
    ):
        assert forbidden not in projected
        assert forbidden not in recorded


async def test_direct_authority_is_bound_to_its_private_run_scope() -> None:
    """A copied authority cannot be verified from another run scope."""
    fake_obs = FakeObsClient()
    port = _port(fake_obs)
    authority = (
        await port.resolve(_resolve_request("obs://dev-bucket/a.vcf"))
    )[0]

    with pytest.raises(ResearchObjectMetadataError):
        await port.verify(
            ResearchObjectVerifyRequest(
                parent_run_id="other-run",
                execution_fingerprint="other-fingerprint",
                authorities=(authority,),
            )
        )

    assert fake_obs.calls == [("head", "dev-bucket", "a.vcf")]


def test_authority_model_has_no_private_storage_identity() -> None:
    """The public authority DTO has only its opaque id and safe snapshot."""
    assert {field.name for field in fields(ResearchObjectAuthority)} == {
        "dataset_id",
        "authority_id",
        "snapshot",
    }
