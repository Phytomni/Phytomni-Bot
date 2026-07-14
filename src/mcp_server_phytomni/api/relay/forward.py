# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Forwarding core for the credential-injecting relay.

Holds the header and body helpers the relay forward path relies on:
the request-side strip set (caller credentials, Host, Content-Length,
hop-by-hop), the response-side allowlist that defeats reflected-header
credential leaks, and the non-2xx body secret scrub.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import re
import sqlite3
import time
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Mapping,
)
from contextlib import AsyncExitStack, aclosing
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode

import httpx
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse
from starlette.requests import Request

from ...common.httpx_client import get_async_client
from ...config.defaults import ApiConfig
from ...runtime.request_context import current_request_id
from ..auth import ApiPrincipal
from .audit import RelayAuditRecord, RelayAuditStore
from .audit_filter import (
    CREDENTIAL_HEADERS,
    redact_body_text,
    sanitize_audit_body,
)

__all__ = [
    "prepare_forward_headers",
    "filter_response_headers",
    "scrub_secrets",
    "validate_relay_path_segment",
    "build_relay_query",
    "RelayFinishReason",
    "TeeOutcome",
    "tee_and_stream",
    "RelayErrorMode",
    "RelayInjectionStrategy",
    "RelayUpstream",
    "forward_relay_request",
]

_LOGGER = logging.getLogger(__name__)

RelayInjectionStrategy = Callable[[], Awaitable[dict[str, str]]]


class RelayErrorMode(enum.Enum):
    """How a relay route surfaces an upstream error to the caller.

    TRANSPARENT (OpenAI-family) passes the upstream status and body
    through so the caller's SDK parses them; ENVELOPE (platform-family)
    maps an upstream error to the unified ApiErrorResponse envelope by
    raising an HTTPException the app's handler renders.
    """

    TRANSPARENT = "transparent"
    ENVELOPE = "envelope"


_REDACTION = b"***redacted***"

# RFC 7230 hop-by-hop headers: meaningful only for a single transport
# hop, so they must never be relayed to or from the upstream.
_HOP_BY_HOP: frozenset[str] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

# Dropped from the outbound request: caller credentials never reach the
# upstream, Host is re-derived by httpx from the upstream URL, and a
# stale/lying Content-Length is recomputed from the body httpx sends.
_REQUEST_DROP: frozenset[str] = (
    CREDENTIAL_HEADERS | _HOP_BY_HOP | frozenset({"host", "content-length"})
)

# Returned to the client: an ALLOWLIST, not a denylist, so an upstream
# or front proxy that reflects the injected operator credential into an
# unforeseen response header cannot leak it. Content-Type is required so
# the caller's OpenAI SDK parses the body.
_RESPONSE_ALLOW: frozenset[str] = frozenset({"content-type"})

# Per-key count of relay forwards currently in flight (per worker, like
# the rate limiter). A dict literal so the count is mutated in place
# without a global statement, by _acquire_key_slot and its release.
_INFLIGHT: dict[str, int] = {}


def prepare_forward_headers(inbound: Mapping[str, str]) -> dict[str, str]:
    """Return the headers to forward upstream, with unsafe ones removed.

    Drops caller credentials (:data:`CREDENTIAL_HEADERS`), ``Host``,
    ``Content-Length``, and the hop-by-hop set. The injected operator
    credential is added later on the per-request call, never here.

    Args:
        inbound: The caller's request headers.

    Returns:
        A new dict of headers safe to forward upstream.
    """
    return {
        key: value
        for key, value in inbound.items()
        if key.lower() not in _REQUEST_DROP
    }


def filter_response_headers(upstream: Mapping[str, str]) -> dict[str, str]:
    """Return the allowlisted subset of upstream response headers.

    Only headers in :data:`_RESPONSE_ALLOW` survive; everything else is
    dropped so no reflected or credential-bearing header (Set-Cookie,
    WWW-Authenticate, an echoed Authorization, vendor debug headers)
    reaches the client.

    Args:
        upstream: The upstream response headers.

    Returns:
        A new dict containing only allowlisted response headers.
    """
    return {
        key: value
        for key, value in upstream.items()
        if key.lower() in _RESPONSE_ALLOW
    }


