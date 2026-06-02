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

from collections.abc import Iterable, Mapping

from .audit_filter import CREDENTIAL_HEADERS

__all__ = [
    "prepare_forward_headers",
    "filter_response_headers",
    "scrub_secrets",
]

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
