# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Upstream-facing /v1/relay router for the credential-injecting relay.

The router is always mounted; a per-request guard re-reads RELAY_ENABLED
so the surface is hidden by default and an operator can disable it
mid-incident without restarting a worker. It exposes a liveness probe
plus the OpenAI-family (transparent) and platform-family (envelope)
relay routes, each scope-gated and forwarding through the core.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from mcp.shared.exceptions import McpError
from starlette.requests import Request

from ...agents.shared.gauss import gauss_query
from ...auth.iam import get_token
from ...config.defaults import ApiConfig, DeepGenomeConfig
from ...config.settings import get_sensitive_config
from ...runtime.request_context import current_request_id
from ..auth import ApiPrincipal, relay_scope_satisfied
from .audit import RelayAuditRecord, get_audit_store
from .deps import (
    read_relay_body,
    relay_enabled_guard,
    require_relay_access,
)
from .forward import (
    RelayErrorMode,
    RelayInjectionStrategy,
    RelayUpstream,
    build_relay_query,
    forward_relay_request,
    validate_relay_path_segment,
)
from .obs import add_obs_routes
from .research_grants import ResearchGrantStore
from .research_input import (
    add_research_input_routes,
    get_research_grant_store,
)

__all__ = [
    "create_relay_router",
    "relay_enabled_guard",
    "require_relay_access",
    "read_relay_body",
]

# OpenAI-family relay services: each fronts an upstream the operator
# reaches with an Authorization: Bearer key, with the SDK path appended
# to the configured base URL. The customer query string is never carried
# onto the operator-credentialed call (the upstream URL is config-only).
_OPENAI_RELAYS = (
    ("llm", "chat/completions", "BASE_URL", "API_KEY"),
    ("coder", "chat/completions", "CODER_URL", "CODER_API_KEY"),
    ("embed", "embeddings", "EMBED_URL", "EMBED_API_KEY"),
)

# Platform-family relay services (ENVELOPE mode): the configured URL is
# the POST target verbatim (no path appended). inject kind is one of
# "none" (upstream is unauthenticated) or "iam" (X-Auth-Token via
# get_token, with the optional region attr).
_PLATFORM_RELAYS = (
    ("retrieve", "search", "RETRIEVE_URL", "none", None),
    ("rerank", "rank", "RERANK_URL", "none", None),
    ("database", "nl2sql", "DATABASE_URL", "iam", None),
    ("analysis", "tasks", "ANALYSIS_URL", "iam", "ANALYSIS_REGION"),
)

_PRIVATE_RESEARCH_KEYS = frozenset(
    ("research_grant_sidecar", "research_input_grants", "research_grants")
    + ("exact_reference", "grant_id", "snapshot_digest")
    + ("parent_run_id", "execution_fingerprint")
)


@dataclass(frozen=True, slots=True)
class _PlatformRelaySpec:
    """Configuration for one platform-family relay route."""

    name: str
    url_attr: str
    inject_kind: str
    region_attr: str | None


@dataclass(frozen=True, slots=True)
class _ResearchSidecarBinding:
    """Parsed operator-private binding carried by a Research sidecar."""

    parent_run_id: str
    execution_fingerprint: str
    expected: Mapping[str, tuple[str, str, str]]


async def _relay_no_inject() -> dict[str, str]:
    """Inject no operator credential (the upstream is unauthenticated)."""
    return {}


def _build_platform_inject(
    kind: str, region: str | None
) -> RelayInjectionStrategy:
    """Build the per-service injection strategy for a platform route.

    ``none`` injects nothing; ``iam`` mints an IAM ``X-Auth-Token`` via
    ``get_token`` (with the service's region when one applies).
    """
    if kind == "none":
        return _relay_no_inject

    async def _iam_inject() -> dict[str, str]:
        token = await get_token(region=region) if region else await get_token()
        return {"X-Auth-Token": token}

    return _iam_inject