def scrub_secrets(body: bytes, secrets: Iterable[str]) -> bytes:
    """Mask any injected operator secret echoed back in a response body.

    Defense in depth for the TRANSPARENT non-2xx path, where the body is
    otherwise forwarded verbatim: a front proxy or debug page that
    reflects the request's injected credential into its error body would
    leak it, so each injected secret value is replaced with a redaction
    marker. An empty secret is ignored so it cannot blanket-mask a body.

    Args:
        body: The raw response body about to reach the client.
        secrets: The injected secret values to mask (e.g. the Bearer
            header value or the IAM token).

    Returns:
        The body with every non-empty secret occurrence redacted.
    """
    for secret in secrets:
        if secret:
            body = body.replace(secret.encode("utf-8"), _REDACTION)
    return body


# A relay path segment ({task_id}/{repo_id}) is appended to a config URL,
# so it must not smuggle traversal or an alternate host/path: only an
# unreserved-id charset survives and ".." is rejected outright.
_SAFE_PATH_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")


def validate_relay_path_segment(value: str, *, field: str) -> str:
    """Validate a client-supplied relay path segment or reject it.

    The segment is appended to a server-resolved upstream URL, so it must
    not carry a path separator, traversal sequence, scheme, or whitespace
    that could redirect the call to another host or path.

    Args:
        value: The raw path segment from the client request.
        field: Field name used in the rejection detail (e.g. ``task_id``).

    Returns:
        The segment unchanged when it is a safe identifier.

    Raises:
        HTTPException: 400 when the segment is empty, contains ``..``, or
            holds any character outside ``[A-Za-z0-9._-]``.
    """
    if not _SAFE_PATH_SEGMENT.fullmatch(value) or ".." in value:
        raise HTTPException(status_code=400, detail=f"invalid relay {field}")
    return value


def build_relay_query(query_string: str, allowed: Iterable[str]) -> str:
    """Rebuild a query string keeping only allowlisted keys.

    The client query is never forwarded wholesale (the upstream URL is
    server-resolved); a route opts specific keys back in by allowlist.
    Repeated keys and their order are preserved and values are re-encoded.

    Args:
        query_string: The raw inbound query string (no leading ``?``).
        allowed: The query keys a route permits onto the upstream call.

    Returns:
        A URL-encoded query string of the allowlisted pairs, or ``""``.
    """
    permitted = set(allowed)
    pairs = [
        (key, value)
        for key, value in parse_qsl(query_string, keep_blank_values=True)
        if key in permitted
    ]
    return urlencode(pairs)


class RelayFinishReason(enum.Enum):
    """How a relayed response stream ended, for the audit record."""

    COMPLETE = "complete"
    UPSTREAM_ABORTED = "upstream_aborted"
    CLIENT_DISCONNECTED = "client_disconnected"
    DEADLINE_EXCEEDED = "deadline_exceeded"


@dataclass(frozen=True)
class TeeOutcome:
    """The audit-relevant result of teeing a relayed response stream.

    Attributes:
        body: The captured audit copy, capped at the audit budget.
        finish_reason: How the stream ended.
        truncated: True when the upstream sent more than the audit cap,
            so ``body`` is shorter than ``total_bytes``.
        total_bytes: Total upstream bytes streamed to the client.
        error_type: The upstream exception type name on an abort, else
            None.
    """

    body: bytes
    finish_reason: RelayFinishReason
    truncated: bool
    total_bytes: int
    error_type: str | None = None


