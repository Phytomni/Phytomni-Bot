# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""OBS object relay routes (server-side SDK termination).

Functions: add_obs_routes. The customer relay child holds no operator OBS
AK/SK, so these routes run the operator's own ``ObsClient`` (via
``storage/obs_relay_ops``) on its behalf: upload / download / dir-marker
by re-validated key plus a list confined to the server-owned output root.
Each audits metadata only — never the binary body.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import re
import sqlite3
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.requests import Request

from ...config.defaults import ApiConfig, ServerConfig
from ...runtime.outbound import ObsProfileName, current_outbound_runtime
from ...runtime.request_context import current_request_id
from ...storage import obs_relay_ops
from ...storage.gene_example_reader import (
    CuratedReadControl,
    read_curated_object,
)
from ...storage.gene_examples import (
    CuratedGeneError,
    parse_manifest_key,
    parse_material_key,
)
from ...storage.obs_relay_ops import ObsAccessOptions
from ...storage.obs_storage import (
    ObsPathError,
    normalize_obs_object_key,
    obs_path_from_key,
)
from ...storage.path_policy import AGENT_DATA_ROOT, USER_DATA_ROOT
from ..auth import ApiPrincipal
from .audit import RelayAuditRecord, get_audit_store
from .deps import read_relay_body, require_relay_access

__all__ = ["add_obs_routes"]

_OBS_SERVICE = "obs"
_LOGGER = logging.getLogger(__name__)
_OBS_OPERATION_ERRORS = (
    AttributeError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


@dataclass(frozen=True, slots=True)
class _ObsStreamLifecycle:
    """Cross-thread source and response lifetime signals."""

    stop: threading.Event
    source_close: list[Callable[[], None] | None]
    source_terminal: threading.Event
    response_done: threading.Event


@dataclass(frozen=True, slots=True)
class _ObsChunkRequest:
    """Inputs for one blocking OBS response-stream worker."""

    client: Any
    events: queue.Queue[object]
    bucket: str
    object_key: str
    lifecycle: _ObsStreamLifecycle


# A content-addressed shared path must carry a FULL sha256 fingerprint
# segment (64 lowercase hex) plus something after it. Anchoring on the
# fingerprint (not the bare ``shared/`` prefix) keeps the possession-of-
# fingerprint model intact: a caller cannot list the bare shared root to
# enumerate other tenants' fingerprints, only read a path whose
# (unguessable) fingerprint it already possesses.
_SHARED_FP_RE = re.compile(
    rf"^{re.escape(AGENT_DATA_ROOT)}/shared/[0-9a-f]{{64}}/"
)
_GENE_EXAMPLE_LIST_PREFIX = "gene-examples/md/"
_GENE_ID_PATTERN = r"(?:AT|GLYMA|Os|Traes|Zm)[A-Za-z0-9.-]*"
_GENE_EXAMPLE_MD_RE = re.compile(
    rf"^gene-examples/md/(?P<gene>{_GENE_ID_PATTERN})_result[.]md$"
)
_GENE_EXAMPLE_IMAGE_RE = re.compile(
    rf"^gene-examples/img/(?P<gene>{_GENE_ID_PATTERN})/"
    rf"(?P=gene)_[A-Za-z0-9._-]+[.]png$"
)


def _require_query(request: Request, key: str) -> str:
    """Return a required non-empty query parameter or raise 400."""
    value = request.query_params.get(key, "")
    if not value:
        raise HTTPException(status_code=400, detail=f"missing {key}")
    return value


def _begin() -> tuple[float, ApiConfig, ServerConfig]:
    """Return the shared (start time, api config, server config) preamble."""
    return time.monotonic(), ApiConfig(), ServerConfig()


async def _run_obs_op(
    op: Callable[..., Any], *args: Any, **kwargs: Any
) -> Any:
    """Run one operator OBS operation under the finite OBS runtime."""
    outbound = current_outbound_runtime()
    if outbound.obs is None:
        raise RuntimeError("operator OBS runtime is unavailable")
    try:
        return await outbound.obs.run(
            ObsProfileName.PRIMARY,
            lambda client: op(
                *args,
                access=ObsAccessOptions(client=client),
                **kwargs,
            ),
        )
    except ObsPathError as exc:
        raise HTTPException(
            status_code=400, detail="obs path outside bucket"
        ) from exc


def _close_stream_iterator(iterator: Iterator[bytes]) -> None:
    """Close a blocking OBS iterator when it exposes ``close``."""
    close = getattr(iterator, "close", None)
    if callable(close):
        close()


def _record_obs_audit(
    principal: ApiPrincipal,
    operation: str,
    started: float,
    meta: dict[str, Any],
    *,
    db_path: str,
) -> None:
    """Persist an OBS audit row with metadata only (never the binary body).

    ``meta`` carries only path / prefix / byte-count / object-count
    descriptors; the relayed object bytes are never written to the store.
    """
    entry = RelayAuditRecord(
        request_id=current_request_id() or "",
        user_id=principal.user_id,
        key_prefix=principal.key_prefix,
        service=_OBS_SERVICE,
        operation=operation,
        status_code=200,
        duration_ms=int((time.monotonic() - started) * 1000),
        request_body=json.dumps(meta),
    )
    try:
        get_audit_store(db_path).record(entry)
    except (sqlite3.Error, OSError):
        _LOGGER.exception("relay obs audit write failed")


def _normalized_obs_key(bucket: str, value: str, *, kind: str) -> str:
    """Normalize an OBS path and map bucket escapes to a client error."""
    try:
        return normalize_obs_object_key(value, bucket)
    except ObsPathError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"obs {kind} outside bucket",
        ) from exc