def _openai_relay_handler(
    name: str, path: str, url_field: str, key_field: str
) -> Callable[..., Awaitable[Response]]:
    """Build a TRANSPARENT relay handler for one OpenAI-family service.

    The handler is scope-gated, reads the body under the byte budget,
    injects the operator Bearer key, and forwards to the config base URL
    with ``path`` appended (the client query is dropped — the upstream
    URL is config-resolved only).
    """

    async def _handler(
        request: Request,
        principal: ApiPrincipal = Depends(require_relay_access(name)),
    ) -> Response:
        config = ApiConfig()
        body = await read_relay_body(request, config.RELAY_REQUEST_MAX_BYTES)
        sensitive = get_sensitive_config()
        base = getattr(sensitive, url_field).rstrip("/")
        key = getattr(sensitive, key_field).get_secret_value()

        async def _inject() -> dict[str, str]:
            return {"Authorization": f"Bearer {key}"}

        upstream = RelayUpstream(
            url=f"{base}/{path}",
            error_mode=RelayErrorMode.TRANSPARENT,
            service=name,
            inject_headers=_inject,
        )
        return await forward_relay_request(
            request=request,
            body=body,
            upstream=upstream,
            principal=principal,
            audit_store=get_audit_store(config.RELAY_AUDIT_DB_PATH),
        )

    return _handler


def _platform_relay_handler(
    spec: _PlatformRelaySpec,
) -> Callable[..., Awaitable[Response]]:
    """Build an ENVELOPE relay handler for one platform-family service.

    The handler is scope-gated, reads the body under the byte budget,
    forwards it verbatim to the config-resolved URL (used as-is, no path
    appended, no client query), and injects the per-service credential.
    """

    async def _handler(
        request: Request,
        principal: ApiPrincipal = Depends(require_relay_access(spec.name)),
    ) -> Response:
        config = ApiConfig()
        body = await read_relay_body(request, config.RELAY_REQUEST_MAX_BYTES)
        return await _forward_platform_body(
            request=request,
            body=body,
            principal=principal,
            spec=spec,
        )

    return _handler


def _research_analysis_relay_handler(
    spec: _PlatformRelaySpec,
) -> Callable[..., Awaitable[Response]]:
    """Build the analysis submit handler with grant-store injection."""

    async def _handler(
        request: Request,
        principal: ApiPrincipal = Depends(require_relay_access("analysis")),
        grant_store: ResearchGrantStore = Depends(get_research_grant_store),
    ) -> Response:
        config = ApiConfig()
        body = await read_relay_body(request, config.RELAY_REQUEST_MAX_BYTES)
        body = _unwrap_research_analysis_body(body, principal, grant_store)
        return await _forward_platform_body(
            request=request,
            body=body,
            principal=principal,
            spec=spec,
        )

    return _handler


async def _forward_platform_body(
    request: Request,
    body: bytes,
    principal: ApiPrincipal,
    spec: _PlatformRelaySpec,
) -> Response:
    """Forward one already-read platform body through the relay core."""
    config = ApiConfig()
    platform = DeepGenomeConfig()
    region = getattr(platform, spec.region_attr) if spec.region_attr else None
    upstream = RelayUpstream(
        url=getattr(platform, spec.url_attr),
        error_mode=RelayErrorMode.ENVELOPE,
        service=spec.name,
        inject_headers=_build_platform_inject(spec.inject_kind, region),
    )
    return await forward_relay_request(
        request=request,
        body=body,
        upstream=upstream,
        principal=principal,
        audit_store=get_audit_store(config.RELAY_AUDIT_DB_PATH),
    )


