# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Coverage and failure-boundary tests for Research runtime bindings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.agents.research import dispatch_runtime
from mcp_server_phytomni.agents.research.dispatch_outbox import (
    ResearchDispatchRecord,
)
from mcp_server_phytomni.storage.research_objects import (
    DirectResearchObjectMetadataPort,
    RelayResearchObjectMetadataPort,
    ResearchObjectAuthority,
    ResearchObjectMetadataError,
    ResearchObjectSnapshot,
    research_object_snapshot_payload,
)

pytestmark = pytest.mark.unit


def _record(
    *,
    remote_task_id: str | None = "task-1",
    payload: Mapping[str, Any] | None = None,
) -> ResearchDispatchRecord:
    """Build the smallest durable child record accepted by the bindings."""
    return ResearchDispatchRecord(
        dispatch_id="dispatch-1",
        run_id="run-1",
        child_ordinal=0,
        dispatch_fingerprint="f" * 64,
        state="sent",
        remote_task_id=remote_task_id,
        revision=1,
        payload=payload or {},
    )


def _bindings(metadata: Any) -> Any:
    """Construct the private callback bundle without provider I/O."""
    bindings_type = getattr(dispatch_runtime, "_RuntimeBindings")
    return bindings_type(
        analyst_agent=object(),
        analyst_config=SimpleNamespace(),
        sensitive_config=object(),
        metadata_port=metadata,
    )


def _grant_payload(
    snapshot: ResearchObjectSnapshot,
) -> list[dict[str, object]]:
    """Build one persisted grant sidecar for binding verification tests."""
    return [
        {
            "dataset_id": snapshot.dataset_id,
            "exact_reference": "obs://bucket/data.tsv",
            "compound_suffix": ".tsv",
            "grant_id": "grant-old",
            "snapshot": research_object_snapshot_payload(snapshot),
        }
    ]


def _snapshot(dataset_id: str = "dataset-1") -> ResearchObjectSnapshot:
    """Return one immutable metadata snapshot."""
    return ResearchObjectSnapshot(
        dataset_id=dataset_id,
        size_bytes=12,
        etag="etag-1",
        version_id="version-1",
        last_modified="2026-08-08T00:00:00+00:00",
        placeholder=False,
        snapshot_digest=f"snapshot-{dataset_id}",
    )