async def tee_and_stream(
    source: AsyncIterator[bytes],
    *,
    audit_cap: int,
    on_complete: Callable[[TeeOutcome], Awaitable[None]],
    deadline: float | None = None,
) -> AsyncGenerator[bytes, None]:
    """Stream upstream chunks to the client while capturing an audit copy.

    Every chunk is yielded to the client verbatim and untruncated; the
    audit copy stops growing once it reaches ``audit_cap`` (the cap is
    enforced during accumulation, never by buffering the whole body).
    ``on_complete`` is invoked exactly once with the outcome, marking an
    upstream mid-stream abort, a client disconnect, and a wall-clock
    deadline distinctly so the audit trail can tell a clean 200 from a
    truncated one. When ``deadline`` is set it bounds the total
    upstream-production time so a drip-feeding upstream cannot hold the
    connection open indefinitely.

    Args:
        source: The upstream response byte iterator.
        audit_cap: Maximum bytes to retain for the audit copy.
        on_complete: Coroutine called once with the :class:`TeeOutcome`.
        deadline: Optional total wall-clock budget in seconds for the
            whole upstream stream.

    Yields:
        Each upstream chunk, unmodified.
    """
    copy = bytearray()
    total = 0
    reason = RelayFinishReason.COMPLETE
    error_type: str | None = None
    started = time.monotonic()
    try:
        while True:
            timeout = None
            if deadline is not None:
                timeout = deadline - (time.monotonic() - started)
                if timeout <= 0:
                    reason = RelayFinishReason.DEADLINE_EXCEEDED
                    error_type = "deadline_exceeded"
                    break
            try:
                chunk = await asyncio.wait_for(anext(source), timeout)
            except StopAsyncIteration:
                break
            except TimeoutError:
                reason = RelayFinishReason.DEADLINE_EXCEEDED
                error_type = "deadline_exceeded"
                break
            total += len(chunk)
            if len(copy) < audit_cap:
                copy.extend(chunk[: audit_cap - len(copy)])
            yield chunk
    except (GeneratorExit, asyncio.CancelledError):
        reason = RelayFinishReason.CLIENT_DISCONNECTED
        raise
    except (httpx.HTTPError, OSError) as exc:
        reason = RelayFinishReason.UPSTREAM_ABORTED
        error_type = type(exc).__name__
    finally:
        await on_complete(
            TeeOutcome(
                body=bytes(copy),
                finish_reason=reason,
                truncated=total > len(copy),
                total_bytes=total,
                error_type=error_type,
            )
        )


@dataclass(frozen=True)
class RelayUpstream:
    """A resolved upstream target and its error shaping for one route.

    Attributes:
        url: Upstream URL resolved from server config (never client
            supplied, query included).
        error_mode: TRANSPARENT (pass status/body through) or ENVELOPE
            (map an upstream error to the unified envelope).
        service: Relay service name recorded in the audit row.
        inject_headers: Strategy minting the operator auth header(s).
        operation: Optional sub-operation label for the audit row.
        trust_env: When False the upstream call ignores the host proxy /
            cert env (an ephemeral client) — needed for a bare-IP upstream
            the host's HTTP(S)_PROXY cannot reach. True keeps the shared
            keep-alive pool.
    """

    url: str
    error_mode: RelayErrorMode
    service: str
    inject_headers: RelayInjectionStrategy
    operation: str | None = None
    trust_env: bool = True


async def _buffered_relay_response(
    upstream: httpx.Response,
    *,
    error_mode: RelayErrorMode,
    secrets: list[str],
    cap: int,
    record: Callable[..., None],
) -> Response:
    """Buffer, audit, and shape a non-streamed relay response.

    TRANSPARENT passes the upstream status/body through with the injected
    secret scrubbed from any echoed error body; ENVELOPE returns a 2xx
    body and maps an upstream error to the unified envelope.
    """
    raw = await upstream.aread()
    status = upstream.status_code
    audit_body = sanitize_audit_body(raw, cap)
    record(status_code=status, response_body=audit_body, error_type=None)
    if error_mode is RelayErrorMode.TRANSPARENT:
        return Response(
            content=scrub_secrets(raw, secrets),
            status_code=status,
            headers=filter_response_headers(upstream.headers),
        )
    if 200 <= status < 300:
        return Response(
            content=raw,
            status_code=status,
            media_type=upstream.headers.get("content-type"),
        )
    raise HTTPException(status_code=status, detail="relay upstream error")


