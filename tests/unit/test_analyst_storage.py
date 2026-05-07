# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for analyst storage obsfs-first behavior."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni import analyst_storage
from mcp_server_phytomni.path_policy import IdFactory, RunIdentity

pytestmark = pytest.mark.unit


def _obsfs_root(tmp_path: Path) -> Path:
    """Create and return a fake obsfs bucket root."""
    root = tmp_path / "phytomni"
    root.mkdir()
    return root


def test_create_output_dir_prefers_obsfs(tmp_path):
    """Verify output directories are created through obsfs first."""
    root = _obsfs_root(tmp_path)
    run_identity = RunIdentity.create(
        "user-a",
        "analysis_task",
        IdFactory(token_factory=lambda _: "abc12345"),
    )

    result = analyst_storage.create_output_dir(
        "user-a",
        "analysis_task",
        bucket_name="phytomni",
        obsfs_mount_root=str(tmp_path),
        run_identity=run_identity,
    )

    assert result == (
        f"/obs/phytomni/agent_data/user_data/user-a/runs/"
        f"{run_identity.date_stamp}/{run_identity.run_id}/"
        "analysis_task/output/"
    )
    object_key = analyst_storage.normalize_obs_object_key(
        result,
        "phytomni",
    )
    assert (root / object_key).is_dir()


def test_upload_analyst_agents_content_prefers_obsfs(tmp_path):
    """Verify generated metadata content is written through obsfs."""
    root = _obsfs_root(tmp_path)

    result = analyst_storage.upload_analyst_agents_content(
        '{"ok": true}',
        "submit.json",
        bucket_name="phytomni",
        obsfs_mount_root=str(tmp_path),
    )

    assert result == "phytomni:/agent_data/tmp_data/submit.json"
    assert (root / "agent_data" / "tmp_data" / "submit.json").read_text(
        encoding="utf-8"
    ) == '{"ok": true}'


def test_upload_analyst_agents_data_prefers_obsfs_copy(tmp_path):
    """Verify local metadata files are copied through obsfs."""
    root = _obsfs_root(tmp_path)
    source_file = tmp_path / "source.json"
    source_file.write_text("payload", encoding="utf-8")

    result = analyst_storage.upload_analyst_agents_data(
        str(source_file),
        bucket_name="phytomni",
        obsfs_mount_root=str(tmp_path),
    )

    assert result == "phytomni:/agent_data/tmp_data/source.json"
    assert (root / "agent_data" / "tmp_data" / "source.json").read_text(
        encoding="utf-8"
    ) == "payload"


def test_delete_analyst_agents_data_prefers_obsfs(tmp_path):
    """Verify delete removes files through obsfs first."""
    root = _obsfs_root(tmp_path)
    target_file = root / "agent_data" / "tmp_data" / "delete.json"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("payload", encoding="utf-8")

    result = analyst_storage.delete_analyst_agents_data(
        "/obs/phytomni/agent_data/tmp_data/delete.json",
        bucket_name="phytomni",
        obsfs_mount_root=str(tmp_path),
    )

    assert "Delete Object Succeeded" in result
    assert not target_file.exists()


def test_download_obs_out_prefers_obsfs_and_filters_outputs(tmp_path):
    """Verify result downloads copy matching files from obsfs first."""
    root = _obsfs_root(tmp_path)
    result_dir = root / "results"
    result_dir.mkdir()
    (result_dir / "keep.txt").write_text("keep", encoding="utf-8")
    (result_dir / "skip.log").write_text("skip", encoding="utf-8")

    statuses = list(
        analyst_storage.download_obs_out(
            "task-1",
            "results",
            download_path=str(tmp_path / "downloads"),
            bucket_name="phytomni",
            obsfs_mount_root=str(tmp_path),
            target_file_feature=[".txt"],
            if_download_all=False,
        )
    )

    assert statuses == ["keep.txt download succeed."]
    assert (tmp_path / "downloads" / "task-1" / "keep.txt").read_text(
        encoding="utf-8"
    ) == "keep"
    assert not (tmp_path / "downloads" / "task-1" / "skip.log").exists()


def test_upload_content_falls_back_to_sdk_when_obsfs_missing(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify content uploads fall back to the OBS SDK."""
    captured: dict[str, Any] = {}

    class FakeObsClient:
        """Minimal OBS client for putContent fallback."""

        def __init__(self, **kwargs: Any):
            captured["client"] = kwargs

        def __getattr__(self, name: str):
            """Map OBS SDK camelCase methods to lint-friendly fakes."""
            if name == "putContent":
                return self.put_content
            raise AttributeError(name)

        def put_content(self, **kwargs: Any):
            """Capture putContent fallback arguments."""
            captured["put_content"] = kwargs
            return SimpleNamespace(status=200, requestId="request-id")

    monkeypatch.setattr(analyst_storage, "ObsClient", FakeObsClient)

    result = analyst_storage.upload_analyst_agents_content(
        "payload",
        "submit.json",
        bucket_name="phytomni",
        obsfs_mount_root=str(tmp_path / "missing"),
    )

    assert result == "phytomni:/agent_data/tmp_data/submit.json"
    assert captured["put_content"]["content"] == "payload"
    assert (
        captured["put_content"]["objectKey"]
        == "agent_data/tmp_data/submit.json"
    )


def test_download_obs_out_falls_back_to_sdk_when_obsfs_missing(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify result downloads fall back to OBS SDK listing and getObject."""

    class FakeObsClient:
        """Minimal OBS client for list/get fallback."""

        def __init__(self, **kwargs: Any):
            del kwargs

        def __getattr__(self, name: str):
            """Map OBS SDK camelCase methods to lint-friendly fakes."""
            if name == "listObjects":
                return self.list_objects
            if name == "getObject":
                return self.get_object
            raise AttributeError(name)

        def list_objects(self, **kwargs: Any):
            """Return one listed object for SDK fallback."""
            assert kwargs["prefix"] == "results"
            body = SimpleNamespace(
                contents=[SimpleNamespace(key="results/keep.txt")],
                is_truncated=False,
            )
            return SimpleNamespace(status=200, body=body)

        def get_object(self, **kwargs: Any):
            """Write one fake downloaded object."""
            Path(kwargs["downloadPath"]).write_text("sdk", encoding="utf-8")
            return SimpleNamespace(status=200)

    monkeypatch.setattr(analyst_storage, "ObsClient", FakeObsClient)

    statuses = list(
        analyst_storage.download_obs_out(
            "task-1",
            "/obs/phytomni/results",
            download_path=str(tmp_path / "downloads"),
            bucket_name="phytomni",
            obsfs_mount_root=str(tmp_path / "missing"),
            if_download_all=True,
        )
    )

    assert statuses == ["keep.txt download succeed."]
    assert (tmp_path / "downloads" / "task-1" / "keep.txt").read_text(
        encoding="utf-8"
    ) == "sdk"
