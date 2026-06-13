# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for the terminal artifact OBS listing helper."""

from __future__ import annotations

from mcp_server_phytomni.storage import artifact_listing


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
        obs_server="https://obs.example",
        mount_root=str(mount_root),
    )

    assert sorted(paths) == [
        "/obs/phytomni/agent_data/u1/run0/fig1.png",
        "/obs/phytomni/agent_data/u1/run0/sub/table.csv",
    ]


def test_sdk_branch_used_when_obsfs_absent(monkeypatch, tmp_path):
    """With no obsfs mount, the SDK list path is used and its keys are
    converted to public paths."""
    calls = {}

    def fake_list_object_keys(bucket, prefix, *, obs_server):
        calls["args"] = (bucket, prefix, obs_server)
        return ["agent_data/u1/run0/fig1.png", "agent_data/u1/run0/x.txt"]

    monkeypatch.setattr(
        artifact_listing, "list_object_keys", fake_list_object_keys
    )

    paths = artifact_listing.list_artifact_paths(
        "/obs/phytomni/agent_data/u1/run0",
        bucket_name="phytomni",
        obs_server="https://obs.example",
        mount_root=str(tmp_path),  # exists but no <mount>/phytomni dir
    )

    assert paths == [
        "/obs/phytomni/agent_data/u1/run0/fig1.png",
        "/obs/phytomni/agent_data/u1/run0/x.txt",
    ]
    assert calls["args"][0] == "phytomni"
    assert calls["args"][1] == "agent_data/u1/run0"


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
        obs_server="https://obs.example",
        mount_root=str(tmp_path),
    )

    assert paths == ["/obs/phytomni/agent_data/u1/run0/fig1.png"]