def _streaming_relay_response(
    stack: AsyncExitStack,
    upstream: httpx.Response,
    *,
    cap: int,
    record: Callable[..., None],
    deadline: float | None = None,
) -> StreamingResponse:
    """Build a streamed 2xx relay response that tees + audits on end.

    The upstream stream and the shared-client context are released in the
    body generator's finally via ``stack.aclose()`` so they outlive this
    function until the client has consumed the whole response.
    """
    status = upstream.status_code

    async def _on_complete(outcome: TeeOutcome) -> None:
        body_text = sanitize_audit_body(outcome.body, cap)
        if outcome.truncated:
            dropped = outcome.total_bytes - len(outcome.body)
            body_text = (
                redact_body_text(body_text + f"<truncated: {dropped} bytes>")
                or ""
            )
        error_type = outcome.error_type
        if (
            error_type is None
            and outcome.finish_reason is not RelayFinishReason.COMPLETE
        ):
            error_type = outcome.finish_reason.value
        record(
            status_code=status,
            response_body=body_text,
            error_type=error_type,
        )

    async def _stream() -> AsyncIterator[bytes]:
        # aclosing() guarantees the tee's finally (the audit write) runs
        # when the client disconnects: closing this outer generator does
        # NOT cascade into the inner tee, so it must be closed explicitly.
        try:
            async with aclosing(
                tee_and_stream(
                    upstream.aiter_bytes(),
                    audit_cap=cap,
                    on_complete=_on_complete,
                    deadline=deadline,
                )
            ) as teed:
                async for chunk in teed:
                    yield chunk
        finally:
            await stack.aclose()

    return StreamingResponse(
        _stream(),
        status_code=status,
        headers=filter_response_headers(upstream.headers),
    )


def _acquire_key_slot(
    key_prefix: str, limit: int, stack: AsyncExitStack
) -> None:
    """Reserve a per-key in-flight relay slot or reject at capacity.

    asyncio is single-threaded, so the check-then-increment is atomic.
    The release is registered on ``stack`` so the slot is held until the
    forward (including any streamed body) finishes, bounding how many
    shared-pool connections one key can hold open. A ``limit`` <= 0
    disables the cap.

    Raises:
        HTTPException: 503 when the key already holds ``limit`` forwards.
    """
    if 0 < limit <= _INFLIGHT.get(key_prefix, 0):
        raise HTTPException(
            status_code=503, detail="relay concurrency limit exceeded"
        )
    _INFLIGHT[key_prefix] = _INFLIGHT.get(key_prefix, 0) + 1

    async def _release() -> None:
        remaining = _INFLIGHT.get(key_prefix, 0) - 1
        if remaining > 0:
            _INFLIGHT[key_prefix] = remaining
        else:
            _INFLIGHT.pop(key_prefix, None)

    stack.push_async_callback(_release)


