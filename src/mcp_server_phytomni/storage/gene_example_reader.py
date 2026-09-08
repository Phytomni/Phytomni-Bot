# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Read only declared curated materials through one bounded storage lane."""

from __future__ import annotations

import os
import stat
import threading
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .gene_examples import (
    MAX_FILE_BYTES,
    MAX_MANIFEST_BYTES,
    CuratedGeneError,
    decode_gene_manifest,
    manifest_key,
    parse_manifest_key,
    parse_material_key,
    validate_material_bytes,
    validate_report_bytes,
)
from .obs_relay_ops import (
    ObsAccessOptions,
    ObsObjectNotFoundError,
    head_object_metadata,
)

_CHUNK_BYTES = 64 * 1024


class CuratedReadControl:
    """Close the active source on cancellation, including late registration."""

    def __init__(self) -> None:
        """Keep cancellation and one active source under the same lock."""
        self._lock = threading.Lock()
        self._stopped = False
        self._close: Callable[[], None] | None = None

    def cancel(self) -> None:
        """Interrupt an open body without releasing its worker lease early."""
        with self._lock:
            self._stopped = True
        self.finish_source()

    def finish_source(self) -> None:
        """Claim the active closer once at EOF, failure, or cancellation."""
        with self._lock:
            close = self._close
            self._close = None
        if close is not None:
            close()

    def bind_source(self, close: Callable[[], None]) -> None:
        """Immediately close a source registered after cancellation."""
        with self._lock:
            stopped = self._stopped
            if not stopped:
                self._close = close
        if stopped:
            close()

    def check(self) -> None:
        """Refuse new reads after the caller has stopped consuming."""
        with self._lock:
            if self._stopped:
                raise CuratedGeneError("curated_read_cancelled", 499)


@dataclass(frozen=True, slots=True)
class CuratedObject:
    """A fully validated body ready for a bounded HTTP response."""

    content: bytes
    media_type: str


def _bounded_body(
    source: BinaryIO, size: int, limit: int, control: CuratedReadControl
) -> bytes:
    """Enforce both declared and observed length before exposing any bytes."""
    if size > limit:
        raise CuratedGeneError("curated_object_too_large", 413)
    if size < 0:
        raise CuratedGeneError("curated_object_unavailable", 502)
    raw = bytearray()
    while True:
        control.check()
        chunk = source.read(min(_CHUNK_BYTES, limit + 1 - len(raw)))
        control.check()
        if not chunk:
            break
        raw.extend(chunk)
        if len(raw) > limit:
            raise CuratedGeneError("curated_object_too_large", 413)
    if len(raw) != size:
        raise CuratedGeneError("curated_object_size_mismatch", 409)
    return bytes(raw)


def _mounted_read(
    root_fd: int, key: str, limit: int, control: CuratedReadControl
) -> bytes:
    """Walk exact catalog components without following directory aliases."""
    with ExitStack() as stack:
        parent = root_fd
        parts = key.split("/")
        for part in parts[:-1]:
            parent = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent,
            )
            stack.callback(os.close, parent)
        fd = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent,
        )
        source = stack.enter_context(os.fdopen(fd, "rb"))
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise CuratedGeneError("curated_object_unavailable", 502)
        control.bind_source(source.close)
        try:
            return _bounded_body(source, metadata.st_size, limit, control)
        finally:
            control.finish_source()


def _sdk_read(
    client: Any, bucket: str, key: str, limit: int, control: CuratedReadControl
) -> bytes:
    """Use the leased operator SDK with bounded HEAD and streaming GET."""
    try:
        return _sdk_body(client, bucket, key, limit, control)
    except (CuratedGeneError, ObsObjectNotFoundError):
        raise
    except Exception:
        # Huawei's ResponseWrapper raises plain Exception for truncated
        # bodies and CRC failures. Translate only this SDK I/O boundary.
        control.check()
        raise CuratedGeneError("curated_object_unavailable", 502) from None


def _sdk_body(
    client: Any, bucket: str, key: str, limit: int, control: CuratedReadControl
) -> bytes:
    """Own the SDK response source until its bounded read or failure ends."""
    size = head_object_metadata(bucket, key, client=client).size_bytes
    if size > limit:
        raise CuratedGeneError("curated_object_too_large", 413)
    control.check()
    response = client.getObject(
        bucketName=bucket,
        objectKey=key,
        loadStreamInMemory=False,
    )
    source = getattr(getattr(response, "body", None), "response", None)
    if source is not None:
        control.bind_source(source.close)

    try:
        if response.status == 404:
            raise CuratedGeneError("curated_object_not_found", 404)
        if response.status != 200 or source is None:
            raise CuratedGeneError("curated_object_unavailable", 502)
        return _bounded_body(source, size, limit, control)
    finally:
        control.finish_source()


def _validated_read(
    key: str, read: Callable[[str, int], bytes], max_bytes: int
) -> CuratedObject:
    """Bind a manifest and any requested material to the current report."""
    gene = parse_manifest_key(key)
    material = parse_material_key(key)
    if gene is None and material is None:
        raise CuratedGeneError("curated_object_not_found", 404)
    if gene is None:
        assert material is not None
        gene = material[0]
    raw = read(manifest_key(gene), min(max_bytes, MAX_MANIFEST_BYTES))
    manifest = decode_gene_manifest(raw)
    if manifest.gene_id != gene:
        raise CuratedGeneError("curated_manifest_binding_mismatch", 409)
    report = read(
        f"gene-examples/md/{gene}_result.md", min(max_bytes, MAX_FILE_BYTES)
    )
    validate_report_bytes(report, manifest.report_sha256)
    if material is None:
        return CuratedObject(raw, "application/json")
    resource = next(
        (entry for entry in manifest.resources if entry.object_key == key),
        None,
    )
    if resource is None:
        raise CuratedGeneError("curated_object_not_found", 404)
    content = read(key, min(max_bytes, MAX_FILE_BYTES))
    validate_material_bytes(content, resource)
    return CuratedObject(content, resource.media_type)


def read_curated_object(
    bucket: str,
    key: str,
    *,
    access: ObsAccessOptions,
    control: CuratedReadControl,
    max_bytes: int = MAX_FILE_BYTES,
) -> CuratedObject:
    """Read a curated manifest or registered material, never private outputs.

    A mounted bucket is authoritative for the whole read. Missing files never
    switch to SDK storage. Otherwise all reads share the supplied leased SDK.
    Errors contain stable codes only, without storage paths or credentials.
    """
    control.check()
    if parse_manifest_key(key) is None and parse_material_key(key) is None:
        raise CuratedGeneError("curated_object_not_found", 404)
    try:
        root = Path(access.mount_root) / bucket
        with ExitStack() as stack:
            if root.exists() or root.is_symlink():
                root_fd = os.open(
                    root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                )
                stack.callback(os.close, root_fd)

                def read(path: str, limit: int) -> bytes:
                    """Keep every bundle object on the selected mount."""
                    control.check()
                    return _mounted_read(root_fd, path, limit, control)

            else:

                def read(path: str, limit: int) -> bytes:
                    """Keep every bundle object on the selected SDK."""
                    control.check()
                    return _sdk_read(
                        access.client, bucket, path, limit, control
                    )

            return _validated_read(key, read, max_bytes)
    except CuratedGeneError:
        raise
    except (FileNotFoundError, ObsObjectNotFoundError):
        raise CuratedGeneError("curated_object_not_found", 404) from None
    except (OSError, AttributeError, TypeError, ValueError):
        control.check()
        raise CuratedGeneError("curated_object_unavailable", 502) from None