def _is_gene_example_object(key: str) -> bool:
    """Return whether a key matches the curated gene object grammar."""
    return bool(
        _GENE_EXAMPLE_MD_RE.fullmatch(key)
        or _GENE_EXAMPLE_IMAGE_RE.fullmatch(key)
        or parse_manifest_key(key)
        or parse_material_key(key)
    )


def _require_read_object(
    bucket: str, path: str, principal: ApiPrincipal
) -> str:
    """Return a normalized object key allowed for authenticated reads."""
    normalized = _normalized_obs_key(bucket, path, kind="path")
    tenant_prefixes = (
        f"{USER_DATA_ROOT}/{principal.user_id}/",
        f"{AGENT_DATA_ROOT}/uploads/{principal.user_id}/",
    )
    if (
        normalized.startswith(tenant_prefixes)
        or _SHARED_FP_RE.match(normalized)
        or _is_gene_example_object(normalized)
    ):
        return normalized
    raise HTTPException(
        status_code=403,
        detail="obs path outside tenant namespace",
    )


def _require_list_prefix(
    bucket: str, prefix: str, principal: ApiPrincipal
) -> str:
    """Return a normalized prefix allowed for authenticated listing."""
    normalized = _normalized_obs_key(bucket, prefix, kind="prefix")
    own_output = f"{USER_DATA_ROOT}/{principal.user_id}/"
    if (
        normalized.startswith(own_output)
        or _SHARED_FP_RE.match(normalized)
        or normalized == _GENE_EXAMPLE_LIST_PREFIX
    ):
        return normalized
    raise HTTPException(
        status_code=403,
        detail="list prefix outside readable namespace",
    )


def _require_tenant_prefix(
    bucket: str, path: str, principal: ApiPrincipal
) -> str:
    """Return a normalized mutation key confined to the caller's namespace.

    Object write / dir relay is bound to the caller key's own tenant namespace
    under the two real roots (``user_data`` outputs and ``uploads``). Even
    inside the shared operator bucket a key cannot reach another tenant's
    objects; anything else is a 403.

    A content-addressed shared path (``AGENT_DATA_ROOT/shared/<64-hex>/``)
    is also permitted on a possession-of-fingerprint basis: the path must
    carry a full sha256 fingerprint segment — the bare shared root is
    rejected so a caller cannot enumerate it — and the fingerprint is
    unguessable, so a caller that knows it already proved possession of the
    inputs that produced it.
    """
    try:
        normalized = normalize_obs_object_key(path, bucket)
    except ObsPathError as exc:
        raise HTTPException(
            status_code=400, detail="obs path outside bucket"
        ) from exc
    allowed = (
        f"{USER_DATA_ROOT}/{principal.user_id}/",
        f"{AGENT_DATA_ROOT}/uploads/{principal.user_id}/",
    )
    if not (normalized.startswith(allowed) or _SHARED_FP_RE.match(normalized)):
        raise HTTPException(
            status_code=403, detail="obs path outside tenant namespace"
        )
    return normalized