def _unwrap_research_analysis_body(
    body: bytes,
    principal: ApiPrincipal,
    grant_store: ResearchGrantStore,
) -> bytes:
    """Verify a private sidecar and return only its Analyst request bytes."""
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, ValueError):
        return body
    if not isinstance(payload, dict) or "research_input_grants" not in payload:
        return body
    if set(payload) != {"analysis_request", "research_input_grants"}:
        raise _invalid_research_sidecar()
    if not relay_scope_satisfied(principal.scopes, "research-input"):
        raise HTTPException(status_code=403, detail="insufficient scope")
    analysis_request = payload.get("analysis_request")
    sidecar = payload.get("research_input_grants")
    _verify_research_sidecar(principal, sidecar, grant_store)
    if not isinstance(analysis_request, dict):
        raise _invalid_research_sidecar()
    if _contains_private_grant_key(analysis_request):
        raise _invalid_research_sidecar()
    return json.dumps(
        analysis_request, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def _verify_research_sidecar(
    principal: ApiPrincipal,
    sidecar: object,
    grant_store: ResearchGrantStore,
) -> None:
    """Require exactly one active grant row per sidecar object."""
    binding = _parse_research_sidecar(sidecar)
    rows = _read_research_grant_rows(grant_store, tuple(binding.expected))
    now = datetime.now(UTC).isoformat()
    if len(rows) != len(binding.expected):
        raise _invalid_research_sidecar()
    for row in rows:
        if not _grant_row_matches(row, principal, binding, now):
            raise _invalid_research_sidecar()


def _parse_research_sidecar(
    sidecar: object,
) -> _ResearchSidecarBinding:
    """Parse strict sidecar bindings without exposing their values."""
    if not isinstance(sidecar, dict) or set(sidecar) != {
        "schema_version",
        "parent_run_id",
        "execution_fingerprint",
        "objects",
    }:
        raise _invalid_research_sidecar()
    parent_run_id = sidecar["parent_run_id"]
    execution_fingerprint = sidecar["execution_fingerprint"]
    objects = sidecar["objects"]
    if (
        sidecar["schema_version"] != 1
        or not _safe_sidecar_text(parent_run_id)
        or not _safe_sidecar_text(execution_fingerprint)
        or not isinstance(objects, list)
        or not objects
    ):
        raise _invalid_research_sidecar()
    expected: dict[str, tuple[str, str, str]] = {}
    dataset_ids: set[str] = set()
    references: set[str] = set()
    for item in objects:
        grant_id, binding = _parse_research_sidecar_object(item)
        dataset_id, exact_reference, _ = binding
        if (
            dataset_id in dataset_ids
            or exact_reference in references
            or grant_id in expected
        ):
            raise _invalid_research_sidecar()
        dataset_ids.add(dataset_id)
        references.add(exact_reference)
        expected[grant_id] = binding
    return _ResearchSidecarBinding(
        parent_run_id=parent_run_id,
        execution_fingerprint=execution_fingerprint,
        expected=expected,
    )


def _parse_research_sidecar_object(
    item: object,
) -> tuple[str, tuple[str, str, str]]:
    """Parse one exact grant object from a private sidecar."""
    if not isinstance(item, dict) or set(item) != {
        "dataset_id",
        "exact_reference",
        "grant_id",
        "snapshot_digest",
    }:
        raise _invalid_research_sidecar()
    values = tuple(
        item[field]
        for field in (
            "dataset_id",
            "exact_reference",
            "grant_id",
            "snapshot_digest",
        )
    )
    if not all(_safe_sidecar_text(value) for value in values):
        raise _invalid_research_sidecar()
    dataset_id, exact_reference, grant_id, snapshot_digest = values
    return grant_id, (dataset_id, exact_reference, snapshot_digest)


def _grant_row_matches(
    row: sqlite3.Row,
    principal: ApiPrincipal,
    binding: _ResearchSidecarBinding,
    now: str,
) -> bool:
    """Compare one private row against its exact sidecar binding."""
    grant_id = row["grant_id"]
    expected_values = binding.expected.get(grant_id)
    if expected_values is None:
        return False
    return _grant_context_matches(row, principal, binding) and (
        _grant_is_unexpired(row["expires_at"], now)
        and tuple(
            row[field]
            for field in ("dataset_id", "exact_reference", "snapshot_digest")
        )
        == expected_values
    )


def _grant_context_matches(
    row: sqlite3.Row,
    principal: ApiPrincipal,
    binding: _ResearchSidecarBinding,
) -> bool:
    """Compare principal/run/execution/schema/state binding fields."""
    return (
        row["principal_key_prefix"] == principal.key_prefix
        and row["parent_run_id"] == binding.parent_run_id
        and row["execution_fingerprint"] == binding.execution_fingerprint
        and row["grant_schema_version"] == 3
        and row["state"] == "active"
    )


def _read_research_grant_rows(
    grant_store: ResearchGrantStore,
    grant_ids: tuple[str, ...],
) -> list[sqlite3.Row]:
    """Read only binding columns from the operator-private grant table."""
    placeholders = ",".join("?" for _ in grant_ids)
    query = (
        "SELECT grant_id, principal_key_prefix, parent_run_id, "
        "execution_fingerprint, dataset_id, exact_reference, "
        "snapshot_digest, state, expires_at, grant_schema_version "
        "FROM research_object_grants "
        f"WHERE grant_id IN ({placeholders})"
    )
    try:
        with sqlite3.connect(grant_store.db_path) as connection:
            connection.row_factory = sqlite3.Row
            return list(connection.execute(query, grant_ids))
    except (OSError, sqlite3.Error):
        raise _invalid_research_sidecar() from None


def _safe_sidecar_text(value: object) -> bool:
    """Return whether one opaque sidecar text value is bounded and safe."""
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 4096
        and all(
            ord(character) >= 32 and ord(character) != 127
            for character in value
        )
    )


