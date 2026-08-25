# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for the terminal artifact OBS listing helper."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.support.outbound_fakes import CountingObsRuntime

from mcp_server_phytomni.storage import artifact_listing


async def test_async_sdk_listing_leases_list_and_each_metadata_head(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """One LIST and two object HEADs consume three distinct OBS leases."""
    runtime = CountingObsRuntime(object())

    monkeypatch.setattr(
        artifact_listing,
        "list_object_metadata_page",
        lambda *_args, **_kwargs: (
            [
                artifact_listing.ObsListedObject(
                    "agent_data/u1/run0/summary.csv", None
                ),
                artifact_listing.ObsListedObject(
                    "agent_data/u1/run0/figure.png", None
                ),
            ],
            None,
        ),
    )
    monkeypatch.setattr(
        artifact_listing,
        "object_size",
        lambda _bucket, _key, **_kwargs: 37,
    )

    objects = await artifact_listing.list_artifact_objects_with_runtime(
        "/obs/phytomni/agent_data/u1/run0",
        bucket_name="phytomni",
        obs_runtime=runtime,
        mount_root=str(tmp_path),
    )

    assert [item.relative_path for item in objects] == [
        "summary.csv",
        "figure.png",
    ]
    assert runtime.calls == 3


async def test_async_sdk_listing_uses_sizes_from_list_response(
    tmp_path,
) -> None:
    """OBS LIST metadata avoids one sequential HEAD per result object."""

    def list_objects(**_kwargs):
        return SimpleNamespace(
            status=200,
            body=SimpleNamespace(
                contents=[
                    SimpleNamespace(
                        key="agent_data/u1/run0/summary.csv", size=37
                    ),
                    SimpleNamespace(
                        key="agent_data/u1/run0/figure.png", size=41
                    ),
                ],
                is_truncated=False,
                next_marker=None,
            ),
        )

    def unexpected_head(**_kwargs):
        raise AssertionError("LIST already returned object sizes")

    client = SimpleNamespace(
        listObjects=list_objects,
        getObjectMetadata=unexpected_head,
    )
    runtime = CountingObsRuntime(client)

    objects = await artifact_listing.list_artifact_objects_with_runtime(
        "/obs/phytomni/agent_data/u1/run0",
        bucket_name="phytomni",
        obs_runtime=runtime,
        mount_root=str(tmp_path),
    )

    assert [(item.relative_path, item.size_bytes) for item in objects] == [
        ("summary.csv", 37),
        ("figure.png", 41),
    ]
    assert runtime.calls == 1


async def test_async_sdk_listing_requests_only_the_remaining_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A bounded listing does not fetch a full 1000-object SDK page."""
    requested: list[int | None] = []

    def list_page(
        _bucket,
        prefix,
        marker,
        *,
        access,
        max_keys=None,
    ):
        del prefix, access
        assert isinstance(max_keys, int)
        requested.append(max_keys)
        start = 0 if marker is None else int(marker)
        objects = [
            artifact_listing.ObsListedObject(
                f"agent_data/u1/run0/{index}.txt", index
            )
            for index in range(start, start + int(max_keys))
        ]
        return objects, str(start + int(max_keys))

    monkeypatch.setattr(
        artifact_listing, "list_object_metadata_page", list_page
    )
    runtime = CountingObsRuntime(object())

    objects = await artifact_listing.list_artifact_objects_with_runtime(
        "/obs/phytomni/agent_data/u1/run0",
        bucket_name="phytomni",
        obs_runtime=runtime,
        mount_root=str(tmp_path),
        limit=201,
    )

    assert len(objects) == 201
    assert requested == [201]


async def test_async_obsfs_listing_stops_after_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Mounted output enumeration stats at most cap+1 file objects."""
    bucket = "phytomni"
    run_dir = tmp_path / bucket / "agent_data" / "u1" / "run0"
    run_dir.mkdir(parents=True)
    for index in range(500):
        (run_dir / f"{index:04}.txt").write_bytes(b"x")
    manifest = run_dir / ".phytomni-artifacts.json"
    manifest.write_text('{"artifacts": []}', encoding="utf-8")

    original_scandir = artifact_listing.os.scandir

    class ManifestLastScandir:
        def __init__(self, path) -> None:
            with original_scandir(path) as entries:
                self._entries = sorted(
                    entries, key=lambda entry: entry.name == manifest.name
                )

        def __enter__(self):
            return iter(self._entries)

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(
        artifact_listing.os,
        "scandir",
        lambda path: ManifestLastScandir(path),
    )

    visited: list[str] = []
    original_stat = artifact_listing.Path.stat

    def counting_stat(path, *args, **kwargs):
        if path.parent == run_dir:
            visited.append(path.name)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(artifact_listing.Path, "stat", counting_stat)
    objects = await artifact_listing.list_artifact_objects_with_runtime(
        f"/obs/{bucket}/agent_data/u1/run0",
        bucket_name=bucket,
        obs_runtime=CountingObsRuntime(object()),
        mount_root=str(tmp_path),
        limit=201,
    )

    assert len(objects) == 201
    assert objects[0].relative_path == ".phytomni-artifacts.json"
    assert any(item.relative_path == manifest.name for item in objects)
    assert len(set(visited)) <= 201


def test_obsfs_branch_lists_files_as_public_paths(tmp_path):
    """When the obsfs bucket is mounted, files under output_dir are
    returned as /obs/<bucket>/<key> paths (recursive, dirs excluded)."""
    bucket = "phytomni"
    mount_root = tmp_path
    run_dir = mount_root / bucket / "agent_data" / "u1" / "run0"
    (run_dir / "sub").mkdir(parents=True)
    (run_dir / "fig1.png").write_bytes(b"x")
    (run_dir / "sub" / "table.csv").write_text("a,b")

    paths = artifact_listing.list_artifact_paths(
        f"/obs/{bucket}/agent_data/u1/run0",
        bucket_name=bucket,
        mount_root=str(mount_root),
    )

    assert sorted(paths) == [
        "/obs/phytomni/agent_data/u1/run0/fig1.png",
        "/obs/phytomni/agent_data/u1/run0/sub/table.csv",
    ]


def test_obsfs_branch_lists_objects_with_actual_sizes(tmp_path):
    """Mounted files expose stat sizes and private source paths internally."""
    bucket = "phytomni"
    mount_root = tmp_path
    run_dir = mount_root / bucket / "agent_data" / "u1" / "run0"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.csv").write_bytes(b"abcde")

    objects = artifact_listing.list_artifact_objects(
        f"/obs/{bucket}/agent_data/u1/run0",
        bucket_name=bucket,
        mount_root=str(mount_root),
    )

    assert len(objects) == 1
    assert objects[0].relative_path == "summary.csv"
    assert objects[0].size_bytes == 5
    assert Path(objects[0].source_path).parts[-5:] == (
        "phytomni",
        "agent_data",
        "u1",
        "run0",
        "summary.csv",
    )
    assert objects[0].download_ref == (
        "/obs/phytomni/agent_data/u1/run0/summary.csv"
    )


def test_obsfs_object_listing_omits_symlinks(tmp_path):
    """A symlink inside the child directory cannot enter artifact listings."""
    bucket = "phytomni"
    mount_root = tmp_path
    run_dir = mount_root / bucket / "agent_data" / "u1" / "run0"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.csv").write_bytes(b"safe")
    outside = tmp_path / "outside.csv"
    outside.write_bytes(b"private")
    (run_dir / "escaped.csv").symlink_to(outside)

    objects = artifact_listing.list_artifact_objects(
        f"/obs/{bucket}/agent_data/u1/run0",
        bucket_name=bucket,
        mount_root=str(mount_root),
    )

    assert [item.relative_path for item in objects] == ["summary.csv"]


def test_sdk_branch_used_when_obsfs_absent(monkeypatch, tmp_path):
    """With no obsfs mount, the SDK list path is used and its keys are
    converted to public paths."""
    calls = {}

    def fake_list_object_keys(bucket, prefix, *, access):
        calls["args"] = (bucket, prefix, access.client)
        return ["agent_data/u1/run0/fig1.png", "agent_data/u1/run0/x.txt"]

    monkeypatch.setattr(
        artifact_listing, "list_object_keys", fake_list_object_keys
    )

    paths = artifact_listing.list_artifact_paths(
        "/obs/phytomni/agent_data/u1/run0",
        bucket_name="phytomni",
        client=object(),
        mount_root=str(tmp_path),  # exists but no <mount>/phytomni dir
    )

    assert paths == [
        "/obs/phytomni/agent_data/u1/run0/fig1.png",
        "/obs/phytomni/agent_data/u1/run0/x.txt",
    ]
    assert calls["args"][0] == "phytomni"
    assert calls["args"][1] == "agent_data/u1/run0"


def test_sdk_branch_lists_objects_using_head_sizes(monkeypatch, tmp_path):
    """SDK object listings obtain actual sizes through the metadata seam."""
    monkeypatch.setattr(
        artifact_listing,
        "list_object_keys",
        lambda *args, **kwargs: [
            "agent_data/u1/run0/summary.csv",
            "agent_data/u1/other/secret.csv",
        ],
    )
    sizes = {}

    def fake_object_size(bucket, key, *, access):
        sizes[(bucket, key, access.client, access.mount_root)] = True
        return 37

    monkeypatch.setattr(artifact_listing, "object_size", fake_object_size)

    client = object()
    objects = artifact_listing.list_artifact_objects(
        "/obs/phytomni/agent_data/u1/run0",
        bucket_name="phytomni",
        client=client,
        mount_root=str(tmp_path),
    )

    assert [(item.relative_path, item.size_bytes) for item in objects] == [
        ("summary.csv", 37)
    ]
    assert list(sizes) == [
        (
            "phytomni",
            "agent_data/u1/run0/summary.csv",
            client,
            str(tmp_path),
        )
    ]


def test_obsfs_mounted_but_dir_absent_falls_back_to_sdk(monkeypatch, tmp_path):
    """obsfs bucket mounted but the specific run dir missing -> SDK list."""
    bucket = "phytomni"
    (tmp_path / bucket).mkdir()  # bucket root exists, run dir does not
    monkeypatch.setattr(
        artifact_listing,
        "list_object_keys",
        lambda *a, **k: ["agent_data/u1/run0/fig1.png"],
    )

    paths = artifact_listing.list_artifact_paths(
        "/obs/phytomni/agent_data/u1/run0",
        bucket_name=bucket,
        client=object(),
        mount_root=str(tmp_path),
    )

    assert paths == ["/obs/phytomni/agent_data/u1/run0/fig1.png"]


def test_obsfs_enumeration_confines_to_requested_tenant_prefix(tmp_path):
    """Enumeration is scoped to output_dir's key, not the bucket mount root.

    A run's output_dir resolves to one tenant-scoped key prefix; the
    recursive walk must return only that run's objects and never a
    sibling tenant's objects living elsewhere under the same bucket."""
    bucket = "phytomni"
    mount_root = tmp_path
    mine = mount_root / bucket / "agent_data" / "user_data" / "ua" / "run0"
    mine.mkdir(parents=True)
    (mine / "mine.png").write_bytes(b"x")
    sibling = mount_root / bucket / "agent_data" / "user_data" / "ub" / "run9"
    sibling.mkdir(parents=True)
    (sibling / "secret.csv").write_text("a,b")

    paths = artifact_listing.list_artifact_paths(
        f"/obs/{bucket}/agent_data/user_data/ua/run0",
        bucket_name=bucket,
        client=object(),
        mount_root=str(mount_root),
    )

    assert paths == ["/obs/phytomni/agent_data/user_data/ua/run0/mine.png"]
    assert all("/ub/" not in path for path in paths)