async def _put_object(
    request: Request,
    principal: ApiPrincipal = Depends(require_relay_access(_OBS_SERVICE)),
) -> Response:
    """Write the request body at the client-supplied (validated) key."""
    started, config, server = _begin()
    body = await read_relay_body(request, config.RELAY_REQUEST_MAX_BYTES)
    path = _require_query(request, "path")
    safe_key = _require_tenant_prefix(server.BUCKET_NAME, path, principal)
    key = await _run_obs_op(
        obs_relay_ops.put_object_bytes,
        server.BUCKET_NAME,
        safe_key,
        body,
    )
    _record_obs_audit(
        principal,
        "obs_upload",
        started,
        {"path": path, "key": key, "bytes": len(body)},
        db_path=config.RELAY_AUDIT_DB_PATH,
    )
    return JSONResponse(
        {"obs_path": obs_path_from_key(server.BUCKET_NAME, key)}
    )


async def _get_curated_object(
    request: Request, bucket: str, key: str, max_bytes: int
) -> Response:
    """Validate the entire bounded object before sending successful headers."""
    control = CuratedReadControl()
    producer = asyncio.create_task(
        _run_obs_op(
            read_curated_object,
            bucket,
            key,
            control=control,
            max_bytes=max_bytes,
        )
    )
    try:
        while not producer.done():
            await asyncio.wait({producer}, timeout=0.05)
            if not producer.done() and await request.is_disconnected():
                raise HTTPException(
                    status_code=499, detail="curated_read_cancelled"
                )
        result = producer.result()
        return Response(
            result.content,
            media_type=result.media_type,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except CuratedGeneError as error:
        raise HTTPException(
            status_code=error.status, detail=error.code
        ) from None
    finally:
        control.cancel()
        if not producer.done():
            producer.cancel()
            await _wait_for_producer(producer)


async def _get_object(
    request: Request,
    principal: ApiPrincipal = Depends(require_relay_access(_OBS_SERVICE)),
) -> Response:
    """Stream the client-supplied (validated) object key under a budget."""
    started, config, server = _begin()
    path = _require_query(request, "path")
    safe_key = _require_read_object(server.BUCKET_NAME, path, principal)
    if parse_manifest_key(safe_key) or parse_material_key(safe_key):
        if "\\" in path or "." in path.split("/"):
            raise HTTPException(status_code=400, detail="invalid curated path")
        response = await _get_curated_object(
            request,
            server.BUCKET_NAME,
            safe_key,
            config.RELAY_RESPONSE_MAX_BYTES,
        )
        _record_obs_audit(
            principal,
            "obs_download",
            started,
            {"path": path, "bytes": len(response.body)},
            db_path=config.RELAY_AUDIT_DB_PATH,
        )
        return response
    size = await _run_obs_op(
        obs_relay_ops.object_size,
        server.BUCKET_NAME,
        safe_key,
    )
    if size > config.RELAY_RESPONSE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="obs object too large")
    _record_obs_audit(
        principal,
        "obs_download",
        started,
        {"path": path, "bytes": size},
        db_path=config.RELAY_AUDIT_DB_PATH,
    )

    async def _stream() -> AsyncIterator[bytes]:
        outbound = current_outbound_runtime()
        if outbound.obs is None:
            raise RuntimeError("operator OBS runtime is unavailable")
        obs_runtime = outbound.obs
        events: queue.Queue[object] = queue.Queue(maxsize=2)
        lifecycle = _ObsStreamLifecycle(
            stop=threading.Event(),
            source_close=[None],
            source_terminal=threading.Event(),
            response_done=threading.Event(),
        )

        async def _produce() -> None:
            """Hold the SDK lease while the source iterator is consumed."""
            await obs_runtime.run(
                ObsProfileName.PRIMARY,
                lambda client: _produce_obs_chunks(
                    _ObsChunkRequest(
                        client=client,
                        events=events,
                        bucket=server.BUCKET_NAME,
                        object_key=safe_key,
                        lifecycle=lifecycle,
                    )
                ),
            )

        producer = asyncio.create_task(_produce())
        try:
            while True:
                try:
                    event = events.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.01)
                    continue
                if event is _STREAM_END:
                    return
                kind, value = cast(tuple[str, Any], event)
                if kind == "error":
                    raise value
                yield value
        finally:
            consumer_aborted = not lifecycle.source_terminal.is_set()
            lifecycle.stop.set()
            close_source = lifecycle.source_close[0]
            if close_source is not None:
                close_source()
            if consumer_aborted:
                producer.cancel()
            lifecycle.response_done.set()
            await _wait_for_producer(producer)

    return StreamingResponse(
        _stream(),
        media_type="application/octet-stream",
        headers={"Content-Length": str(size)},
    )


