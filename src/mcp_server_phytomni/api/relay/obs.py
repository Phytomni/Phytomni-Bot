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
import sqlite3
import time
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from starlette.requests import Request

from ...config.defaults import ApiConfig, ServerConfig
from ...runtime.request_context import current_request_id
from ...storage import obs_relay_ops
from ...storage.obs_storage import (
    ObsPathError,
    normalize_obs_object_key,
    obs_path_from_key,
)
from ...storage.path_policy import USER_DATA_ROOT
from ..auth import ApiPrincipal
from .audit import RelayAuditRecord, get_audit_store
from .deps import read_relay_body, require_relay_access

__all__ = ["add_obs_routes"]

_OBS_SERVICE = "obs"
_LOGGER = logging.getLogger(__name__)


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


def _require_output_prefix(bucket: str, prefix: str) -> str:
    """Return a normalized list prefix confined to the output root.

    The list relay is bound to the server-owned analyst output root
    (``USER_DATA_ROOT``) so a customer key cannot enumerate arbitrary
    bucket prefixes; anything else is a 403.
    """
    try:
        normalized = normalize_obs_object_key(prefix, bucket)
    except ObsPathError as exc:
        raise HTTPException(
            status_code=400, detail="obs prefix outside bucket"
        ) from exc
    if not normalized.startswith(f"{USER_DATA_ROOT}/"):
        raise HTTPException(
            status_code=403, detail="list prefix outside the output root"
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
    key = await _run_obs_op(
        obs_relay_ops.put_object_bytes,
        server.BUCKET_NAME,
        path,
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
    """Return the bytes of the client-supplied (validated) object key."""
    started, config, server = _begin()
    path = _require_query(request, "path")
    data = await _run_obs_op(
        obs_relay_ops.get_object_bytes,
        server.BUCKET_NAME,
        path,
        obs_server=server.OBS_SERVER,
    )
    _record_obs_audit(
        principal,
        "obs_download",
        started,
        {"path": path, "bytes": len(data)},
        db_path=config.RELAY_AUDIT_DB_PATH,
    )
    return Response(content=data, media_type="application/octet-stream")


async def _list_objects(
    request: Request,
    principal: ApiPrincipal = Depends(require_relay_access(_OBS_SERVICE)),
) -> Response:
    """List object keys under an output-root-confined prefix."""
    started, config, server = _begin()
    prefix = _require_output_prefix(
        server.BUCKET_NAME, _require_query(request, "prefix")
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
    key = await _run_obs_op(
        obs_relay_ops.put_dir_marker,
        server.BUCKET_NAME,
        path,
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
