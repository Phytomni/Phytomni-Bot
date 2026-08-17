# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Edge coverage for private result-archive inventory persistence."""

from __future__ import annotations

import json
from typing import cast

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

_DIGEST = "sha256:" + "a" * 64
_RUN_ROOT = "/obs/phytomni/runs/r"


def _member_data(**overrides: object) -> dict[str, object]:
    """Return one persisted member object with optional field overrides."""
    payload: dict[str, object] = {
        "archive_path": "results/part-001/report.md",
        "child_index": 1,
        "download_ref": "/obs/phytomni/runs/r/report.md",
        "media_type": "text/markdown",
        "role": "scientific_report",
        "size_bytes": 3,
    }
    payload.update(overrides)
    return payload


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
        _RUN_ROOT,
        (member,),
        inventory_digest((member,)),
        3,
    )


def _document(**overrides: object) -> dict[str, object]:
    """Return one persisted inventory document with optional overrides."""
    payload: dict[str, object] = {
        "digest": _DIGEST,
        "members": [_member_data()],
        "run_root": _RUN_ROOT,
        "total_size_bytes": 3,
    }
    payload.update(overrides)
    return payload


def test_persist_race_without_existing_object_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A create race that cannot reread the object stays retryable."""

    def _missing(*_args: object, **_kwargs: object) -> bytes:
        raise storage.ObsObjectNotFoundError("missing")

    def _raced(*_args: object, **_kwargs: object) -> str:
        raise storage.ObsObjectAlreadyExistsError("exists")

    monkeypatch.setattr(storage, "get_object_bytes", _missing)
    monkeypatch.setattr(storage, "put_object_bytes_if_absent", _raced)
    with pytest.raises(
        ResultArchiveError, match="archive_publish_failed"
    ) as captured:
        storage.persist_result_archive_inventory(
            _inventory(),
            bucket="phytomni",
            client=object(),
        )
    assert captured.value.retryable is True


def test_persist_maps_put_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transport failure during conditional create is retryable."""

    def _missing(*_args: object, **_kwargs: object) -> bytes:
        raise storage.ObsObjectNotFoundError("missing")

    def _denied(*_args: object, **_kwargs: object) -> str:
        raise OSError("denied")

    monkeypatch.setattr(storage, "get_object_bytes", _missing)
    monkeypatch.setattr(storage, "put_object_bytes_if_absent", _denied)
    with pytest.raises(
        ResultArchiveError, match="archive_publish_failed"
    ) as captured:
        storage.persist_result_archive_inventory(
            _inventory(),
            bucket="phytomni",
            client=object(),
        )
    assert captured.value.retryable is True


async def test_persist_runtime_reuses_identical_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second leased persist accepts byte-identical inventory reuse."""
    inventory = _inventory()
    objects: dict[str, bytes] = {}

    def _get(_bucket: str, key: str, **_kwargs: object) -> bytes:
        if key not in objects:
            raise storage.ObsObjectNotFoundError("missing")
        return objects[key]

    def _put(_bucket: str, key: str, content: bytes, **_kwargs: object) -> str:
        objects[key] = content
        return key

    monkeypatch.setattr(storage, "get_object_bytes", _get)
    monkeypatch.setattr(storage, "put_object_bytes_if_absent", _put)
    first = await storage.persist_result_archive_inventory_with_runtime(
        inventory,
        bucket="phytomni",
        obs_runtime=CountingObsRuntime(object()),
    )
    second = await storage.persist_result_archive_inventory_with_runtime(
        inventory,
        bucket="phytomni",
        obs_runtime=CountingObsRuntime(object()),
    )
    assert first == second
    assert len(objects) == 1


async def test_persist_runtime_race_reloads_identical_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leased create race rereads and accepts identical content."""
    inventory = _inventory()
    objects: dict[str, bytes] = {}
    calls = 0

    def _get(*_args: object, **_kwargs: object) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise storage.ObsObjectNotFoundError("missing")
        return objects["inventory"]

    def _raced(
        _bucket: str,
        _key: str,
        content: bytes,
        **_kwargs: object,
    ) -> str:
        objects["inventory"] = content
        raise storage.ObsObjectAlreadyExistsError("exists")

    monkeypatch.setattr(storage, "get_object_bytes", _get)
    monkeypatch.setattr(storage, "put_object_bytes_if_absent", _raced)
    key = await storage.persist_result_archive_inventory_with_runtime(
        inventory,
        bucket="phytomni",
        obs_runtime=CountingObsRuntime(object()),
    )
    assert key.endswith(".phytomni-result-inventory.json")
    assert calls == 2


