# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP mapping for conversation-context lifecycle lock timeouts."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import HTTPException

from mcp_server_phytomni.api.routes.context_types import (
    ContextLifecycleHttpRequest,
    execute_context_lifecycle_http,
)
from mcp_server_phytomni.runtime.conversation_context.models import (
    ConversationEnvelopeV1,
)
from mcp_server_phytomni.runtime.conversation_context.store import (
    ReviewMutationLockTimeoutError,
)

pytestmark = pytest.mark.unit


def _envelope() -> ConversationEnvelopeV1:
    """Build one Expert envelope for the lifecycle HTTP wrapper."""
    return ConversationEnvelopeV1.model_validate(
        {
            "schema_version": 1,
            "conversation_key": str(
                UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7")
            ),
            "dialogue_id": str(UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad8")),
            "turn_id": "1",
            "request_id": "request-1",
            "operation": "append",
            "mode": "expert",
            "current_message": {"content": "review rice", "locale": "en-US"},
            "requested_agent_id": "ReviewAgent",
            "allowed_agent_ids": ["ReviewAgent"],
            "ledger_cursor": 1,
            "ledger_version": "a" * 64,
            "base_business_context_version": 0,
            "history_delta": [],
            "artifact_refs": [],
        }
    )


class _BusyExecutor:
    """Executor that reproduces an uncaught Review lock timeout."""

    async def execute(self, **_kwargs: Any) -> Any:
        """Raise the same timeout /v1/query/route currently surfaces as 500."""
        raise ReviewMutationLockTimeoutError()


@pytest.mark.asyncio
async def test_lifecycle_http_maps_review_lock_timeout_to_503() -> None:
    """A busy Review mutation lock is retryable, not an ASGI 500."""

    async def _unused(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("lifecycle wrapper must not invoke transport")

    request = ContextLifecycleHttpRequest(
        executor=cast(Any, _BusyExecutor()),
        envelope=_envelope(),
        invoke=_unused,
        delegate_async=_unused,
        selection_failure_detail="router did not resolve one permitted agent",
    )
    with pytest.raises(HTTPException) as caught:
        await execute_context_lifecycle_http(request)
    assert caught.value.status_code == 503
    assert caught.value.detail == "Review mutation is busy"
