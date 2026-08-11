# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Private persistence for immutable result archive inventories."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ..runtime.artifact_roles import ArtifactRole
from ..runtime.outbound import ObsProfileName
from ..runtime.result_archive import (
    MAX_RESULT_ARCHIVE_ARTIFACTS,
    ResultArchiveError,
    ResultArchiveInventory,
    ResultArchiveMember,
    result_archive_member_to_data,
    validate_result_archive_inventory,
)
from .obs_relay_ops import (
    ObsAccessOptions,
    ObsObjectAlreadyExistsError,
    ObsObjectNotFoundError,
    get_object_bytes,
    put_object_bytes_if_absent,
)

__all__ = [
    "load_result_archive_inventory",
    "load_result_archive_inventory_with_runtime",
    "persist_result_archive_inventory",
    "persist_result_archive_inventory_with_runtime",
]


def persist_result_archive_inventory(
    inventory: ResultArchiveInventory,
    *,
    bucket: str,
    client: Any,
) -> str:
    """Write one private inventory, accepting only byte-identical reuse."""
    validate_result_archive_inventory(inventory)
    object_key = _inventory_key(inventory.run_root, inventory.digest)
    content = _serialize_inventory(inventory)
    access = ObsAccessOptions(client=client)
    existing = _read_existing_inventory(
        bucket,
        object_key,
        access=access,
    )
    if existing is not None:
        _require_identical_inventory(existing, content)
        return object_key
    try:
        put_object_bytes_if_absent(
            bucket,
            object_key,
            content,
            access=access,
        )
    except ObsObjectAlreadyExistsError as exc:
        existing = _read_existing_inventory(
            bucket,
            object_key,
            access=access,
        )
        if existing is None:
            raise ResultArchiveError(
                "archive_publish_failed", retryable=True
            ) from exc
        _require_identical_inventory(existing, content)
    except OSError:
        raise ResultArchiveError(
            "archive_publish_failed", retryable=True
        ) from None
    return object_key


async def persist_result_archive_inventory_with_runtime(
    inventory: ResultArchiveInventory,
    *,
    bucket: str,
    obs_runtime: Any,
) -> str:
    """Persist one inventory with a separate lease for every OBS attempt."""
    validate_result_archive_inventory(inventory)
    object_key = _inventory_key(inventory.run_root, inventory.digest)
    content = _serialize_inventory(inventory)
    existing = await _read_existing_inventory_with_runtime(
        bucket,
        object_key,
        obs_runtime=obs_runtime,
    )
    if existing is not None:
        _require_identical_inventory(existing, content)
        return object_key
    try:
        await obs_runtime.run(
            ObsProfileName.PRIMARY,
            lambda client: put_object_bytes_if_absent(
                bucket,
                object_key,
                content,
                access=ObsAccessOptions(client=client),
            ),
        )
    except ObsObjectAlreadyExistsError as exc:
        existing = await _read_existing_inventory_with_runtime(
            bucket,
            object_key,
            obs_runtime=obs_runtime,
        )
        if existing is None:
            raise ResultArchiveError(
                "archive_publish_failed", retryable=True
            ) from exc
        _require_identical_inventory(existing, content)
    except OSError:
        raise ResultArchiveError(
            "archive_publish_failed", retryable=True
        ) from None
    return object_key


def load_result_archive_inventory(
    run_root: str,
    digest: str,
    *,
    bucket: str,
    client: Any,
) -> ResultArchiveInventory:
    """Load one private inventory and reject any malformed or changed field."""
    if not isinstance(run_root, str) or not isinstance(digest, str):
        raise ResultArchiveError("archive_contract_invalid")
    try:
        access = ObsAccessOptions(client=client)
        raw = get_object_bytes(
            bucket,
            _inventory_key(run_root, digest),
            access=access,
        )
        decoded = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ResultArchiveError("archive_contract_invalid") from None
    inventory = _inventory_from_data(decoded)
    if inventory.run_root != run_root or inventory.digest != digest:
        raise ResultArchiveError("archive_contract_invalid")
    return validate_result_archive_inventory(inventory)


async def load_result_archive_inventory_with_runtime(
    run_root: str,
    digest: str,
    *,
    bucket: str,
    obs_runtime: Any,
) -> ResultArchiveInventory:
    """Load one inventory with its single OBS read separately leased."""
    if not isinstance(run_root, str) or not isinstance(digest, str):
        raise ResultArchiveError("archive_contract_invalid")
    try:
        object_key = _inventory_key(run_root, digest)
        raw = await obs_runtime.run(
            ObsProfileName.PRIMARY,
            lambda client: get_object_bytes(
                bucket,
                object_key,
                access=ObsAccessOptions(client=client),
            ),
        )
        decoded = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ResultArchiveError("archive_contract_invalid") from None
    inventory = _inventory_from_data(decoded)
    if inventory.run_root != run_root or inventory.digest != digest:
        raise ResultArchiveError("archive_contract_invalid")
    return validate_result_archive_inventory(inventory)


