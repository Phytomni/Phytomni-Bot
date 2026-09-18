# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Bounded curated reads bind every material to its declared report."""

from __future__ import annotations

import hashlib
import io
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from obs.model import ResponseWrapper

from mcp_server_phytomni.storage.gene_example_reader import (
    CuratedReadControl,
    read_curated_object,
)
from mcp_server_phytomni.storage.gene_examples import CuratedGeneError
from mcp_server_phytomni.storage.obs_relay_ops import ObsAccessOptions

_GENE = "AT1G01010"
_REPORT_KEY = f"gene-examples/md/{_GENE}_result.md"
_MANIFEST_KEY = f"gene-examples/manifests/{_GENE}_result.json"
_REPORT = b"# Gene report\nOne reference [1].\n"
_MATERIAL = b"data_structure\n_entry.id structure\n"
_REPORT_SHA = hashlib.sha256(_REPORT).hexdigest()
_MATERIAL_KEY = f"gene-examples/materials/{_GENE}/{_REPORT_SHA}/structure.cif"


def _objects() -> dict[str, bytes]:
    """Create an explicit, internally consistent approved bundle."""
    manifest = {
        "schema_version": 1,
        "gene_id": _GENE,
        "report_file": f"{_GENE}_result.md",
        "report_sha256": _REPORT_SHA,
        "reference_count": 1,
        "resources": [
            {
                "id": "structure",
                "name": "structure.cif",
                "kind": "cif",
                "markdown_href": "./structure.cif",
                "object_key": _MATERIAL_KEY,
                "media_type": "chemical/x-cif",
                "size_bytes": len(_MATERIAL),
                "sha256": hashlib.sha256(_MATERIAL).hexdigest(),
            }
        ],
        "reference_materials": [
            {
                "reference_index": 1,
                "excerpt": "Original source.",
                "resource_ids": ["structure"],
            }
        ],
    }
    return {
        _REPORT_KEY: _REPORT,
        _MATERIAL_KEY: _MATERIAL,
        _MANIFEST_KEY: json.dumps(manifest).encode(),
    }


class _Sdk:
    """Record bounded SDK access without credentials or external storage."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.reads: list[str] = []
        self.streams: list[io.BytesIO] = []
        self.advertised_size: int | None = None

    def metadata(self, **kwargs: Any) -> SimpleNamespace:
        """Return a controllable HEAD response."""
        raw = self.objects.get(kwargs["objectKey"])
        return SimpleNamespace(
            status=404 if raw is None else 200,
            body=SimpleNamespace(
                contentLength=(
                    self.advertised_size
                    if self.advertised_size is not None
                    else len(raw or b"")
                ),
            ),
        )

    def download(self, **kwargs: Any) -> SimpleNamespace:
        """Expose a close-observable source and record exact keys."""
        key = kwargs["objectKey"]
        self.reads.append(key)
        stream = io.BytesIO(self.objects.get(key, b""))
        self.streams.append(stream)
        return SimpleNamespace(
            status=200 if key in self.objects else 404,
            body=SimpleNamespace(response=stream),
        )


setattr(_Sdk, "getObjectMetadata", _Sdk.metadata)
setattr(_Sdk, "getObject", _Sdk.download)


def _read(sdk: _Sdk, key: str, tmp_path: Path, **kwargs: Any) -> Any:
    """Read through a deliberately absent mount into the SDK lane."""
    return read_curated_object(
        "phytomni",
        key,
        access=ObsAccessOptions(
            client=sdk, mount_root=str(tmp_path / "absent")
        ),
        control=CuratedReadControl(),
        **kwargs,
    )


@pytest.mark.parametrize(
    "key,expected_type",
    [
        (_MANIFEST_KEY, "application/json"),
        (_MATERIAL_KEY, "chemical/x-cif"),
    ],
)
def test_reads_validate_manifest_report_and_material(
    tmp_path: Path,
    key: str,
    expected_type: str,
) -> None:
    """No object body escapes before all relevant bindings pass."""
    sdk = _Sdk(_objects())
    result = _read(sdk, key, tmp_path)
    assert result.content == sdk.objects[key]
    assert result.media_type == expected_type
    assert _REPORT_KEY in sdk.reads
    assert all(stream.closed for stream in sdk.streams)


@pytest.mark.parametrize(
    "change,status",
    [
        ("report_changed", 409),
        ("material_changed", 409),
        ("manifest_missing", 404),
        ("resource_missing", 404),
        ("undeclared", 404),
        ("stale_key", 404),
        ("manifest_other_gene", 409),
    ],
)
def test_rejects_missing_stale_or_cross_report_material(
    tmp_path: Path,
    change: str,
    status: int,
) -> None:
    """A syntactically valid path is never sufficient authorization."""
    objects = _objects()
    key = _MATERIAL_KEY
    if change == "report_changed":
        objects[_REPORT_KEY] += b"Changed."
    elif change == "material_changed":
        objects[_MATERIAL_KEY] += b"Changed."
    elif change == "manifest_missing":
        del objects[_MANIFEST_KEY]
    elif change == "resource_missing":
        del objects[_MATERIAL_KEY]
    elif change == "undeclared":
        key = key.replace("structure.cif", "other.cif")
        objects[key] = _MATERIAL
    elif change == "stale_key":
        key = key.replace(_REPORT_SHA, "a" * 64)
        objects[key] = _MATERIAL
    else:
        objects[_MANIFEST_KEY] = objects[_MANIFEST_KEY].replace(
            _GENE.encode(), b"AT1G01020"
        )
    sdk = _Sdk(objects)
    with pytest.raises(CuratedGeneError) as caught:
        _read(sdk, key, tmp_path)
    assert caught.value.status == status
    assert all(stream.closed for stream in sdk.streams)
    if change in {"undeclared", "stale_key"}:
        assert key not in sdk.reads


def test_head_and_actual_body_limits_are_both_enforced(tmp_path: Path) -> None:
    """A dishonest HEAD cannot turn a bounded read into an unbounded body."""
    sdk = _Sdk(_objects())
    sdk.advertised_size = 1
    with pytest.raises(CuratedGeneError) as caught:
        _read(sdk, _MANIFEST_KEY, tmp_path, max_bytes=20)
    assert caught.value.status == 413
    assert all(stream.closed for stream in sdk.streams)
    sdk = _Sdk(_objects())
    sdk.advertised_size = 1000
    with pytest.raises(CuratedGeneError) as caught:
        _read(sdk, _MANIFEST_KEY, tmp_path, max_bytes=20)
    assert caught.value.status == 413
    assert not sdk.reads


def test_mounted_missing_material_never_falls_through_to_sdk(
    tmp_path: Path,
) -> None:
    """Choose storage once for the complete curated bundle."""
    objects = _objects()
    root = tmp_path / "mount" / "phytomni"
    for key in (_MANIFEST_KEY, _REPORT_KEY):
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(objects[key])
    sdk = _Sdk(objects)
    with pytest.raises(CuratedGeneError) as caught:
        read_curated_object(
            "phytomni",
            _MATERIAL_KEY,
            access=ObsAccessOptions(client=sdk, mount_root=str(root.parent)),
            control=CuratedReadControl(),
        )
    assert caught.value.status == 404
    assert not sdk.reads


def test_mounted_symlink_cannot_escape_bucket(tmp_path: Path) -> None:
    """Reject alias components even when the target contains valid bytes."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / f"{_GENE}_result.json").write_bytes(_objects()[_MANIFEST_KEY])
    root = tmp_path / "mount" / "phytomni" / "gene-examples"
    root.mkdir(parents=True)
    (root / "manifests").symlink_to(outside, target_is_directory=True)
    with pytest.raises(CuratedGeneError):
        read_curated_object(
            "phytomni",
            _MANIFEST_KEY,
            access=ObsAccessOptions(
                client=_Sdk(_objects()), mount_root=str(tmp_path / "mount")
            ),
            control=CuratedReadControl(),
        )


