# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for analyst storage obsfs-first behavior.

Covers output directory creation, OBS content/file upload, deletion, download
filtering, and SDK fallback paths when obsfs is unavailable.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from mcp_server_phytomni.agents.analyst import storage as analyst_storage
from mcp_server_phytomni.agents.shared import analysis_storage
from mcp_server_phytomni.storage.obs_storage import normalize_obs_object_key
from mcp_server_phytomni.storage.path_policy import IdFactory, RunIdentity

pytestmark = pytest.mark.unit


def _obsfs_root(tmp_path: Path) -> Path:
    """Create and return a fake obsfs bucket root."""
    root = tmp_path / "phytomni"
    root.mkdir()
    return root


def test_create_output_dir_prefers_obsfs(tmp_path):
    """Verify output directories are created through obsfs first.

    Args:
        tmp_path: Temporary obsfs mount root.
    """
    root = _obsfs_root(tmp_path)
    run_identity = RunIdentity.create(
        "user-a",
        "analysis_task",
        IdFactory(token_factory=lambda _: "abc12345"),
    )

    result = analysis_storage.create_output_dir(
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
    object_key = normalize_obs_object_key(
        result,
        "phytomni",
    )
    assert (root / object_key).is_dir()


def test_create_output_dir_relay_mode_skips_marker(tmp_path, monkeypatch):
    """Relay mode returns the run-scoped path without minting a marker.

    Args:
        tmp_path: Temporary directory standing in for an absent mount.
        monkeypatch: Pytest monkeypatch toggling relay mode and the SDK.
    """
    monkeypatch.setattr(analysis_storage, "relay_mode_enabled", lambda: True)
    no_sdk = Mock()
    monkeypatch.setattr(analysis_storage, "ObsClient", no_sdk)
    run_identity = RunIdentity.create(
        "user-a",
        "analysis_task",
        IdFactory(token_factory=lambda _: "abc12345"),
    )

    result = analysis_storage.create_output_dir(
        "user-a",
        "analysis_task",
        bucket_name="phytomni",
        obsfs_mount_root=str(tmp_path / "no-mount"),
        run_identity=run_identity,
    )

    assert result.startswith("/obs/phytomni/")
    assert result.endswith("/analysis_task/output/")
    assert not no_sdk.called
    assert not (tmp_path / "no-mount").exists()


async def test_upload_analyst_agents_content_relay_mode(monkeypatch):
    """Relay mode forwards the content through the OBS upload relay.

    The OBS SDK is never constructed (the child holds no credentials);
    the content is PUT through the relay and the bucket-colon path is
    returned for the submit payload.
    """
    monkeypatch.setattr(analyst_storage, "relay_mode_enabled", lambda: True)
    relay = Mock()
    relay.put_obs_object = AsyncMock(return_value={"obs_path": "/obs/x"})
    monkeypatch.setattr(analyst_storage, "current_relay_client", lambda: relay)
    no_sdk = Mock()
    monkeypatch.setattr(analyst_storage, "ObsClient", no_sdk)

    result = await analyst_storage.upload_analyst_agents_content(
        '{"ok": true}',
        "task.yaml",
        object_key="agent_data/user_data/cust42/runs/d/r/t/tmp/task.yaml",
        bucket_name="phytomni",
    )

    assert relay.put_obs_object.await_count == 1
    sent_path, sent_bytes = relay.put_obs_object.await_args.args
    assert "cust42" in sent_path
    assert sent_bytes == b'{"ok": true}'
    assert result == (
        "phytomni:/agent_data/user_data/cust42/runs/d/r/t/tmp/task.yaml"
    )
    assert not no_sdk.called


async def test_download_obs_out_via_relay_writes_matching_objects(
    tmp_path, monkeypatch
):
    """Relay download lists the prefix and streams only matching objects."""
    relay = Mock()
    relay.get_obs_list = AsyncMock(
        return_value=[
            "agent_data/user_data/cust42/runs/x/output/a.png",
            "agent_data/user_data/cust42/runs/x/output/b.txt",
        ]
    )

    async def _to_path(obs_path, destination, *, message):
        del message
        # Tiny tmp fixture I/O stays inline: thread wake-up is under test.
        Path(destination).write_bytes(  # noqa: ASYNC240
            b"DATA:" + obs_path.encode()
        )

    relay.get_obs_object_to_path = AsyncMock(side_effect=_to_path)
    monkeypatch.setattr(analyst_storage, "current_relay_client", lambda: relay)

    written = await analyst_storage.download_obs_out_via_relay(
        task_dir="cust42",
        obs_output_path="agent_data/user_data/cust42/runs/x/output/",
        download_path=str(tmp_path),
        target_file_feature=(".png",),
        if_download_all=False,
    )

    assert (tmp_path / "cust42" / "a.png").exists()
    assert not (tmp_path / "cust42" / "b.txt").exists()
    assert any("a.png" in line for line in written)
    assert relay.get_obs_object_to_path.await_count == 1


async def test_download_obs_out_via_relay_downloads_all_when_flagged(
    tmp_path, monkeypatch
):
    """if_download_all bypasses the suffix filter."""
    relay = Mock()
    relay.get_obs_list = AsyncMock(
        return_value=["agent_data/user_data/c/runs/x/output/notes.log"]
    )

    async def _to_path(obs_path, destination, *, message):
        del obs_path, message
        # Tiny tmp fixture I/O stays inline: thread wake-up is under test.
        Path(destination).write_bytes(b"x")  # noqa: ASYNC240

    relay.get_obs_object_to_path = AsyncMock(side_effect=_to_path)
    monkeypatch.setattr(analyst_storage, "current_relay_client", lambda: relay)

    written = await analyst_storage.download_obs_out_via_relay(
        task_dir="c",
        obs_output_path="agent_data/user_data/c/runs/x/output/",
        download_path=str(tmp_path),
        target_file_feature=(".png",),
        if_download_all=True,
    )

    assert (tmp_path / "c" / "notes.log").exists()
    assert len(written) == 1


async def test_upload_analyst_agents_content_prefers_obsfs(tmp_path):
    """Verify generated metadata content is written through obsfs.

    Args:
        tmp_path: Temporary obsfs mount root.
    """
    root = _obsfs_root(tmp_path)

    result = await analyst_storage.upload_analyst_agents_content(
        '{"ok": true}',
        "submit.json",
        bucket_name="phytomni",
        obsfs_mount_root=str(tmp_path),
    )

    assert result == "phytomni:/agent_data/tmp_data/submit.json"
    assert (root / "agent_data" / "tmp_data" / "submit.json").read_text(
        encoding="utf-8"
    ) == '{"ok": true}'


async def test_upload_analyst_agents_content_accepts_run_scoped_key(tmp_path):
    """Verify generated metadata can be written under a run-scoped key.

    Args:
        tmp_path: Temporary obsfs mount root.
    """
    root = _obsfs_root(tmp_path)
    object_key = (
        "agent_data/user_data/user-a/runs/20260507/run-1/"
        "analysis_agents_task/tmp/submit.json"
    )

    result = await analyst_storage.upload_analyst_agents_content(
        '{"ok": true}',
        "submit.json",
        object_key=object_key,
        bucket_name="phytomni",
        obsfs_mount_root=str(tmp_path),
    )

    assert result == f"phytomni:/{object_key}"
    assert (root / object_key).read_text(encoding="utf-8") == '{"ok": true}'


def test_upload_analyst_agents_data_prefers_obsfs_copy(tmp_path):
    """Verify local metadata files are copied through obsfs.

    Args:
        tmp_path: Temporary obsfs mount root and source-file directory.
    """
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
    """Verify delete removes files through obsfs first.

    Args:
        tmp_path: Temporary obsfs mount root.
    """
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


def test_obs_download_options_explicit_download_path_wins(tmp_path):
    """Verify explicit download_path kwarg overrides resolver and config.

    Args:
        tmp_path: Temporary path supplying the explicit override value.
    """
    options = analyst_storage.ObsDownloadOptions.from_kwargs(
        {
            "download_path": str(tmp_path / "explicit"),
            "run_identity": RunIdentity.create(
                "user-a",
                "analyst-task",
                IdFactory(token_factory=lambda _: "abc12345"),
            ),
            "obsfs_mount_root": str(tmp_path / "missing"),
        }
    )

    assert options.download_path == str(tmp_path / "explicit")


def test_obs_download_options_uses_resolver_with_run_identity(tmp_path):
    """Verify the resolver activates when only run_identity is provided.

    Args:
        tmp_path: Temporary path used as the local fallback.
    """
    run_identity = RunIdentity.create(
        "user-a",
        "analyst-task",
        IdFactory(token_factory=lambda _: "abc12345"),
    )

    options = analyst_storage.ObsDownloadOptions.from_kwargs(
        {
            "run_identity": run_identity,
            "obsfs_mount_root": str(tmp_path / "missing"),
            "bucket_name": "phytomni",
        }
    )

    assert Path(options.download_path).parts[-2:] == (
        run_identity.run_id,
        "analyst",
    )


def test_obs_download_options_falls_back_to_static_default():
    """Verify the static default is used when no run_identity is supplied."""
    options = analyst_storage.ObsDownloadOptions.from_kwargs({})

    assert (
        options.download_path == analyst_storage.ANALYST_CONFIG.DOWNLOAD_PATH
    )


def test_download_obs_out_prefers_obsfs_and_filters_outputs(tmp_path):
    """Verify result downloads copy matching files from obsfs first.

    Args:
        tmp_path: Temporary obsfs mount root and download directory.
    """
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


async def test_upload_content_falls_back_to_sdk_when_obsfs_missing(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify content uploads fall back to the OBS SDK.

    Args:
        tmp_path: Temporary path with no mounted obsfs bucket.
        monkeypatch: Pytest monkeypatch fixture used to replace ObsClient.

    Returns:
        None after SDK upload assertions pass.
    """
    captured: dict[str, Any] = {}

    class FakeObsClient:
        """Minimal OBS client for putContent fallback.

        Attributes:
            Captured client and upload calls are stored externally.
        """

        def __init__(self, **kwargs: Any):
            captured["client"] = kwargs

        def __getattr__(self, name: str):
            """Map OBS SDK camelCase methods to lint-friendly fakes."""
            if name == "putContent":
                return self.put_content
            raise AttributeError(name)

        def put_content(self, **kwargs: Any):
            """Capture putContent fallback arguments.

            Args:
                **kwargs: OBS SDK putContent keyword arguments.

            Returns:
                Successful OBS response stand-in.
            """
            captured["put_content"] = kwargs
            return SimpleNamespace(status=200, requestId="request-id")

    monkeypatch.setattr(analyst_storage, "ObsClient", FakeObsClient)

    result = await analyst_storage.upload_analyst_agents_content(
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
    """Verify result downloads fall back to OBS SDK listing and getObject.

    Args:
        tmp_path: Temporary path with no mounted obsfs bucket.
        monkeypatch: Pytest monkeypatch fixture used to replace ObsClient.

    Returns:
        None after SDK download assertions pass.
    """

    class FakeObsClient:
        """Minimal OBS client for list/get fallback.

        Attributes:
            No instance attributes are needed for the fake download client.
        """

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
            """Return one listed object for SDK fallback.

            Args:
                **kwargs: OBS SDK listObjects keyword arguments.

            Returns:
                Successful listObjects response stand-in.
            """
            assert kwargs["prefix"] == "results"
            body = SimpleNamespace(
                contents=[SimpleNamespace(key="results/keep.txt")],
                is_truncated=False,
            )
            return SimpleNamespace(status=200, body=body)

        def get_object(self, **kwargs: Any):
            """Write one fake downloaded object.

            Args:
                **kwargs: OBS SDK getObject keyword arguments.

            Returns:
                Successful getObject response stand-in.
            """
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