def _inventory_key(run_root: str, digest: str) -> str:
    """Return the digest-addressed private inventory object path."""
    if (
        not isinstance(run_root, str)
        or not isinstance(digest, str)
        or not digest.startswith("sha256:")
    ):
        raise ResultArchiveError("archive_contract_invalid")
    digest_hex = digest.removeprefix("sha256:")
    if len(digest_hex) != 64 or any(
        char not in "0123456789abcdef" for char in digest_hex
    ):
        raise ResultArchiveError("archive_contract_invalid")
    return (
        f"{run_root.rstrip('/')}/delivery/{digest_hex}/"
        ".phytomni-result-inventory.json"
    )


def _read_existing_inventory(
    bucket: str,
    object_key: str,
    *,
    access: ObsAccessOptions,
) -> bytes | None:
    """Read one inventory, treating only a confirmed 404 as absence."""
    try:
        return get_object_bytes(bucket, object_key, access=access)
    except ObsObjectNotFoundError:
        return None
    except OSError:
        raise ResultArchiveError(
            "archive_publish_failed", retryable=True
        ) from None


async def _read_existing_inventory_with_runtime(
    bucket: str,
    object_key: str,
    *,
    obs_runtime: Any,
) -> bytes | None:
    """Read one inventory under its own OBS lease."""
    try:
        return await obs_runtime.run(
            ObsProfileName.PRIMARY,
            lambda client: get_object_bytes(
                bucket,
                object_key,
                access=ObsAccessOptions(client=client),
            ),
        )
    except ObsObjectNotFoundError:
        return None
    except OSError:
        raise ResultArchiveError(
            "archive_publish_failed", retryable=True
        ) from None


def _require_identical_inventory(existing: bytes, content: bytes) -> None:
    """Reject a digest-key collision whose immutable bytes differ."""
    if existing != content:
        raise ResultArchiveError("archive_contract_invalid")


def _serialize_inventory(inventory: ResultArchiveInventory) -> bytes:
    """Encode bounded inventory without exposing a local source path."""
    return json.dumps(
        {
            "digest": inventory.digest,
            "members": [
                result_archive_member_to_data(member)
                for member in inventory.members
            ],
            "run_root": inventory.run_root,
            "total_size_bytes": inventory.total_size_bytes,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _inventory_from_data(value: object) -> ResultArchiveInventory:
    """Convert persisted JSON into immutable inventory dataclasses."""
    if not isinstance(value, Mapping) or set(value) != {
        "digest",
        "members",
        "run_root",
        "total_size_bytes",
    }:
        raise ResultArchiveError("archive_contract_invalid")
    raw_members = value.get("members")
    if (
        not isinstance(raw_members, list)
        or not raw_members
        or len(raw_members) > MAX_RESULT_ARCHIVE_ARTIFACTS
    ):
        raise ResultArchiveError("archive_contract_invalid")
    members = tuple(_member_from_data(raw) for raw in raw_members)
    run_root = value.get("run_root")
    digest = value.get("digest")
    total_size_bytes = value.get("total_size_bytes")
    if (
        not isinstance(run_root, str)
        or not isinstance(digest, str)
        or isinstance(total_size_bytes, bool)
        or not isinstance(total_size_bytes, int)
    ):
        raise ResultArchiveError("archive_contract_invalid")
    return ResultArchiveInventory(run_root, members, digest, total_size_bytes)


def _member_from_data(value: object) -> ResultArchiveMember:
    """Convert one exact persisted member JSON object."""
    required = {
        "archive_path",
        "child_index",
        "download_ref",
        "media_type",
        "role",
        "size_bytes",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise ResultArchiveError("archive_contract_invalid")
    try:
        role = ArtifactRole(value["role"])
    except (KeyError, TypeError, ValueError):
        raise ResultArchiveError("archive_contract_invalid") from None
    child_index = value.get("child_index")
    size_bytes = value.get("size_bytes")
    download_ref = value.get("download_ref")
    archive_path = value.get("archive_path")
    media_type = value.get("media_type")
    (
        child_index,
        size_bytes,
        download_ref,
        archive_path,
        media_type,
    ) = _validate_member_fields(
        child_index,
        size_bytes,
        download_ref,
        archive_path,
        media_type,
    )
    return ResultArchiveMember(
        child_index, download_ref, archive_path, role, media_type, size_bytes
    )


def _validate_member_fields(
    child_index: object,
    size_bytes: object,
    download_ref: object,
    archive_path: object,
    media_type: object,
) -> tuple[int, int, str, str, str]:
    """Reject malformed dynamic member fields before typed construction."""
    if isinstance(child_index, bool) or not isinstance(child_index, int):
        raise ResultArchiveError("archive_contract_invalid")
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int):
        raise ResultArchiveError("archive_contract_invalid")
    if not isinstance(download_ref, str):
        raise ResultArchiveError("archive_contract_invalid")
    if not isinstance(archive_path, str):
        raise ResultArchiveError("archive_contract_invalid")
    if not isinstance(media_type, str):
        raise ResultArchiveError("archive_contract_invalid")
    return child_index, size_bytes, download_ref, archive_path, media_type