async def test_persist_runtime_race_without_existing_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leased create race that cannot reread stays retryable."""

    def _missing(*_args: object, **_kwargs: object) -> bytes:
        raise storage.ObsObjectNotFoundError("missing")

    def _raced(*_args: object, **_kwargs: object) -> str:
        raise storage.ObsObjectAlreadyExistsError("exists")

    monkeypatch.setattr(storage, "get_object_bytes", _missing)
    monkeypatch.setattr(storage, "put_object_bytes_if_absent", _raced)
    with pytest.raises(
        ResultArchiveError, match="archive_publish_failed"
    ) as captured:
        await storage.persist_result_archive_inventory_with_runtime(
            _inventory(),
            bucket="phytomni",
            obs_runtime=CountingObsRuntime(object()),
        )
    assert captured.value.retryable is True


async def test_persist_runtime_maps_put_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leased PUT transport failure is a retryable publish error."""

    def _missing(*_args: object, **_kwargs: object) -> bytes:
        raise storage.ObsObjectNotFoundError("missing")

    def _denied(*_args: object, **_kwargs: object) -> str:
        raise OSError("denied")

    monkeypatch.setattr(storage, "get_object_bytes", _missing)
    monkeypatch.setattr(storage, "put_object_bytes_if_absent", _denied)
    with pytest.raises(
        ResultArchiveError, match="archive_publish_failed"
    ) as captured:
        await storage.persist_result_archive_inventory_with_runtime(
            _inventory(),
            bucket="phytomni",
            obs_runtime=CountingObsRuntime(object()),
        )
    assert captured.value.retryable is True


async def test_persist_runtime_maps_read_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leased inventory HEAD/GET transport failure fails closed."""

    def _denied(*_args: object, **_kwargs: object) -> bytes:
        raise OSError("denied")

    monkeypatch.setattr(storage, "get_object_bytes", _denied)
    with pytest.raises(
        ResultArchiveError, match="archive_publish_failed"
    ) as captured:
        await storage.persist_result_archive_inventory_with_runtime(
            _inventory(),
            bucket="phytomni",
            obs_runtime=CountingObsRuntime(object()),
        )
    assert captured.value.retryable is True


@pytest.mark.parametrize("run_root,digest", [(1, _DIGEST), (_RUN_ROOT, 1)])
def test_load_rejects_non_string_coordinates(
    run_root: object,
    digest: object,
) -> None:
    """Load refuses coordinates that are not strings."""
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        storage.load_result_archive_inventory(
            cast(str, run_root),
            cast(str, digest),
            bucket="phytomni",
            client=object(),
        )


@pytest.mark.parametrize(
    "payload",
    [OSError("denied"), b"\xff\xfe", b"{"],
)
def test_load_rejects_unreadable_inventory(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
) -> None:
    """Transport, encoding, and JSON failures stay contract-invalid."""

    def _get(*_args: object, **_kwargs: object) -> bytes:
        if isinstance(payload, Exception):
            raise payload
        return cast(bytes, payload)

    monkeypatch.setattr(storage, "get_object_bytes", _get)
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        storage.load_result_archive_inventory(
            _RUN_ROOT,
            _DIGEST,
            bucket="phytomni",
            client=object(),
        )


@pytest.mark.parametrize("run_root,digest", [(1, _DIGEST), (_RUN_ROOT, 1)])
async def test_load_runtime_rejects_non_string_coordinates(
    run_root: object,
    digest: object,
) -> None:
    """The leased loader refuses non-string coordinates."""
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        await storage.load_result_archive_inventory_with_runtime(
            cast(str, run_root),
            cast(str, digest),
            bucket="phytomni",
            obs_runtime=CountingObsRuntime(object()),
        )


async def test_load_runtime_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leased load revalidates the inventory it just persisted."""
    inventory = _inventory()
    objects: dict[str, bytes] = {}

    def _get(_bucket: str, key: str, **_kwargs: object) -> bytes:
        if key not in objects:
            raise storage.ObsObjectNotFoundError("missing")
        return objects[key]

    def _put(_bucket: str, key: str, content: bytes, **_kwargs: object) -> str:
        objects[key] = content
        return key

    monkeypatch.setattr(storage, "get_object_bytes", _get)
    monkeypatch.setattr(storage, "put_object_bytes_if_absent", _put)
    runtime = CountingObsRuntime(object())
    await storage.persist_result_archive_inventory_with_runtime(
        inventory,
        bucket="phytomni",
        obs_runtime=runtime,
    )
    loaded = await storage.load_result_archive_inventory_with_runtime(
        inventory.run_root,
        inventory.digest,
        bucket="phytomni",
        obs_runtime=runtime,
    )
    assert loaded == inventory