def test_cancel_closes_active_source_and_refuses_further_reads(
    tmp_path: Path,
) -> None:
    """Cancellation remains effective before and after source registration."""
    control = CuratedReadControl()
    closed = threading.Event()
    control.bind_source(closed.set)
    control.cancel()
    assert closed.is_set()
    late = threading.Event()
    control.bind_source(late.set)
    assert late.is_set()
    with pytest.raises(CuratedGeneError) as caught:
        read_curated_object(
            "phytomni",
            _MANIFEST_KEY,
            access=ObsAccessOptions(
                client=_Sdk(_objects()), mount_root=str(tmp_path / "absent")
            ),
            control=control,
        )
    assert caught.value.code == "curated_read_cancelled"


def test_control_releases_each_source_once_without_cancelling_next_read() -> (
    None
):
    """Normal EOF and cancellation share one owner for each source closer."""
    control = CuratedReadControl()
    closed: list[str] = []
    control.bind_source(lambda: closed.append("manifest"))
    control.finish_source()
    control.finish_source()
    control.check()
    control.bind_source(lambda: closed.append("report"))
    control.cancel()
    control.finish_source()
    control.bind_source(lambda: closed.append("late"))
    control.finish_source()
    assert closed == ["manifest", "report", "late"]


@pytest.mark.parametrize("size,crc", [(4, None), (1, "1")])
def test_actual_huawei_response_errors_are_safe_upstream_failures(
    tmp_path: Path,
    size: int,
    crc: str | None,
) -> None:
    """The installed SDK raises plain Exception on truncation and bad CRC."""
    raw = io.BytesIO(b"x")
    result = SimpleNamespace(
        read=raw.read, getheader=lambda *_args: "close", status=200
    )
    wrapped = ResponseWrapper(
        SimpleNamespace(close=raw.close),
        result,
        None,
        contentLength=size,
        obs_crc64=crc,
    )
    sdk = SimpleNamespace(
        getObjectMetadata=lambda **_kwargs: SimpleNamespace(
            status=200, body=SimpleNamespace(contentLength=size)
        ),
        getObject=lambda **_kwargs: SimpleNamespace(
            status=200, body=SimpleNamespace(response=wrapped)
        ),
    )
    with pytest.raises(CuratedGeneError) as caught:
        read_curated_object(
            "phytomni",
            _MANIFEST_KEY,
            access=ObsAccessOptions(
                client=sdk, mount_root=str(tmp_path / "absent")
            ),
            control=CuratedReadControl(),
        )
    assert caught.value.status == 502
    assert caught.value.code == "curated_object_unavailable"
    assert raw.closed