def _grant_is_unexpired(expires_at: object, now: str) -> bool:
    """Compare store timestamps without accepting malformed expiry data."""
    if not isinstance(expires_at, str):
        return False
    try:
        return datetime.fromisoformat(expires_at) > datetime.fromisoformat(now)
    except ValueError:
        return False


def _contains_private_grant_key(value: object) -> bool:
    """Reject a nested private sidecar instead of forwarding it upstream."""
    if isinstance(value, Mapping):
        return any(
            key in _PRIVATE_RESEARCH_KEYS or _contains_private_grant_key(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_private_grant_key(child) for child in value)
    return False


def _invalid_research_sidecar() -> HTTPException:
    """Build one non-disclosing sidecar rejection."""
    return HTTPException(
        status_code=400, detail="invalid research grant sidecar"
    )


# Analysis-platform lifecycle ops. The client task id is validated and
# appended to ANALYSIS_URL (the submit base); the op suffix follows, with
# an IAM X-Auth-Token for the analysis region. ``logs`` opts its task_name
# query key back in; status/terminate carry no client query.
_ANALYSIS_LIFECYCLE = (
    ("", "GET", (), "analysis_status"),
    ("/logs", "GET", ("task_name",), "analysis_logs"),
    ("/terminate", "POST", (), "analysis_terminate"),
)


def _analysis_lifecycle_handler(
    suffix: str, query_allow: tuple[str, ...], operation: str
) -> Callable[..., Awaitable[Response]]:
    """Build an IAM-injected handler for one analysis-lifecycle op.

    Scope-gated on ``relay:analysis``, the handler validates the client
    task id, appends ``suffix`` (and the allowlisted query, if any) to the
    config ANALYSIS_URL, and injects the operator IAM token for the
    analysis region. The upstream URL is server-resolved: the only
    client-derived part is the validated task id plus allowlisted keys.
    """

    async def _handler(
        task_id: str,
        request: Request,
        principal: ApiPrincipal = Depends(require_relay_access("analysis")),
    ) -> Response:
        safe_id = validate_relay_path_segment(task_id, field="task_id")
        config = ApiConfig()
        body = await read_relay_body(request, config.RELAY_REQUEST_MAX_BYTES)
        platform = DeepGenomeConfig()
        url = f"{platform.ANALYSIS_URL}/{safe_id}{suffix}"
        query = build_relay_query(request.url.query, query_allow)
        if query:
            url = f"{url}?{query}"
        upstream = RelayUpstream(
            url=url,
            error_mode=RelayErrorMode.ENVELOPE,
            service="analysis",
            inject_headers=_build_platform_inject(
                "iam", platform.ANALYSIS_REGION
            ),
            operation=operation,
        )
        return await forward_relay_request(
            request=request,
            body=body,
            upstream=upstream,
            principal=principal,
            audit_store=get_audit_store(config.RELAY_AUDIT_DB_PATH),
        )

    return _handler


def _spa_faq_handler() -> Callable[..., Awaitable[Response]]:
    """Build the spa-faq relay handler (IAM-injected, proxy-bypass).

    Scope-gated on ``relay:spa-faq``, the handler validates the client repo
    id, fills it into the configured SPA_FAQ_URL template, allowlists the
    ``question`` / ``page_size`` / ``page_num`` query keys, and injects the
    operator IAM token. ``trust_env=False`` forces an ephemeral,
    proxy-bypassing client because the SPA FAQ upstream is a bare-IP host
    the operator's HTTP(S)_PROXY cannot reach.
    """

    async def _handler(
        repo_id: str,
        request: Request,
        principal: ApiPrincipal = Depends(require_relay_access("spa-faq")),
    ) -> Response:
        safe_repo = validate_relay_path_segment(repo_id, field="repo_id")
        config = ApiConfig()
        body = await read_relay_body(request, config.RELAY_REQUEST_MAX_BYTES)
        platform = DeepGenomeConfig()
        url = platform.SPA_FAQ_URL.format(repo_id=safe_repo)
        query = build_relay_query(
            request.url.query, ("question", "page_size", "page_num")
        )
        if query:
            url = f"{url}?{query}"
        upstream = RelayUpstream(
            url=url,
            error_mode=RelayErrorMode.ENVELOPE,
            service="spa_faq",
            inject_headers=_build_platform_inject("iam", None),
            trust_env=False,
        )
        return await forward_relay_request(
            request=request,
            body=body,
            upstream=upstream,
            principal=principal,
            audit_store=get_audit_store(config.RELAY_AUDIT_DB_PATH),
        )

    return _handler


def _bi_query_handler() -> Callable[..., Awaitable[Response]]:
    """Build the server-side BI query handler (no HTTP forward).

    Scope-gated on ``relay:bi``, the handler reads ``{"sql": ...}`` under
    the byte budget, runs it against GaussDB via ``gauss_query``, and
    returns the envelope. A driver error is converted to the
    ``{"message": "sql error", "data": []}`` envelope at this HTTP edge
    (gauss_query raises McpError in-process). Audit records metadata only.
    """

    async def _handler(
        request: Request,
        principal: ApiPrincipal = Depends(require_relay_access("bi")),
    ) -> Response:
        started = time.monotonic()
        config = ApiConfig()
        body = await read_relay_body(request, config.RELAY_REQUEST_MAX_BYTES)
        try:
            sql = json.loads(body)["sql"]
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="missing sql") from exc
        if not isinstance(sql, str) or not sql.strip():
            raise HTTPException(status_code=400, detail="invalid sql")
        try:
            payload = await gauss_query(sql)
        except McpError:
            payload = {"message": "sql error", "data": []}
        entry = RelayAuditRecord(
            request_id=current_request_id() or "",
            user_id=principal.user_id,
            key_prefix=principal.key_prefix,
            service="bi",
            operation="bi_query",
            status_code=200,
            duration_ms=int((time.monotonic() - started) * 1000),
            request_body=json.dumps({"sql_len": len(sql)}),
        )
        get_audit_store(config.RELAY_AUDIT_DB_PATH).record(entry)
        return JSONResponse(payload)

    return _handler


