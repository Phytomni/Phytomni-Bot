# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for the terminal artifact OBS listing helper."""

from __future__ import annotations

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
        "list_object_keys_page",
        lambda *_args, **_kwargs: (
            [
                "agent_data/u1/run0/summary.csv",
                "agent_data/u1/run0/figure.png",
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


async def test_async_sdk_listing_stops_list_and_heads_at_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A listing cap stops pagination and object HEADs while walking."""
    runtime = CountingObsRuntime(object())
    pages = [
        (
            [f"agent_data/u1/run0/f{index:03d}.csv" for index in range(3)],
            "next-page",
        ),
        (
            [f"agent_data/u1/run0/f{index:03d}.csv" for index in range(3, 6)],
            None,
        ),
    ]
    seen = {"pages": 0, "heads": 0}

    def fake_list_page(*_args: object, **_kwargs: object):
        seen["pages"] += 1
        return pages.pop(0)

    def fake_object_size(_bucket: str, _key: str, **_kwargs: object) -> int:
        seen["heads"] += 1
        return 37

    monkeypatch.setattr(
        artifact_listing,
        "list_object_keys_page",
        fake_list_page,
    )
    monkeypatch.setattr(artifact_listing, "object_size", fake_object_size)

    objects = await artifact_listing.list_artifact_objects_with_runtime(
        "/obs/phytomni/agent_data/u1/run0",
        bucket_name="phytomni",
        obs_runtime=runtime,
        mount_root=str(tmp_path),
        limit=3,
    )

    assert len(objects) == 3
    assert seen["pages"] == 1
    assert seen["heads"] == 3
    assert runtime.calls == 4


def test_obsfs_object_listing_stops_after_limit(tmp_path) -> None:
    """Mounted listing stops after the requested file cap."""
    bucket = "phytomni"
    mount_root = tmp_path
    run_dir = mount_root / bucket / "agent_data" / "u1" / "run0"
    run_dir.mkdir(parents=True)
    for index in range(5):
        (run_dir / f"f{index}.txt").write_bytes(b"x")

    objects = artifact_listing.list_artifact_objects(
        f"/obs/{bucket}/agent_data/u1/run0",
        bucket_name=bucket,
        mount_root=str(mount_root),
        limit=2,
    )

    assert len(objects) == 2


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
    assert objects[0].source_path.endswith(
        "/phytomni/agent_data/u1/run0/summary.csv"
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


def test_obsfs_path_listing_stops_after_limit(tmp_path) -> None:
    """Mounted path listing stops after the requested file cap."""
    bucket = "phytomni"
    run_dir = tmp_path / bucket / "agent_data" / "u1" / "run0"
    run_dir.mkdir(parents=True)
    for index in range(4):
        (run_dir / f"f{index}.txt").write_bytes(b"x")

    paths = artifact_listing.list_artifact_paths(
        f"/obs/{bucket}/agent_data/u1/run0",
        bucket_name=bucket,
        mount_root=str(tmp_path),
        limit=2,
    )

    assert len(paths) == 2


async def test_async_obsfs_listing_skips_sdk_when_mount_has_files(
    tmp_path,
) -> None:
    """A mounted output dir is listed without taking an OBS SDK lease."""
    bucket = "phytomni"
    run_dir = tmp_path / bucket / "agent_data" / "u1" / "run0"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.csv").write_bytes(b"x")
    runtime = CountingObsRuntime(object())

    objects = await artifact_listing.list_artifact_objects_with_runtime(
        f"/obs/{bucket}/agent_data/u1/run0",
        bucket_name=bucket,
        obs_runtime=runtime,
        mount_root=str(tmp_path),
        limit=1,
    )
    paths = await artifact_listing.list_artifact_paths_with_runtime(
        f"/obs/{bucket}/agent_data/u1/run0",
        bucket_name=bucket,
        obs_runtime=runtime,
        mount_root=str(tmp_path),
        limit=1,
    )

    assert [item.relative_path for item in objects] == ["summary.csv"]
    assert paths == ["/obs/phytomni/agent_data/u1/run0/summary.csv"]
    assert runtime.calls == 0


def test_sdk_path_listing_stops_pagination_at_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """SDK path listing does not fetch the next page after the cap."""
    pages = [
        (
            ["agent_data/u1/run0/a.txt", "agent_data/u1/run0/b.txt"],
            "next-page",
        ),
        (["agent_data/u1/run0/c.txt"], None),
    ]

    def fake_page(*_args: object, **_kwargs: object):
        return pages.pop(0)

    monkeypatch.setattr(artifact_listing, "list_object_keys_page", fake_page)
    paths = artifact_listing.list_artifact_paths(
        "/obs/phytomni/agent_data/u1/run0",
        bucket_name="phytomni",
        client=object(),
        mount_root=str(tmp_path),
        limit=2,
    )

    assert paths == [
        "/obs/phytomni/agent_data/u1/run0/a.txt",
        "/obs/phytomni/agent_data/u1/run0/b.txt",
    ]
    assert pages == [(["agent_data/u1/run0/c.txt"], None)]


def test_extend_keys_reports_cap_when_already_full() -> None:
    """A later page is ignored once the listing already holds the cap."""
    keys = ["a", "b"]
    assert artifact_listing._extend_keys_up_to_limit(keys, ["c"], 2) is True
    assert keys == ["a", "b"]
