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
import re
import sqlite3
import time
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.requests import Request

from ...config.defaults import ApiConfig, ServerConfig
from ...runtime.request_context import current_request_id
from ...storage import obs_relay_ops
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

# A content-addressed shared path must carry a FULL sha256 fingerprint
# segment (64 lowercase hex) plus something after it. Anchoring on the
# fingerprint (not the bare ``shared/`` prefix) keeps the possession-of-
# fingerprint model intact: a caller cannot list the bare shared root to
# enumerate other tenants' fingerprints, only read a path whose
# (unguessable) fingerprint it already possesses.
_SHARED_FP_RE = re.compile(
    rf"^{re.escape(AGENT_DATA_ROOT)}/shared/[0-9a-f]{{64}}/"
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
    """Run a blocking OBS op off-thread, mapping a path escape to 400."""
    try:
        return await asyncio.to_thread(op, *args, **kwargs)
    except ObsPathError as exc:
        raise HTTPException(
            status_code=400, detail="obs path outside bucket"
        ) from exc


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


def _require_output_prefix(
    bucket: str, prefix: str, principal: ApiPrincipal
) -> str:
    """Return a normalized list prefix confined to the caller's output root.

    The list relay is bound to the caller key's own output namespace
    (``USER_DATA_ROOT/<user_id>``) so a customer key cannot enumerate
    another tenant's output dirs in the shared operator bucket, let alone
    arbitrary bucket prefixes; anything else is a 403.

    A content-addressed shared path (``AGENT_DATA_ROOT/shared/<64-hex>/``)
    is also permitted on a possession-of-fingerprint basis: the path must
    carry a full sha256 fingerprint segment — the bare shared root is
    rejected so a caller cannot enumerate it — and the fingerprint is
    unguessable, so a caller that knows it already proved possession of the
    inputs that produced it.
    """
    try:
        normalized = normalize_obs_object_key(prefix, bucket)
    except ObsPathError as exc:
        raise HTTPException(
            status_code=400, detail="obs prefix outside bucket"
        ) from exc
    user_prefix = f"{USER_DATA_ROOT}/{principal.user_id}/"
    if not (
        normalized.startswith(user_prefix) or _SHARED_FP_RE.match(normalized)
    ):
        raise HTTPException(
            status_code=403,
            detail="list prefix outside the tenant output root",
        )
    return normalized


def _require_tenant_prefix(
    bucket: str, path: str, principal: ApiPrincipal
) -> str:
    """Return a normalized object key confined to the caller's namespace.

    Object read / write / dir relay is bound to the caller key's own
    tenant namespace under the two real roots (``user_data`` outputs and
    ``uploads``). Even inside the shared operator bucket a key cannot
    reach another tenant's objects; anything else is a 403.

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
        obs_server=server.OBS_SERVER,
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


async def _get_object(
    request: Request,
    principal: ApiPrincipal = Depends(require_relay_access(_OBS_SERVICE)),
) -> Response:
    """Stream the client-supplied (validated) object key under a budget."""
    started, config, server = _begin()
    path = _require_query(request, "path")
    safe_key = _require_tenant_prefix(server.BUCKET_NAME, path, principal)
    size = await _run_obs_op(
        obs_relay_ops.object_size,
        server.BUCKET_NAME,
        safe_key,
        obs_server=server.OBS_SERVER,
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

    def _stream() -> Iterator[bytes]:
        yield from obs_relay_ops.iter_object_chunks(
            server.BUCKET_NAME, safe_key, obs_server=server.OBS_SERVER
        )

    return StreamingResponse(
        _stream(),
        media_type="application/octet-stream",
        headers={"Content-Length": str(size)},
    )


async def _list_objects(
    request: Request,
    principal: ApiPrincipal = Depends(require_relay_access(_OBS_SERVICE)),
) -> Response:
    """List object keys under an output-root-confined prefix."""
    started, config, server = _begin()
    prefix = _require_output_prefix(
        server.BUCKET_NAME, _require_query(request, "prefix"), principal
    )
    keys = await _run_obs_op(
        obs_relay_ops.list_object_keys,
        server.BUCKET_NAME,
        prefix,
        obs_server=server.OBS_SERVER,
    )
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
        obs_server=server.OBS_SERVER,
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