@pytest.mark.asyncio
async def test_runtime_query_maps_provider_failures_and_statuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery query fails closed for transport and terminal statuses."""
    bindings = _bindings(SimpleNamespace())
    record = _record()

    async def raise_failure(*_args: Any, **_kwargs: Any) -> object:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(dispatch_runtime, "task_status", raise_failure)
    assert await bindings.query(record) is None

    async def wrong_shape(*_args: Any, **_kwargs: Any) -> object:
        return object()

    monkeypatch.setattr(dispatch_runtime, "task_status", wrong_shape)
    assert await bindings.query(record) is None

    async def terminal(*_args: Any, **_kwargs: Any) -> object:
        return {"status": "failed_at_agent_level"}

    monkeypatch.setattr(dispatch_runtime, "task_status", terminal)
    assert await bindings.query(record) is None


@pytest.mark.asyncio
async def test_runtime_verify_direct_restart_fallback_rotates_grant() -> None:
    """Direct metadata loss re-resolves exact references and rotates IDs."""
    snapshot = _snapshot()
    payload = {"research_grants": _grant_payload(snapshot)}

    class RestartPort(DirectResearchObjectMetadataPort):
        """Force the documented in-memory-authority restart path."""

        async def verify(self, request: Any) -> tuple[Any, ...]:
            del request
            raise ResearchObjectMetadataError()

        async def resolve(self, request: Any) -> tuple[Any, ...]:
            del request
            return (
                ResearchObjectAuthority("dataset-1", "grant-new", snapshot),
            )

    bindings = _bindings(RestartPort("bucket", object))
    verified = await bindings.verify(_record(payload=payload))

    assert verified.grant_ids == ("grant-new",)
    assert verified.payload["research_grants"][0]["grant_id"] == "grant-new"


@pytest.mark.asyncio
async def test_runtime_verify_rejects_non_direct_failure_and_bad_sets() -> (
    None
):
    """Relay errors and inconsistent provider authority sets never pass."""
    snapshot = _snapshot()
    payload = {"research_grants": _grant_payload(snapshot)}

    class FailingPort:
        """Metadata port that cannot verify persisted authorities."""

        async def verify(self, _request: Any) -> tuple[Any, ...]:
            """Raise the safe metadata error used by relay ports."""
            raise ResearchObjectMetadataError()

        async def resolve(self, _request: Any) -> tuple[Any, ...]:
            """Provide the companion metadata-port method."""
            return ()

    with pytest.raises(ResearchObjectMetadataError):
        await _bindings(FailingPort()).verify(_record(payload=payload))

    class WrongPort:
        """Metadata port returning an invalid cardinality or dataset set."""

        def __init__(self, result: tuple[Any, ...]) -> None:
            self.result = result

        async def verify(self, _request: Any) -> tuple[Any, ...]:
            """Return the intentionally malformed authority result."""
            return self.result

        async def resolve(self, _request: Any) -> tuple[Any, ...]:
            """Provide the companion metadata-port method."""
            return ()

    with pytest.raises(ResearchObjectMetadataError):
        await _bindings(WrongPort(())).verify(_record(payload=payload))
    with pytest.raises(ResearchObjectMetadataError):
        await _bindings(
            WrongPort((ResearchObjectAuthority("other", "grant", snapshot),))
        ).verify(_record(payload=payload))

    changed = replace(snapshot, snapshot_digest="changed")
    with pytest.raises(ResearchObjectMetadataError):
        await _bindings(
            WrongPort(
                (ResearchObjectAuthority("dataset-1", "grant", changed),)
            )
        ).verify(_record(payload=payload))


def test_runtime_helper_contracts_reject_unsafe_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Private persisted projections reject malformed shapes before I/O."""
    obs_file_list = getattr(dispatch_runtime, "_obs_file_list")
    data_list = getattr(dispatch_runtime, "_data_list")
    grant_uses = getattr(dispatch_runtime, "_grant_uses")
    grant_bindings = getattr(dispatch_runtime, "_grant_bindings")
    snapshot_from_payload = getattr(dispatch_runtime, "_snapshot_from_payload")

    assert not obs_file_list(None)
    with pytest.raises(ResearchObjectMetadataError):
        obs_file_list("not-a-list")
    with pytest.raises(ResearchObjectMetadataError):
        obs_file_list(["valid", " "])
    assert data_list({"dataset": "description"}) == {"dataset": "description"}
    assert data_list([("dataset", "description")]) == {
        "dataset": "description"
    }
    assert not data_list(42)
    with pytest.raises(ResearchObjectMetadataError):
        grant_uses(["not-a-grant"])

    with pytest.raises(ResearchObjectMetadataError):
        grant_bindings("not-a-sequence")
    with pytest.raises(ResearchObjectMetadataError):
        grant_bindings(["not-a-grant"])
    with pytest.raises(ResearchObjectMetadataError):
        grant_bindings([{"dataset_id": "dataset-1"}])
    with pytest.raises(ResearchObjectMetadataError):
        snapshot_from_payload(None)
    with pytest.raises(ResearchObjectMetadataError):
        snapshot_from_payload({"dataset_id": "dataset-1"})
    invalid_size = research_object_snapshot_payload(_snapshot())
    invalid_size["size_bytes"] = True
    with pytest.raises(ResearchObjectMetadataError):
        snapshot_from_payload(invalid_size)

    monkeypatch.setattr(dispatch_runtime, "relay_mode_enabled", lambda: True)
    monkeypatch.setattr(dispatch_runtime, "current_relay_client", object)
    assert isinstance(
        getattr(dispatch_runtime, "_metadata_port")(),
        RelayResearchObjectMetadataPort,
    )