async def test_load_runtime_rejects_unreadable_and_mismatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leased load maps decode errors and identity drift."""

    def _invalid(*_args: object, **_kwargs: object) -> bytes:
        return b"{"

    monkeypatch.setattr(storage, "get_object_bytes", _invalid)
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        await storage.load_result_archive_inventory_with_runtime(
            _RUN_ROOT,
            _DIGEST,
            bucket="phytomni",
            obs_runtime=CountingObsRuntime(object()),
        )
    inventory = _inventory()
    monkeypatch.setattr(
        storage,
        "get_object_bytes",
        lambda *_args, **_kwargs: json.dumps(
            {
                "digest": inventory.digest,
                "members": [_member_data()],
                "run_root": inventory.run_root,
                "total_size_bytes": 3,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode(),
    )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        await storage.load_result_archive_inventory_with_runtime(
            "/obs/phytomni/runs/other",
            inventory.digest,
            bucket="phytomni",
            obs_runtime=CountingObsRuntime(object()),
        )


@pytest.mark.parametrize(
    "digest",
    ["sha256-missing-prefix" + "a" * 40, "sha256:" + "z" * 64],
)
def test_load_rejects_malformed_digest(digest: str) -> None:
    """Digest-addressed keys must be a lowercase sha256 hex digest."""
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        storage.load_result_archive_inventory(
            _RUN_ROOT,
            digest,
            bucket="phytomni",
            client=object(),
        )


@pytest.mark.parametrize(
    "raw",
    [
        {"digest": _DIGEST},
        _document(members=[]),
        _document(run_root=1),
        _document(digest=1),
        _document(total_size_bytes=True),
        _document(members=[None]),
        _document(members=[_member_data(role="not-a-role")]),
        _document(members=[_member_data(child_index=True)]),
        _document(members=[_member_data(size_bytes=True)]),
        _document(members=[_member_data(download_ref=1)]),
        _document(members=[_member_data(archive_path=1)]),
        _document(members=[_member_data(media_type=1)]),
    ],
)
def test_load_rejects_malformed_documents(
    monkeypatch: pytest.MonkeyPatch,
    raw: dict[str, object],
) -> None:
    """Persisted JSON must match the exact inventory contract."""
    monkeypatch.setattr(
        storage,
        "get_object_bytes",
        lambda *_args, **_kwargs: json.dumps(raw).encode(),
    )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        storage.load_result_archive_inventory(
            _RUN_ROOT,
            _DIGEST,
            bucket="phytomni",
            client=object(),
        )


def test_load_rejects_oversize_member_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persisted member list above the archive bound is rejected."""
    monkeypatch.setattr(storage, "MAX_RESULT_ARCHIVE_ARTIFACTS", 1)
    monkeypatch.setattr(
        storage,
        "get_object_bytes",
        lambda *_args, **_kwargs: json.dumps(
            _document(
                members=[
                    _member_data(),
                    _member_data(archive_path="results/part-001/other.md"),
                ]
            )
        ).encode(),
    )
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        storage.load_result_archive_inventory(
            _RUN_ROOT,
            _DIGEST,
            bucket="phytomni",
            client=object(),
        )
