# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``storage/obs_relay_ops.py`` server-side OBS object ops.

Covers the SDK-path primitives the relay's SDK-termination handlers call
(put_object_bytes / put_dir_marker / get_object_bytes / list_object_keys)
plus the bucket-confinement re-validation a relay runs on an untrusted
client path. obsfs is forced unavailable (mount root is nonexistent) so
every case exercises the SDK fallback through the shared capturing fake
``ObsClient`` (``fake_obs_client_factory``).
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.storage import obs_relay_ops as ops
from mcp_server_phytomni.storage.obs_storage import ObsPathError

pytestmark = pytest.mark.unit


def _seed_fake(
    _monkeypatch: pytest.MonkeyPatch, factory: Callable[..., Any]
) -> Any:
    """Return a fresh fake to be explicitly lent to each OBS operation."""
    del _monkeypatch
    return factory()


def _missing_mount(tmp_path: Any) -> str:
    """Return an obsfs mount root that does not exist (forces SDK path)."""
    return str(tmp_path / "no-mount")


def _access(client: Any, mount_root: str) -> ops.ObsAccessOptions:
    """Build one explicit owned-client access context for an OBS op."""
    return ops.ObsAccessOptions(client=client, mount_root=mount_root)


def _capturing_obs_client(
    captured: dict[str, Any],
    method_name: str,
    status: int = 200,
) -> Callable[..., Any]:
    """Return a constructor-shaped SDK fake for one OBS write method."""
    capture_key = {"putContent": "put_content", "putFile": "put_file"}[
        method_name
    ]

    def sdk_method(**kwargs: Any) -> Any:
        captured[capture_key] = kwargs
        return SimpleNamespace(status=status)

    def factory(**kwargs: Any) -> Any:
        captured["init"] = kwargs
        return SimpleNamespace(**{method_name: sdk_method})

    return factory


def test_put_object_bytes_sdk_fallback_writes_exact_key(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_obs_client_factory: Callable[..., Any],
) -> None:
    """SDK putContent is called with the exact bucket/key and content."""
    fake = _seed_fake(monkeypatch, fake_obs_client_factory)

    returned = ops.put_object_bytes(
        "phytomni",
        "agent_data/uploads/alice/report.pdf",
        b"hello-bytes",
        access=_access(fake(), _missing_mount(tmp_path)),
    )

    put = fake.captured["put_content"]
    assert put["bucketName"] == "phytomni"
    assert put["objectKey"] == "agent_data/uploads/alice/report.pdf"
    assert put["content"] == b"hello-bytes"
    assert returned == "agent_data/uploads/alice/report.pdf"


def test_put_object_file_sdk_fallback_streams_local_file(
    tmp_path: Any,
) -> None:
    """SDK putFile receives the source path instead of buffered file bytes."""
    source = tmp_path / "archive.zip"
    source.write_bytes(b"zip-bytes")
    captured: dict[str, Any] = {}
    client = _capturing_obs_client(captured, "putFile")()

    returned = ops.put_object_file(
        "phytomni",
        "agent_data/runs/archive.zip",
        source,
        access=_access(client, _missing_mount(tmp_path)),
    )

    assert returned == "agent_data/runs/archive.zip"
    assert captured["put_file"] == {
        "bucketName": "phytomni",
        "objectKey": "agent_data/runs/archive.zip",
        "file_path": str(source),
    }


def test_put_object_file_obsfs_copies_local_file(tmp_path: Any) -> None:
    """Mounted OBS copies a local archive byte-for-byte."""
    source = tmp_path / "source.zip"
    source.write_bytes(b"zip-bytes")
    mount_root = tmp_path / "mount"
    (mount_root / "phytomni").mkdir(parents=True)

    ops.put_object_file(
        "phytomni",
        "agent_data/runs/archive.zip",
        source,
        access=ops.ObsAccessOptions(mount_root=str(mount_root)),
    )

    assert (
        mount_root / "phytomni/agent_data/runs/archive.zip"
    ).read_bytes() == b"zip-bytes"


def test_put_object_bytes_if_absent_sdk_uses_conditional_create(
    tmp_path: Any,
) -> None:
    """SDK creation sends If-None-Match instead of allowing an overwrite."""
    captured: dict[str, Any] = {}
    client = _capturing_obs_client(captured, "putContent")()

    ops.put_object_bytes_if_absent(
        "phytomni",
        "agent_data/runs/private.json",
        b"{}",
        access=_access(client, _missing_mount(tmp_path)),
    )

    assert captured["put_content"]["extensionHeaders"] == {
        "If-None-Match": "*"
    }


def test_put_object_bytes_if_absent_obsfs_rejects_existing_object(
    tmp_path: Any,
) -> None:
    """Mounted OBS uses exclusive create and leaves existing content intact."""
    mount_root = tmp_path / "mount"
    existing = mount_root / "phytomni/agent_data/runs/private.json"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"original")

    with pytest.raises(ops.ObsObjectAlreadyExistsError):
        ops.put_object_bytes_if_absent(
            "phytomni",
            "agent_data/runs/private.json",
            b"replacement",
            access=ops.ObsAccessOptions(mount_root=str(mount_root)),
        )

    assert existing.read_bytes() == b"original"


