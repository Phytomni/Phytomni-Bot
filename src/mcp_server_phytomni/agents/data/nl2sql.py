# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Natural-language SQL request helpers.

This module exposes `Nl2SqlRequest`, `nl2sql`, and
`execute_nl2sql_request` for preparing authenticated database API calls.
"""

import asyncio
from dataclasses import dataclass
from random import uniform
from typing import Any, Dict

from httpx import AsyncClient, Timeout
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from ...auth.iam import get_token
from ...common.http import (
    JsonPostRequest,
    JsonPostRetry,
    post_json_with_retries,
)
from ...common.httpx_client import get_async_client
from ...config.defaults import DataConfig
from ...func_cache import LONG_TTL_SECONDS, func_cache
from ...storage.path_policy import IdFactory

DATA_CONFIG = DataConfig()

# Small, capped backoff between rotated conversations. The actual fix
# for a poisoned conversation cache is the fresh dialog_id, not
# waiting -- this only keeps rotations from instantly hammering the
# gateway when a 504 was in fact brief gateway saturation rather than
# cache confusion. Kept small on purpose so a 5-rotation worst case
# adds seconds, not minutes.
_ROTATION_BACKOFF_BASE_SECONDS = 0.5
_ROTATION_BACKOFF_CAP_SECONDS = 4.0
_ROTATION_BACKOFF_JITTER_SECONDS = 0.25


def _default_dialog_id() -> str:
    """Return a generated dialog ID for caller-omitted NL2SQL sessions."""
    return IdFactory().new_id("dialog")


async def _rotation_backoff(attempt: int) -> None:
    """Pause briefly before retrying under a fresh conversation.

    Args:
        attempt: Zero-based index of the attempt that just failed
            (the pause grows with it, capped low).
    """
    delay = min(
        _ROTATION_BACKOFF_BASE_SECONDS * (2**attempt),
        _ROTATION_BACKOFF_CAP_SECONDS,
    )
    await asyncio.sleep(delay + uniform(0, _ROTATION_BACKOFF_JITTER_SECONDS))


@dataclass(frozen=True)
class Nl2SqlRequest:
    """Resolved request settings for one NL2SQL call.

    Attributes:
        database_url: DataArts NLQ endpoint URL.
        workspace_id: Workspace ID sent in the request headers.
        payload_data: JSON payload body for the database API.
        timeout: Request timeout in seconds.
        retriable_codes: HTTP status codes that trigger retries.
        max_retries: Maximum retry attempts for the request.
    """

    database_url: str
    workspace_id: str
    payload_data: Dict[str, Any]
    timeout: float
    retriable_codes: tuple[int, ...]
    max_retries: int

    @classmethod
    def from_kwargs(cls, message_content: str, values: Dict[str, Any]):
        """Build request settings from keyword-compatible overrides.

        Args:
            message_content: Natural-language query sent to the NL2SQL API.
            values: Keyword-compatible overrides for request settings.

        Returns:
            Resolved NL2SQL request settings.
        """
        retriable_codes = values.get("retriable_codes")
        if retriable_codes is None:
            retriable_codes = DATA_CONFIG.RETRIABLE_CODES
        return cls(
            database_url=values.get("database_url", DATA_CONFIG.DATABASE_URL),
            workspace_id=values.get("workspace_id", DATA_CONFIG.WORKSPACE_ID),
            payload_data={
                "subject_id": values.get("subject_id", DATA_CONFIG.SUBJECT_ID),
                "dialog_id": values.get("dialog_id") or _default_dialog_id(),
                "message_content": message_content,
                "need_insight": values.get(
                    "need_insight", DATA_CONFIG.NEED_INSIGHT
                ),
                "simplify_response": values.get(
                    "simplify_response",
                    DATA_CONFIG.SIMPLIFY_RESPONSE,
                ),
            },
            timeout=values.get("timeout", DATA_CONFIG.TIMEOUT),
            retriable_codes=tuple(retriable_codes),
            max_retries=values.get("max_retries", DATA_CONFIG.MAX_RETRIES),
        )

    def payload(self) -> Dict[str, Any]:
        """Return the database API JSON payload.

        Returns:
            Copy of the request payload body sent to the NL2SQL API.
        """
        return dict(self.payload_data)

    def payload_with_fresh_dialog(self) -> Dict[str, Any]:
        """Return the payload under a newly minted conversation id.

        Each call rebinds ``dialog_id`` to a fresh
        ``_default_dialog_id()`` so a retry starts a clean
        server-side conversation, bypassing a poisoned/confused
        conversation-cache slot left by a previous failed attempt.

        Returns:
            Copy of the request payload with a brand-new ``dialog_id``.
        """
        body = dict(self.payload_data)
        body["dialog_id"] = _default_dialog_id()
        return body


async def nl2sql(
    message_content: str,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Convert a natural language query to SQL and execute it.

    Args:
        message_content: Natural-language database question.
        **kwargs: Keyword-compatible request and retry overrides.

    Returns:
        Raw dictionary response from the NL2SQL service.

    Raises:
        McpError: If the request does not produce a dictionary response.
    """
    request = Nl2SqlRequest.from_kwargs(message_content, kwargs)
    result = await execute_nl2sql_request(request)
    if isinstance(result, dict):
        return result

    raise McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message="Failed to query SQL database after all retries",
        )
    )


