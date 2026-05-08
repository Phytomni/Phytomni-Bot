# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for obsfs-first OBS storage helpers."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.storage.obs_storage import (
    ObsPathError,
    bucket_colon_path,
    normalize_obs_object_key,
    obs_path_from_key,
    obsfs_bucket_available,
    obsfs_bucket_root,
    obsfs_or_sdk,
    obsfs_path_exists,
    obsfs_path_for,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("obs_path", "expected"),
    [
        ("/obs/phytomni/agent_data/file.txt", "agent_data/file.txt"),
        ("obs://phytomni/agent_data/file.txt", "agent_data/file.txt"),
        ("obs://agent_data/file.txt", "agent_data/file.txt"),
        ("phytomni:/agent_data/file.txt", "agent_data/file.txt"),
        ("/phytomni/agent_data/file.txt", "agent_data/file.txt"),
        ("/agent_data/file.txt", "agent_data/file.txt"),
        ("agent_data/file.txt", "agent_data/file.txt"),
        ("agent_data/folder/", "agent_data/folder/"),
    ],
)
def test_normalize_obs_object_key_accepts_public_path_forms(
    obs_path: str,
    expected: str,
):
    """Verify accepted OBS path forms normalize to object keys."""
    assert normalize_obs_object_key(obs_path, "phytomni") == expected


@pytest.mark.parametrize(
    "obs_path",
    [
        "/obs/other/agent_data/file.txt",
        "../secret.txt",
        "agent_data/../../secret.txt",
        "agent_data\\..\\secret.txt",
    ],
)
def test_normalize_obs_object_key_rejects_unsafe_paths(obs_path: str):
    """Verify unsafe OBS paths cannot escape the bucket root."""
    with pytest.raises(ObsPathError):
        normalize_obs_object_key(obs_path, "phytomni")


def test_obsfs_bucket_root_resolves_bucket_under_mount(tmp_path):
    """Verify obsfs bucket roots are resolved under the mount root."""
    assert obsfs_bucket_root("phytomni", tmp_path) == tmp_path / "phytomni"


def test_obsfs_path_for_joins_safe_object_key(tmp_path):
    """Verify OBS paths map to local obsfs paths."""
    assert (
        obsfs_path_for(
            "obs://phytomni/agent_data/file.txt",
            "phytomni",
            tmp_path,
        )
        == tmp_path / "phytomni" / "agent_data" / "file.txt"
    )


def test_obsfs_bucket_available_tracks_mount_root(tmp_path):
    """Verify obsfs availability checks the bucket directory."""
    assert not obsfs_bucket_available("phytomni", tmp_path)

    (tmp_path / "phytomni").mkdir()

    assert obsfs_bucket_available("phytomni", tmp_path)


def test_obsfs_path_exists_returns_false_for_missing_root(tmp_path):
    """Verify path existence handles missing obsfs roots."""
    assert not obsfs_path_exists("agent_data/file.txt", "phytomni", tmp_path)


def test_public_path_formatters_preserve_legacy_shapes():
    """Verify helper formatters preserve public OBS path shapes."""
    assert (
        obs_path_from_key("phytomni", "agent_data/file.txt")
        == "/obs/phytomni/agent_data/file.txt"
    )
    assert (
        bucket_colon_path("phytomni", "agent_data/file.txt")
        == "phytomni:/agent_data/file.txt"
    )


def test_obsfs_or_sdk_returns_obsfs_result_without_fallback():
    """Verify successful obsfs actions do not call the fallback."""
    called = {"sdk": False}

    def sdk_action():
        called["sdk"] = True
        return "sdk"

    assert obsfs_or_sdk(lambda: "obsfs", sdk_action) == "obsfs"
    assert called == {"sdk": False}


def test_obsfs_or_sdk_falls_back_on_io_errors():
    """Verify I/O failures use the SDK fallback action."""

    def obsfs_action():
        raise PermissionError("mount denied")

    assert obsfs_or_sdk(obsfs_action, lambda: "sdk") == "sdk"
