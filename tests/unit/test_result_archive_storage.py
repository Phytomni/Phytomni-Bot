# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Behavior contracts for private result archive inventory persistence."""

# pylint: disable=protected-access, too-few-public-methods

from __future__ import annotations

import json

import pytest
from tests.support.outbound_fakes import CountingObsRuntime

from mcp_server_phytomni.runtime.artifact_roles import ArtifactRole
from mcp_server_phytomni.runtime.result_archive import (
    ResultArchiveError,
    ResultArchiveInventory,
    ResultArchiveMember,
    inventory_digest,
)
from mcp_server_phytomni.storage import result_archive_storage as storage

pytestmark = pytest.mark.unit


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
    runtime = CountingObsRuntime(object())
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


def test_persist_fails_when_race_reload_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A create race without a readable inventory stays retryable."""

    def missing(*_args: object, **_kwargs: object) -> bytes:
        raise storage.ObsObjectNotFoundError("missing")

    def raced(*_args: object, **_kwargs: object) -> str:
        raise storage.ObsObjectAlreadyExistsError("exists")

    monkeypatch.setattr(storage, "get_object_bytes", missing)
    monkeypatch.setattr(storage, "put_object_bytes_if_absent", raced)
    with pytest.raises(
        ResultArchiveError, match="archive_publish_failed"
    ) as captured:
        storage.persist_result_archive_inventory(
            _inventory(), bucket="phytomni", client=object()
        )
    assert captured.value.retryable is True


def test_persist_maps_put_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transport failure during create becomes retryable publish failure."""

    def missing(*_args: object, **_kwargs: object) -> bytes:
        raise storage.ObsObjectNotFoundError("missing")

    def denied(*_args: object, **_kwargs: object) -> str:
        raise OSError("denied")

    monkeypatch.setattr(storage, "get_object_bytes", missing)
    monkeypatch.setattr(storage, "put_object_bytes_if_absent", denied)
    with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
        storage.persist_result_archive_inventory(
            _inventory(), bucket="phytomni", client=object()
        )