async def _post_one_conversation(
    client: AsyncClient,
    request: Nl2SqlRequest,
    body: Dict[str, Any],
    token: str,
) -> Any:
    """POST one NL2SQL body as a single server-side conversation.

    Delegates the actual send/backoff/status classification to the
    shared retry helper with ``max_retries=0`` so *any* failure
    (retriable 5xx, non-retriable status, or transport error) raises
    ``McpError`` instead of silently returning ``None``. That makes
    every failed conversation observable to the outer rotation loop.

    Args:
        client: Open async HTTP client.
        request: Resolved NL2SQL request settings.
        body: Fully built JSON payload (its ``dialog_id`` identifies
            the server-side conversation for this single attempt).
        token: Pre-fetched IAM auth token (fetched once by the caller
            so a token round-trip is not repeated per rotation).

    Returns:
        Parsed JSON response for this conversation.

    Raises:
        McpError: On any HTTP/transport failure for this conversation.
    """
    return await post_json_with_retries(
        client,
        JsonPostRequest(
            url=request.database_url,
            headers={
                "X-Auth-Token": token,
                "X-Workspace-Id": request.workspace_id,
                "Content-Type": "application/json",
            },
            json_body=body,
        ),
        JsonPostRetry(
            timeout=request.timeout,
            max_retries=0,
            retriable_codes=request.retriable_codes,
            message="Failed to query SQL database",
        ),
    )


async def _execute_nl2sql_uncached(request: Nl2SqlRequest) -> Any:
    """Execute a resolved NL2SQL request, rotating the conversation.

    The Huawei DataArts NL-query gateway keys conversation state on the
    ``dialog_id`` we send. A single attempt can poison its server-side
    conversation cache (half-registered turn that then times out as
    504); retrying the *same* ``dialog_id`` just re-hits the poisoned
    slot. So each retry here is a *fresh conversation*
    (``payload_with_fresh_dialog()``), per the backend engineers'
    guidance to bypass a confused conversation cache with a new id.

    Args:
        request: Resolved NL2SQL request settings.

    Returns:
        Raw JSON response from the first conversation that succeeds.

    Raises:
        McpError: Re-raised from the final attempt once all rotated
            conversations have been exhausted.
    """
    client_timeout = Timeout(request.timeout, connect=request.timeout)
    token = await get_token()
    async with get_async_client(timeout=client_timeout) as client:
        last_attempt = request.max_retries
        for attempt in range(last_attempt + 1):
            # Attempt 0 keeps the caller's conversation (honors an
            # explicit dialog_id); every retry is a brand-new
            # conversation so a poisoned server-side cache slot left
            # by the failed attempt is bypassed.
            body = (
                request.payload()
                if attempt == 0
                else request.payload_with_fresh_dialog()
            )
            try:
                return await _post_one_conversation(
                    client, request, body, token
                )
            except McpError:
                # Rotate to a fresh conversation on the next attempt;
                # on the final attempt surface the real error so the
                # caller never sees a silent None.
                if attempt == last_attempt:
                    raise
                await _rotation_backoff(attempt)
    # Reached only if max_retries is negative (empty attempt range);
    # never silently return None into the NL2SQL caller.
    raise McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message="Failed to query SQL database: no attempts executed",
        )
    )


@func_cache(
    key_params=[
        "message_content",
        "subject_id",
        "workspace_id",
        "database_url",
        "need_insight",
        "simplify_response",
    ],
    ttl=LONG_TTL_SECONDS,
)
async def _execute_nl2sql_cached(
    message_content: str,
    subject_id: str,
    workspace_id: str,
    database_url: str,
    need_insight: bool,
    simplify_response: bool,
    *,
    request: Nl2SqlRequest,
) -> Any:
    """Cache NL2SQL answers on the deterministic semantic fields only.

    ``dialog_id`` is fresh per call (a server-side conversation slot,
    rotated again inside ``_execute_nl2sql_uncached`` on retry) and
    ``token`` is a volatile IAM credential; both are intentionally
    excluded from ``key_params`` so identical natural-language
    questions over the same workspace / subject / database / insight
    flags hit one cached BI answer regardless of which conversation
    or token attempted it. Failures propagate uncached (the inner
    raises McpError on retry exhaustion), so a transient gateway
    glitch never poisons the 90-day store.
    """
    del message_content, subject_id, workspace_id
    del database_url, need_insight, simplify_response
    return await _execute_nl2sql_uncached(request)


async def execute_nl2sql_request(request: Nl2SqlRequest) -> Any:
    """Execute a resolved NL2SQL request with semantic-input caching.

    Thin adapter that pulls the deterministic semantic scalars off the
    request (the rewritten ``message_content`` plus the workspace,
    subject, database, and insight knobs) and delegates to the cached
    inner; the request itself is forwarded keyword-only so the cache
    miss path retains the rotating-conversation retry behavior the
    Huawei DataArts NL-query gateway requires.

    Args:
        request: Resolved NL2SQL request settings.

    Returns:
        Raw JSON response from the cached or freshly-executed call.
    """
    payload = request.payload_data
    return await _execute_nl2sql_cached(
        message_content=payload["message_content"],
        subject_id=payload["subject_id"],
        workspace_id=request.workspace_id,
        database_url=request.database_url,
        need_insight=payload["need_insight"],
        simplify_response=payload["simplify_response"],
        request=request,
    )


def clear_nl2sql_cache() -> None:
    """Drop the cached NL2SQL answers in the local SQLite store.

    Public seam over the private ``_execute_nl2sql_cached`` cache so
    test fixtures and admin tooling don't need to reach into a
    protected attribute. Calling this before / between tests is the
    only safe way to assert the rotation-and-retry behavior the
    uncached helper provides, because the cached store persists
    across test runs at LONG_TTL_SECONDS.
    """
    _execute_nl2sql_cached.cache_clear()
