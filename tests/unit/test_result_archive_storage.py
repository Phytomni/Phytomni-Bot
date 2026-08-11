# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Behavior contracts for private result archive inventory persistence."""

from __future__ import annotations

import json

import pytest

from mcp_server_phytomni.runtime.artifact_roles import ArtifactRole
from mcp_server_phytomni.runtime.outbound import ObsProfileName
from mcp_server_phytomni.runtime.result_archive import (
    ResultArchiveError,
    ResultArchiveInventory,
    ResultArchiveMember,
    inventory_digest,
)
from mcp_server_phytomni.storage import result_archive_storage as storage

pytestmark = pytest.mark.unit


class _CountingObsRuntime:
    """Record each individually leased OBS operation."""

    def __init__(self) -> None:
        """Expose one opaque runtime-owned client to operations."""
        self.calls = 0
        self.client = object()

    async def run(self, profile: ObsProfileName, operation: object) -> object:
        """Run one operation and retain the lease invocation count."""
        assert profile is ObsProfileName.PRIMARY
        self.calls += 1
        return operation(self.client)  # type: ignore[operator]


def _inventory() -> ResultArchiveInventory:
    """Build one valid immutable archive inventory fixture."""
    member = ResultArchiveMember(
        1,
        "/obs/phytomni/runs/r/report.md",
        "results/part-001/report.md",
        ArtifactRole.SCIENTIFIC_REPORT,
        "text/markdown",
        3,
    )
    return ResultArchiveInventory(
        "/obs/phytomni/runs/r",
        (member,),
        inventory_digest((member,)),
        3,
    )


async def test_persist_leases_inventory_read_and_create_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing inventory uses distinct OBS leases for GET and PUT."""
    inventory = _inventory()
    runtime = _CountingObsRuntime()
    written: list[bytes] = []

    def missing(*_args: object, **_kwargs: object) -> bytes:
        raise storage.ObsObjectNotFoundError("missing")

    monkeypatch.setattr(storage, "get_object_bytes", missing)
    monkeypatch.setattr(
        storage,
        "put_object_bytes_if_absent",
        lambda _bucket, _key, content, **_kwargs: written.append(content),
    )

    key = await storage.persist_result_archive_inventory_with_runtime(
        inventory,
        bucket="phytomni",
        obs_runtime=runtime,
    )

    assert key.endswith(".phytomni-result-inventory.json")
    assert runtime.calls == 2
    assert len(written) == 1


def test_persist_accepts_identical_content_and_load_revalidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persist one inventory idempotently and reload its validated content."""
    inventory = _inventory()
    objects: dict[str, bytes] = {}

    def get_object(_bucket: str, key: str, **_kwargs: object) -> bytes:
        if key not in objects:
            raise storage.ObsObjectNotFoundError("missing")
        return objects[key]

    monkeypatch.setattr(storage, "get_object_bytes", get_object)
    monkeypatch.setattr(
        storage,
        "put_object_bytes_if_absent",
        lambda _bucket, key, content, **_kwargs: objects.setdefault(
            key, content
        )
        or key,
    )

    key = storage.persist_result_archive_inventory(
        inventory, bucket="phytomni", client=object()
    )
    assert key.endswith(
        f"delivery/{inventory.digest.removeprefix('sha256:')}/"
        ".phytomni-result-inventory.json"
    )
    assert (
        storage.persist_result_archive_inventory(
            inventory, bucket="phytomni", client=object()
        )
        == key
    )
    assert (
        storage.load_result_archive_inventory(
            inventory.run_root,
            inventory.digest,
            bucket="phytomni",
            client=object(),
        )
        == inventory
    )


def test_persist_rejects_different_existing_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a digest-addressed object whose bytes differ from inventory."""
    inventory = _inventory()
    monkeypatch.setattr(
        storage, "get_object_bytes", lambda *_args, **_kwargs: b"{}"
    )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        storage.persist_result_archive_inventory(
            inventory, bucket="phytomni", client=object()
        )


def test_persist_fails_closed_when_inventory_read_is_not_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed instead of overwriting when inventory reads fail."""
    inventory = _inventory()
    writes: list[bytes] = []

    def denied(*_args: object, **_kwargs: object) -> bytes:
        raise OSError("permission denied")

    monkeypatch.setattr(storage, "get_object_bytes", denied)
    monkeypatch.setattr(
        storage,
        "put_object_bytes_if_absent",
        lambda *_args, **_kwargs: writes.append(b"unexpected"),
    )

    with pytest.raises(
        ResultArchiveError, match="archive_publish_failed"
    ) as exc_info:
        storage.persist_result_archive_inventory(
            inventory,
            bucket="phytomni",
            client=object(),
        )

    assert exc_info.value.retryable is True
    assert not writes


def test_persist_reloads_after_conditional_create_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accept an identical inventory written by a concurrent creator."""
    inventory = _inventory()
    calls = 0
    objects: dict[str, bytes] = {}

    def get_object(*_args: object, **_kwargs: object) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise storage.ObsObjectNotFoundError("missing")
        return objects["inventory"]

    def raced_create(
        _bucket: str,
        _key: str,
        content: bytes,
        **_kwargs: object,
    ) -> str:
        objects["inventory"] = content
        raise storage.ObsObjectAlreadyExistsError("exists")

    monkeypatch.setattr(storage, "get_object_bytes", get_object)
    monkeypatch.setattr(storage, "put_object_bytes_if_absent", raced_create)

    assert storage.persist_result_archive_inventory(
        inventory,
        bucket="phytomni",
        client=object(),
    ).endswith(".phytomni-result-inventory.json")
    assert calls == 2


def test_load_rejects_tampered_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject persisted JSON whose digest no longer matches its members."""
    inventory = _inventory()
    raw = {
        "run_root": inventory.run_root,
        "members": [
            {
                "child_index": 1,
                "download_ref": "/obs/phytomni/runs/r/report.md",
                "archive_path": "results/part-001/report.md",
                "role": "scientific_report",
                "media_type": "text/markdown",
                "size_bytes": 3,
            }
        ],
        "digest": "sha256:" + "0" * 64,
        "total_size_bytes": 3,
    }
    monkeypatch.setattr(
        storage,
        "get_object_bytes",
        lambda *_args, **_kwargs: json.dumps(raw).encode(),
    )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        storage.load_result_archive_inventory(
            inventory.run_root,
            inventory.digest,
            bucket="phytomni",
            client=object(),
        )