async def _open_relay_upstream(
    *,
    request: Request,
    body: bytes,
    upstream: RelayUpstream,
    stack: AsyncExitStack,
    record: Callable[..., None],
) -> tuple[httpx.Response, list[str]]:
    """Mint the operator credential and open the upstream stream.

    Strips the caller credential, injects the operator one on the
    per-request call, and sends with the upstream registered for cleanup
    on ``stack``. A mint or transport failure fails closed with a 502 and
    is audited; on success the open response and the injected secret
    values (for non-2xx body scrubbing) are returned.

    Raises:
        HTTPException: 502 when the credential mint or the upstream
            connection fails.
    """
    config = ApiConfig()
    try:
        injected = await upstream.inject_headers()
    except Exception as exc:  # fail closed on any credential-mint failure
        await stack.aclose()
        record(
            status_code=None, response_body=None, error_type=type(exc).__name__
        )
        raise HTTPException(
            status_code=502, detail="relay upstream unavailable"
        ) from exc

    forward_headers = {**prepare_forward_headers(request.headers), **injected}
    # trust_env is passed only when False: any client kwarg opts out of the
    # shared keep-alive pool, so the default path stays pooled while a
    # bare-IP upstream gets an ephemeral, proxy-bypassing client.
    timeout = httpx.Timeout(
        config.RELAY_TIMEOUT_SECONDS, connect=config.RELAY_TIMEOUT_SECONDS
    )
    upstream_client = (
        get_async_client(timeout=timeout)
        if upstream.trust_env
        else get_async_client(timeout=timeout, trust_env=False)
    )
    client = await stack.enter_async_context(upstream_client)
    try:
        upstream_resp = await client.send(
            client.build_request(
                request.method,
                upstream.url,
                headers=forward_headers,
                content=body,
            ),
            stream=True,
        )
    except (httpx.HTTPError, OSError) as exc:
        await stack.aclose()
        record(
            status_code=None, response_body=None, error_type=type(exc).__name__
        )
        raise HTTPException(
            status_code=502, detail="relay upstream unavailable"
        ) from exc

    stack.push_async_callback(upstream_resp.aclose)
    return upstream_resp, list(injected.values())


async def forward_relay_request(
    *,
    request: Request,
    body: bytes,
    upstream: RelayUpstream,
    principal: ApiPrincipal,
    audit_store: RelayAuditStore,
) -> Response:
    """Forward a relayed request upstream with injected operator creds.

    Strips the caller credential, injects the operator one on the
    per-request call, reads the upstream status BEFORE building the
    streamed response (so an upstream error is never masked as a 200),
    and audits every call best-effort. A 2xx TRANSPARENT response is
    streamed through a tee; every other case is buffered and shaped per
    the route's error mode. A credential-mint or transport failure fails
    closed with a 502 (the unified envelope) and is audited.

    Args:
        request: The inbound caller request (method and headers used).
        body: The already-read, size-gated inbound request body.
        upstream: The resolved upstream target and error shaping.
        principal: The authenticated caller (audited by id + key prefix).
        audit_store: The relay audit store (written best-effort).

    Returns:
        The response to return to the caller.

    Raises:
        HTTPException: 502 on a mint/transport failure, or the upstream
            status for an ENVELOPE-mode upstream error.
    """
    started = time.monotonic()
    request_id = current_request_id() or ""
    config = ApiConfig()
    request_body = sanitize_audit_body(
        body, config.RELAY_REQUEST_AUDIT_MAX_BYTES
    )

    def record(
        *,
        status_code: int | None,
        response_body: str | None,
        error_type: str | None,
    ) -> None:
        entry = RelayAuditRecord(
            request_id=request_id,
            user_id=principal.user_id,
            key_prefix=principal.key_prefix,
            service=upstream.service,
            operation=upstream.operation,
            status_code=status_code,
            duration_ms=int((time.monotonic() - started) * 1000),
            request_body=request_body,
            response_body=response_body,
            error_type=error_type,
        )
        try:
            audit_store.record(entry)
        except (sqlite3.Error, OSError):
            _LOGGER.exception(
                "relay audit write failed for request %s", request_id
            )

    stack = AsyncExitStack()
    _acquire_key_slot(
        principal.key_prefix, config.RELAY_MAX_CONCURRENT_PER_KEY, stack
    )
    upstream_resp, secrets = await _open_relay_upstream(
        request=request,
        body=body,
        upstream=upstream,
        stack=stack,
        record=record,
    )
    cap = config.RELAY_RESPONSE_AUDIT_MAX_BYTES

    if upstream.error_mode is RelayErrorMode.TRANSPARENT and (
        200 <= upstream_resp.status_code < 300
    ):
        return _streaming_relay_response(
            stack,
            upstream_resp,
            cap=cap,
            record=record,
            deadline=config.RELAY_TIMEOUT_SECONDS,
        )

    try:
        return await _buffered_relay_response(
            upstream_resp,
            error_mode=upstream.error_mode,
            secrets=secrets,
            cap=cap,
            record=record,
        )
    finally:
        await stack.aclose()