def test_put_object_bytes_if_absent_sdk_maps_precondition_failure(
    tmp_path: Any,
) -> None:
    """A conditional SDK collision is distinguishable from a failed write."""
    client = _capturing_obs_client({}, "putContent", status=412)()

    with pytest.raises(ops.ObsObjectAlreadyExistsError):
        ops.put_object_bytes_if_absent(
            "phytomni",
            "agent_data/runs/private.json",
            b"{}",
            access=_access(client, _missing_mount(tmp_path)),
        )


def test_put_dir_marker_sdk_writes_zero_byte_object(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_obs_client_factory: Callable[..., Any],
) -> None:
    """The dir marker is a zero-byte (content=None) putContent."""
    fake = _seed_fake(monkeypatch, fake_obs_client_factory)

    ops.put_dir_marker(
        "phytomni",
        "agent_data/runs/alice/run_x/output/",
        access=_access(fake(), _missing_mount(tmp_path)),
    )

    put = fake.captured["put_content"]
    assert put["objectKey"].endswith("/output/")
    assert put["content"] is None


def test_get_object_bytes_sdk_returns_streamed_content(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_obs_client_factory: Callable[..., Any],
) -> None:
    """SDK get_object_bytes streams the object and joins it back to bytes."""
    fake = _seed_fake(monkeypatch, fake_obs_client_factory)
    fake.objects["agent_data/out/result.cif"] = b"ATOM  1  N"

    data = ops.get_object_bytes(
        "phytomni",
        "agent_data/out/result.cif",
        access=_access(fake(), _missing_mount(tmp_path)),
    )

    assert data == b"ATOM  1  N"
    # Streams out of memory (no temp-file downloadPath).
    assert fake.captured["get_object"].get("downloadPath") is None
    assert fake.captured["get_object"]["loadStreamInMemory"] is False


def test_iter_object_chunks_yields_all_bytes_in_pieces(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_obs_client_factory: Callable[..., Any],
) -> None:
    """iter_object_chunks streams the object in <= chunk_size pieces."""
    fake = _seed_fake(monkeypatch, fake_obs_client_factory)
    fake.objects["agent_data/out/big.bin"] = b"abcdefghij"

    chunks = list(
        ops.iter_object_chunks(
            "phytomni",
            "agent_data/out/big.bin",
            access=_access(fake(), _missing_mount(tmp_path)),
            stream=ops.ObsStreamOptions(chunk_size=4),
        )
    )

    assert b"".join(chunks) == b"abcdefghij"
    assert max(len(chunk) for chunk in chunks) <= 4
    assert len(chunks) >= 3


def test_object_size_sdk_returns_content_length(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_obs_client_factory: Callable[..., Any],
) -> None:
    """object_size reads the content length via getObjectMetadata."""
    fake = _seed_fake(monkeypatch, fake_obs_client_factory)
    fake.objects["agent_data/out/big.bin"] = b"abcdefghij"

    size = ops.object_size(
        "phytomni",
        "agent_data/out/big.bin",
        access=_access(fake(), _missing_mount(tmp_path)),
    )

    assert size == 10
    assert fake.captured["get_object_metadata"]["objectKey"] == (
        "agent_data/out/big.bin"
    )


def test_list_object_keys_sdk_paginates_and_skips_dirs(
    monkeypatch: pytest.MonkeyPatch,
    fake_obs_client_factory: Callable[..., Any],
) -> None:
    """listObjects follows next_marker and drops directory-marker keys."""
    fake = _seed_fake(monkeypatch, fake_obs_client_factory)
    fake.pages = [
        SimpleNamespace(
            contents=[
                SimpleNamespace(key="prefix/a.png"),
                SimpleNamespace(key="prefix/sub/"),
            ],
            is_truncated=True,
            next_marker="prefix/sub/",
        ),
        SimpleNamespace(
            contents=[SimpleNamespace(key="prefix/sub/b.md")],
            is_truncated=False,
            next_marker=None,
        ),
    ]

    keys = ops.list_object_keys(
        "phytomni", "prefix/", access=_access(fake(), "/no-mount")
    )

    assert keys == ["prefix/a.png", "prefix/sub/b.md"]
    assert len(fake.captured["list_calls"]) == 2
    assert fake.captured["list_calls"][1]["marker"] == "prefix/sub/"


def test_put_object_bytes_rejects_out_of_bucket_path(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_obs_client_factory: Callable[..., Any],
) -> None:
    """A key escaping the bucket via ``..`` is rejected before any SDK call."""
    fake = _seed_fake(monkeypatch, fake_obs_client_factory)

    with pytest.raises(ObsPathError):
        ops.put_object_bytes(
            "phytomni",
            "agent_data/../../etc/passwd",
            b"x",
            access=_access(fake(), _missing_mount(tmp_path)),
        )

    assert "put_content" not in fake.captured
