# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Natural-language SQL request helpers.

This module exposes `Nl2SqlRequest`, `nl2sql`, and
`execute_nl2sql_request` for preparing authenticated database API calls.
"""

from dataclasses import dataclass
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
from ...config.defaults import DataConfig
from ...storage.path_policy import IdFactory

DATA_CONFIG = DataConfig()


def _default_dialog_id() -> str:
    """Return a generated dialog ID for caller-omitted NL2SQL sessions."""
    return IdFactory().new_id("dialog")


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


async def execute_nl2sql_request(request: Nl2SqlRequest) -> Any:
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
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
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
    # Reached only if max_retries is negative (empty attempt range);
    # never silently return None into the NL2SQL caller.
    raise McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message="Failed to query SQL database: no attempts executed",
        )
    )