async def _list_objects(
    request: Request,
    principal: ApiPrincipal = Depends(require_relay_access(_OBS_SERVICE)),
) -> Response:
    """List object keys under a read-authorized prefix."""
    started, config, server = _begin()
    prefix = _require_list_prefix(
        server.BUCKET_NAME, _require_query(request, "prefix"), principal
    )
    keys: list[str] = []
    marker: str | None = None
    while True:
        page, marker = await _run_obs_op(
            obs_relay_ops.list_object_keys_page,
            server.BUCKET_NAME,
            prefix,
            marker=marker,
        )
        keys.extend(page)
        if marker is None:
            break
    _record_obs_audit(
        principal,
        "obs_list",
        started,
        {"prefix": prefix, "count": len(keys)},
        db_path=config.RELAY_AUDIT_DB_PATH,
    )
    return JSONResponse({"keys": keys})


async def _put_dir(
    request: Request,
    principal: ApiPrincipal = Depends(require_relay_access(_OBS_SERVICE)),
) -> Response:
    """Create a zero-byte directory-marker object at the validated key."""
    started, config, server = _begin()
    path = _require_query(request, "path")
    safe_key = _require_tenant_prefix(server.BUCKET_NAME, path, principal)
    key = await _run_obs_op(
        obs_relay_ops.put_dir_marker,
        server.BUCKET_NAME,
        safe_key,
    )
    _record_obs_audit(
        principal,
        "obs_mkdir",
        started,
        {"path": path, "key": key},
        db_path=config.RELAY_AUDIT_DB_PATH,
    )
    return JSONResponse(
        {"obs_path": obs_path_from_key(server.BUCKET_NAME, key)}
    )


def add_obs_routes(router: APIRouter) -> None:
    """Register the OBS object relay routes on the relay router.

    Args:
        router: The ``/v1/relay`` router to attach the OBS routes to.
    """
    router.add_api_route("/obs/object", _put_object, methods=["PUT"])
    router.add_api_route("/obs/object", _get_object, methods=["GET"])
    router.add_api_route("/obs/list", _list_objects, methods=["GET"])
    router.add_api_route("/obs/dir", _put_dir, methods=["PUT"])


_STREAM_END = object()


def _put_stream_event(
    events: queue.Queue[object], event: object, stop: threading.Event
) -> None:
    """Put one producer event without wedging cancellation on a full queue."""
    while not stop.is_set():
        try:
            events.put(event, timeout=0.05)
            return
        except queue.Full:
            continue


def _produce_obs_chunks(
    request: _ObsChunkRequest,
) -> None:
    """Read and publish SDK chunks while the caller owns the OBS lease."""
    iterator: Iterator[bytes] | None = None
    try:
        try:
            iterator = obs_relay_ops.iter_object_chunks(
                request.bucket,
                request.object_key,
                access=ObsAccessOptions(client=request.client),
                stream=obs_relay_ops.ObsStreamOptions(
                    on_source_open=lambda close: (
                        request.lifecycle.source_close.__setitem__(0, close)
                    ),
                    stop=request.lifecycle.stop,
                ),
            )
            for chunk in iterator:
                if request.lifecycle.stop.is_set():
                    return
                _put_stream_event(
                    request.events,
                    ("chunk", chunk),
                    request.lifecycle.stop,
                )
            if not request.lifecycle.stop.is_set():
                request.lifecycle.source_terminal.set()
        finally:
            if iterator is not None:
                _close_stream_iterator(iterator)
    except _OBS_OPERATION_ERRORS as exc:
        request.lifecycle.source_terminal.set()
        _put_stream_event(
            request.events,
            ("error", exc),
            request.lifecycle.stop,
        )
        raise
    finally:
        _put_stream_event(
            request.events,
            _STREAM_END,
            request.lifecycle.stop,
        )
        request.lifecycle.response_done.wait()


async def _wait_for_producer(producer: asyncio.Task[None]) -> None:
    """Drain a stream producer even when the downstream task is cancelled."""
    caller_cancelled = False
    current = asyncio.current_task()
    while not producer.done():
        try:
            await asyncio.shield(producer)
        except asyncio.CancelledError:
            if current is not None and current.cancelling():
                caller_cancelled = True
    if caller_cancelled:
        raise asyncio.CancelledError
    if not producer.cancelled():
        producer.result()
