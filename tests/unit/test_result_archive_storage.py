"""Behavior contracts for private result archive inventory persistence."""

from __future__ import annotations

import json

import pytest

from mcp_server_phytomni.runtime.artifact_roles import ArtifactRole
from mcp_server_phytomni.runtime.result_archive import (
    ResultArchiveError,
    ResultArchiveInventory,
    ResultArchiveMember,
)
from mcp_server_phytomni.storage import result_archive_storage as storage

pytestmark = pytest.mark.unit


def _inventory() -> ResultArchiveInventory:
    member = ResultArchiveMember(1, "/obs/phytomni/runs/r/report.md", "results/part-001/report.md", ArtifactRole.SCIENTIFIC_REPORT, "text/markdown", 3)
    from mcp_server_phytomni.runtime.result_archive import inventory_digest
    return ResultArchiveInventory("/obs/phytomni/runs/r", (member,), inventory_digest((member,)), 3)


def test_persist_accepts_identical_content_and_load_revalidates(monkeypatch: pytest.MonkeyPatch) -> None:
    inventory = _inventory()
    objects: dict[str, bytes] = {}
    def get_object(_bucket: str, key: str, **_kwargs: object) -> bytes:
        if key not in objects:
            raise OSError("not found")
        return objects[key]

    monkeypatch.setattr(storage, "get_object_bytes", get_object)
    monkeypatch.setattr(storage, "put_object_bytes", lambda _bucket, key, content, **_kwargs: objects.setdefault(key, content) or key)

    key = storage.persist_result_archive_inventory(inventory, bucket="phytomni", obs_server="https://obs.example")
    assert key.endswith(f"delivery/{inventory.digest.removeprefix('sha256:')}/.phytomni-result-inventory.json")
    assert storage.persist_result_archive_inventory(inventory, bucket="phytomni", obs_server="https://obs.example") == key
    assert storage.load_result_archive_inventory(inventory.run_root, inventory.digest, bucket="phytomni", obs_server="https://obs.example") == inventory


def test_persist_rejects_different_existing_content(monkeypatch: pytest.MonkeyPatch) -> None:
    inventory = _inventory()
    monkeypatch.setattr(storage, "get_object_bytes", lambda *_args, **_kwargs: b"{}")
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        storage.persist_result_archive_inventory(inventory, bucket="phytomni", obs_server="https://obs.example")


def test_load_rejects_tampered_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    inventory = _inventory()
    raw = {"run_root": inventory.run_root, "members": [{"child_index": 1, "download_ref": "/obs/phytomni/runs/r/report.md", "archive_path": "results/part-001/report.md", "role": "scientific_report", "media_type": "text/markdown", "size_bytes": 3}], "digest": "sha256:" + "0" * 64, "total_size_bytes": 3}
    monkeypatch.setattr(storage, "get_object_bytes", lambda *_args, **_kwargs: json.dumps(raw).encode())
    with pytest.raises(ResultArchiveError, match="archive_contract_invalid"):
        storage.load_result_archive_inventory(inventory.run_root, inventory.digest, bucket="phytomni", obs_server="https://obs.example")