def create_relay_router() -> APIRouter:
    """Build the ``/v1/relay`` router gated by the enable kill-switch.

    Returns:
        An ``APIRouter`` whose every route runs ``relay_enabled_guard``
        first, exposing a liveness probe and the OpenAI-family relay
        routes (llm / coder / embed).
    """
    router = APIRouter(
        prefix="/v1/relay",
        dependencies=[Depends(relay_enabled_guard)],
    )

    @router.get("/healthz")
    async def relay_healthz() -> dict[str, str]:
        """Return a relay liveness signal when the relay is enabled."""
        return {"status": "ok"}

    # Literal research-grant routes must precede every parameterized relay
    # registration so capability and object-grant paths cannot be shadowed.
    add_research_input_routes(router)

    for name, path, url_field, key_field in _OPENAI_RELAYS:
        router.add_api_route(
            f"/{name}/{path}",
            _openai_relay_handler(name, path, url_field, key_field),
            methods=["POST"],
        )

    for name, path, url_attr, inject_kind, region_attr in _PLATFORM_RELAYS:
        spec = _PlatformRelaySpec(name, url_attr, inject_kind, region_attr)
        handler = (
            _research_analysis_relay_handler(spec)
            if name == "analysis" and path == "tasks"
            else _platform_relay_handler(spec)
        )
        router.add_api_route(
            f"/{name}/{path}",
            handler,
            methods=["POST"],
        )

    router.add_api_route("/bi/query", _bi_query_handler(), methods=["POST"])

    # Registered after the literal /analysis/tasks submit route so a POST to
    # it matches the submit, not the {task_id} param route.
    for suffix, method, query_allow, operation in _ANALYSIS_LIFECYCLE:
        router.add_api_route(
            f"/analysis/{{task_id}}{suffix}",
            _analysis_lifecycle_handler(suffix, query_allow, operation),
            methods=[method],
        )

    router.add_api_route(
        "/spa-faq/{repo_id}",
        _spa_faq_handler(),
        methods=["GET"],
    )

    add_obs_routes(router)

    return router