async def test_persist_runtime_reuses_identical_and_rejects_races(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime persist covers reuse, race reload, and transport failure."""
    inventory = _inventory()
    runtime = CountingObsRuntime(object())
    content = storage._serialize_inventory(inventory)
    monkeypatch.setattr(
        storage, "get_object_bytes", lambda *_args, **_kwargs: content
    )
    assert (
        await storage.persist_result_archive_inventory_with_runtime(
            inventory, bucket="phytomni", obs_runtime=runtime
        )
    ).endswith(".phytomni-result-inventory.json")

    def missing(*_args: object, **_kwargs: object) -> bytes:
        raise storage.ObsObjectNotFoundError("missing")

    def raced(*_args: object, **_kwargs: object) -> str:
        raise storage.ObsObjectAlreadyExistsError("exists")

    monkeypatch.setattr(storage, "get_object_bytes", missing)
    monkeypatch.setattr(storage, "put_object_bytes_if_absent", raced)
    with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
        await storage.persist_result_archive_inventory_with_runtime(
            inventory, bucket="phytomni", obs_runtime=runtime
        )
    calls = {"n": 0}

    def get_after_race(*_args: object, **_kwargs: object) -> bytes:
        calls["n"] += 1
        if calls["n"] == 1:
            raise storage.ObsObjectNotFoundError("missing")
        return content

    monkeypatch.setattr(storage, "get_object_bytes", get_after_race)
    assert (
        await storage.persist_result_archive_inventory_with_runtime(
            inventory, bucket="phytomni", obs_runtime=runtime
        )
    ).endswith(".phytomni-result-inventory.json")

    def denied(*_args: object, **_kwargs: object) -> str:
        raise OSError("denied")

    monkeypatch.setattr(storage, "get_object_bytes", missing)
    monkeypatch.setattr(storage, "put_object_bytes_if_absent", denied)
    with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
        await storage.persist_result_archive_inventory_with_runtime(
            inventory, bucket="phytomni", obs_runtime=runtime
        )


async def test_load_runtime_revalidates_and_rejects_bad_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime load accepts a matching inventory and rejects bad inputs."""
    inventory = _inventory()
    runtime = CountingObsRuntime(object())
    monkeypatch.setattr(
        storage,
        "get_object_bytes",
        lambda *_args, **_kwargs: storage._serialize_inventory(inventory),
    )
    loaded = await storage.load_result_archive_inventory_with_runtime(
        inventory.run_root,
        inventory.digest,
        bucket="phytomni",
        obs_runtime=runtime,
    )
    assert loaded == inventory
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        await storage.load_result_archive_inventory_with_runtime(
            1,  # type: ignore[arg-type]
            inventory.digest,
            bucket="phytomni",
            obs_runtime=runtime,
        )
    monkeypatch.setattr(storage, "get_object_bytes", lambda *_a, **_k: b"{")
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        await storage.load_result_archive_inventory_with_runtime(
            inventory.run_root,
            inventory.digest,
            bucket="phytomni",
            obs_runtime=runtime,
        )
    raw = json.loads(storage._serialize_inventory(inventory))
    raw["run_root"] = "/obs/other"
    monkeypatch.setattr(
        storage,
        "get_object_bytes",
        lambda *_args, **_kwargs: json.dumps(raw).encode(),
    )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        await storage.load_result_archive_inventory_with_runtime(
            inventory.run_root,
            inventory.digest,
            bucket="phytomni",
            obs_runtime=runtime,
        )


def test_load_and_key_and_member_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load, key, and member parsers reject malformed coordinates."""
    inventory = _inventory()
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        storage.load_result_archive_inventory(
            None,  # type: ignore[arg-type]
            inventory.digest,
            bucket="phytomni",
            client=object(),
        )
    monkeypatch.setattr(storage, "get_object_bytes", lambda *_a, **_k: b"{")
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        storage.load_result_archive_inventory(
            inventory.run_root,
            inventory.digest,
            bucket="phytomni",
            client=object(),
        )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        storage._inventory_key("/obs/phytomni/runs/r", "md5:abcd")
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        storage._inventory_key("/obs/phytomni/runs/r", "sha256:" + "g" * 64)
    for payload in (
        {"digest": "x"},
        {
            "digest": "sha256:" + "0" * 64,
            "members": [],
            "run_root": "/obs/phytomni/runs/r",
            "total_size_bytes": 3,
        },
        {
            "digest": "sha256:" + "0" * 64,
            "members": [{}],
            "run_root": 1,
            "total_size_bytes": 3,
        },
        {
            "digest": "sha256:" + "0" * 64,
            "members": [{}],
            "run_root": "/obs/phytomni/runs/r",
            "total_size_bytes": True,
        },
    ):
        with pytest.raises(
            ResultArchiveError, match="archive_contract_invalid"
        ):
            storage._inventory_from_data(payload)
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        storage._member_from_data({"role": "scientific_report"})
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        storage._member_from_data(
            {
                "archive_path": "results/part-001/report.md",
                "child_index": 1,
                "download_ref": "/obs/x",
                "media_type": "text/plain",
                "role": "not-a-role",
                "size_bytes": 3,
            }
        )
    for fields in (
        (True, 3, "ref", "path", "text/plain"),
        (1, True, "ref", "path", "text/plain"),
        (1, 3, 1, "path", "text/plain"),
        (1, 3, "ref", 1, "text/plain"),
        (1, 3, "ref", "path", 1),
    ):
        with pytest.raises(
            ResultArchiveError, match="archive_contract_invalid"
        ):
            storage._validate_member_fields(*fields)
    member = {
        "archive_path": "results/part-001/report.md",
        "child_index": 1,
        "download_ref": "/obs/x",
        "media_type": "text/plain",
        "role": "scientific_report",
        "size_bytes": 3,
    }
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        storage._inventory_from_data(
            {
                "digest": "sha256:" + "0" * 64,
                "members": [member] * 201,
                "run_root": "/obs/phytomni/runs/r",
                "total_size_bytes": 3,
            }
        )


async def test_runtime_existing_read_maps_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-404 inventory read through the runtime stays retryable."""

    def denied(*_args: object, **_kwargs: object) -> bytes:
        raise OSError("denied")

    monkeypatch.setattr(storage, "get_object_bytes", denied)
    with pytest.raises(ResultArchiveError, match="archive_publish_failed"):
        await storage._read_existing_inventory_with_runtime(
            "phytomni",
            "owner/key",
            obs_runtime=CountingObsRuntime(object()),
        )
