# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP mapping for conversation-context lifecycle routing faults."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException
from tests.support.http_fakes import build_instant_chat_context_envelope

from mcp_server_phytomni.agents.expert import (
    ExpertProviderError,
    ExpertProviderTimeoutError,
    ToolSelectionError,
)
from mcp_server_phytomni.api.lifecycle_contract import SafeApiError
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

_SENTINEL = (
    "ROUTER-PROMPT-SENTINEL RAW-MODEL-SENTINEL "
    "ALLOWLIST-SENTINEL PROVIDER-PAYLOAD-SENTINEL "
    "credential-like-sentinel"
)
_ERROR_LOGGER = "mcp_server_phytomni.api.expert_routing_errors"


@pytest.fixture(autouse=True)
def _capture_provider_logs(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    """Attach caplog when package logging has already disabled propagate."""
    target = logging.getLogger(_ERROR_LOGGER)
    target.addHandler(caplog.handler)
    try:
        yield
    finally:
        target.removeHandler(caplog.handler)


def _envelope() -> ConversationEnvelopeV1:
    """Build one envelope for the lifecycle HTTP wrapper."""
    return ConversationEnvelopeV1.model_validate(
        build_instant_chat_context_envelope("1")
    )


def _raising_request(exc: BaseException) -> ContextLifecycleHttpRequest:
    """Build one wrapper request whose executor raises ``exc``."""

    async def execute(**_kwargs: Any) -> Any:
        raise exc

    async def unused(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("lifecycle wrapper must not invoke transport")

    return ContextLifecycleHttpRequest(
        executor=cast(Any, SimpleNamespace(execute=execute)),
        envelope=_envelope(),
        invoke=unused,
        delegate_async=unused,
        selection_failure_detail="router did not resolve one permitted agent",
    )


@pytest.mark.asyncio
async def test_lifecycle_http_maps_review_lock_timeout_to_503() -> None:
    """A busy Review mutation lock is retryable, not an ASGI 500."""
    with pytest.raises(HTTPException) as caught:
        await execute_context_lifecycle_http(
            _raising_request(ReviewMutationLockTimeoutError())
        )
    assert caught.value.status_code == 503
    assert caught.value.detail == "Review mutation is busy"


@pytest.mark.asyncio
async def test_lifecycle_http_keeps_selection_faults_as_http_502() -> None:
    """Selector contract faults stay the existing HTTPException 502."""
    with pytest.raises(HTTPException) as caught:
        await execute_context_lifecycle_http(
            _raising_request(ToolSelectionError(_SENTINEL))
        )
    assert caught.value.status_code == 502
    assert caught.value.detail == (
        "router did not resolve one permitted agent"
    )
    assert _SENTINEL not in caught.value.detail


@pytest.mark.parametrize(
    ("exc", "status_code", "code"),
    (
        (
            ExpertProviderTimeoutError(_SENTINEL),
            504,
            "upstream_timeout",
        ),
        (
            ExpertProviderError(_SENTINEL),
            502,
            "routing_upstream_failed",
        ),
    ),
    ids=("timeout", "provider"),
)
@pytest.mark.asyncio
async def test_lifecycle_http_maps_provider_faults_to_safe_errors(
    caplog: pytest.LogCaptureFixture,
    exc: ExpertProviderError,
    status_code: int,
    code: str,
) -> None:
    """Provider timeout and failure match the V0 SafeApiError envelope."""
    caplog.set_level(
        logging.WARNING,
        logger="mcp_server_phytomni.api.expert_routing_errors",
    )
    with pytest.raises(SafeApiError) as caught:
        await execute_context_lifecycle_http(_raising_request(exc))
    error = caught.value
    assert error.status_code == status_code
    assert error.code == code
    assert error.stage == "routing"
    assert error.retryable is True
    assert _SENTINEL not in error.message
    records = [
        record
        for record in caplog.records
        if record.getMessage() == "Expert routing provider failed"
    ]
    assert len(records) == 1
    assert getattr(records[0], "error_class") == exc.__class__.__name__
    assert getattr(records[0], "stage") == "routing"
    assert _SENTINEL not in caplog.text
