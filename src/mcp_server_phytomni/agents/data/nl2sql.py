# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Natural-language SQL request helpers."""

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
    """Resolved request settings for one NL2SQL call."""

    database_url: str
    workspace_id: str
    payload_data: Dict[str, Any]
    timeout: float
    retriable_codes: tuple[int, ...]
    max_retries: int

    @classmethod
    def from_kwargs(cls, message_content: str, values: Dict[str, Any]):
        """Build request settings from keyword-compatible overrides."""
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
        """Return the database API JSON payload."""
        return dict(self.payload_data)


async def nl2sql(
    message_content: str,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Convert a natural language query to SQL and execute it."""
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


async def execute_nl2sql_request(request: Nl2SqlRequest) -> Any:
    """Execute one resolved NL2SQL request and return the raw response."""
    client_timeout = Timeout(request.timeout, connect=request.timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        return await post_json_with_retries(
            client,
            JsonPostRequest(
                url=request.database_url,
                headers={
                    "X-Auth-Token": await get_token(),
                    "X-Workspace-Id": request.workspace_id,
                    "Content-Type": "application/json",
                },
                json_body=request.payload(),
            ),
            JsonPostRetry(
                timeout=request.timeout,
                max_retries=request.max_retries,
                retriable_codes=request.retriable_codes,
                message="Failed to query SQL database",
            ),
        )
