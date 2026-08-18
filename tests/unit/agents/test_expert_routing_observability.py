# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for Expert route outcome and Pangu-duration logs."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException
from tests.support.http_fakes import build_instant_chat_context_envelope

from mcp_server_phytomni.agents.expert import (
    ExpertProviderError,
    ExpertProviderTimeoutError,
    ExpertRoutingDeclinedError,
    ToolSelectionError,
)
from mcp_server_phytomni.agents.expert.routing_observability import (
    ExpertProviderAttemptResult,
    ExpertRouteOutcome,
    ExpertRoutePath,
    record_expert_provider_attempt,
    record_expert_route_outcome,
)
from mcp_server_phytomni.api.app import _route_expert_query
from mcp_server_phytomni.api.lifecycle_contract import SafeApiError
from mcp_server_phytomni.api.routes.context_types import (
    ContextLifecycleHttpRequest,
    execute_context_lifecycle_http,
)
from mcp_server_phytomni.api.schemas import ExpertQueryRequest
from mcp_server_phytomni.runtime.conversation_context.models import (
    ConversationEnvelopeV1,
)
from mcp_server_phytomni.runtime.request_context import request_context

pytestmark = pytest.mark.unit

_LEAK = "query-body-must-not-appear allowlist-must-not-appear"
_ROUTE_LOGGER = "mcp_server_phytomni.agents.expert.routing_observability"


def _route_records(
    caplog: pytest.LogCaptureFixture,
) -> list[logging.LogRecord]:
    """Return Expert route-outcome records from the observability logger."""
    return [
        record
        for record in caplog.records
        if record.name == _ROUTE_LOGGER
        and record.getMessage().startswith("Expert route outcome")
    ]


def _provider_records(
    caplog: pytest.LogCaptureFixture,
) -> list[logging.LogRecord]:
    """Return Pangu attempt records from the observability logger."""
    return [
        record
        for record in caplog.records
        if record.name == _ROUTE_LOGGER
        and record.getMessage().startswith("Expert routing provider completed")
    ]


def test_route_outcome_log_keeps_bounded_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Outcome extras stay in the closed taxonomy and omit the query."""
    caplog.set_level(logging.INFO, logger=_ROUTE_LOGGER)
    with request_context("user-1", "request-route-1"):
        record_expert_route_outcome(
            ExpertRouteOutcome.SELECTION_CONTRACT,
            path=ExpertRoutePath.CONTEXT,
            forced=False,
            error_class=_LEAK,
            http_status=502,
        )
    records = _route_records(caplog)
    assert len(records) == 1
    record = records[0]
    assert getattr(record, "event") == "expert_route"
    assert getattr(record, "outcome") == "selection_contract"
    assert getattr(record, "path") == "context"
    assert getattr(record, "forced") is False
    assert getattr(record, "stage") == "routing"
    assert getattr(record, "error_class") == "Exception"
    assert getattr(record, "http_status") == 502
    assert getattr(record, "request_id") == "request-route-1"
    assert "outcome=selection_contract" in record.getMessage()
    assert "request_id=request-route-1" in record.getMessage()
    assert _LEAK not in record.getMessage()
    assert _LEAK not in caplog.text


def test_provider_attempt_log_records_duration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Successful Pangu hops expose a non-negative duration_ms."""
    caplog.set_level(logging.INFO, logger=_ROUTE_LOGGER)
    record_expert_provider_attempt(
        ExpertProviderAttemptResult.OK,
        duration_ms=12,
        attempt=0,
    )
    records = _provider_records(caplog)
    assert len(records) == 1
    record = records[0]
    assert getattr(record, "event") == "expert_provider"
    assert getattr(record, "result") == "ok"
    assert getattr(record, "duration_ms") == 12
    assert getattr(record, "attempt") == 0
    assert getattr(record, "retry_count") == 0
    assert "duration_ms=12" in record.getMessage()


@pytest.mark.asyncio
async def test_v0_logs_declined_no_fallback(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V0 decline without Chat stays 502 and records declined_no_fallback."""

    async def declining(*_args: object, **_kwargs: object) -> None:
        raise ExpertRoutingDeclinedError(_LEAK)

    monkeypatch.setattr(
        "mcp_server_phytomni.api.app.select_agent_tool",
        declining,
    )
    caplog.set_level(logging.WARNING, logger=_ROUTE_LOGGER)
    payload = ExpertQueryRequest(
        user_query=_LEAK,
        allowed_tools=["KnowledgeAgent", "DataAgent"],
    )
    with pytest.raises(SafeApiError) as caught:
        await _route_expert_query(payload, debug=False)
    assert caught.value.status_code == 502
    assert caught.value.code == "routing_contract_violation"
    records = _route_records(caplog)
    assert len(records) == 1
    assert getattr(records[0], "outcome") == "declined_no_fallback"
    assert getattr(records[0], "path") == "v0"
    assert getattr(records[0], "forced") is False
    assert getattr(records[0], "http_status") == 502
    assert _LEAK not in records[0].getMessage()
    assert _LEAK not in caplog.text


@pytest.mark.asyncio
async def test_v0_logs_declined_chat_fallback(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unforced V0 decline records declined_chat_fallback, not selected."""

    async def declining(*_args: object, **_kwargs: object) -> None:
        raise ExpertRoutingDeclinedError(_LEAK)

    async def fake_invoke(**_kwargs: object) -> tuple[dict[str, str], int]:
        return {"status": "succeeded"}, 200

    monkeypatch.setattr(
        "mcp_server_phytomni.api.app.select_agent_tool",
        declining,
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.api.app.prepare_selected_expert_arguments",
        lambda **_kwargs: (
            {"user_query": "what is photosynthesis"},
            SimpleNamespace(evidence=None),
        ),
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.api.app._invoke_agent_run",
        fake_invoke,
    )
    caplog.set_level(logging.WARNING, logger=_ROUTE_LOGGER)
    payload = ExpertQueryRequest(
        user_query=_LEAK,
        allowed_tools=["ChatAgent", "DataAgent"],
    )
    body, status_code = await _route_expert_query(payload, debug=False)
    assert status_code == 200
    assert body["status"] == "succeeded"
    records = _route_records(caplog)
    assert len(records) == 1
    assert getattr(records[0], "outcome") == "declined_chat_fallback"
    assert getattr(records[0], "path") == "v0"
    assert getattr(records[0], "http_status") == 200
    assert _LEAK not in caplog.text


@pytest.mark.asyncio
async def test_v0_logs_selection_contract(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V0 allowlist faults stay 502 and record selection_contract."""

    async def invalid(*_args: object, **_kwargs: object) -> None:
        raise ToolSelectionError(_LEAK)

    monkeypatch.setattr(
        "mcp_server_phytomni.api.app.select_agent_tool",
        invalid,
    )
    caplog.set_level(logging.WARNING, logger=_ROUTE_LOGGER)
    payload = ExpertQueryRequest(
        user_query=_LEAK,
        allowed_tools=["ChatAgent", "KnowledgeAgent"],
    )
    with pytest.raises(SafeApiError) as caught:
        await _route_expert_query(payload, debug=False)
    assert caught.value.status_code == 502
    records = _route_records(caplog)
    assert len(records) == 1
    assert getattr(records[0], "outcome") == "selection_contract"
    assert getattr(records[0], "error_class") == "ToolSelectionError"
    assert _LEAK not in caplog.text


@pytest.mark.parametrize(
    ("exc", "outcome", "status_code"),
    (
        (
            ExpertProviderTimeoutError(_LEAK),
            "provider_timeout",
            504,
        ),
        (ExpertProviderError(_LEAK), "provider_error", 502),
    ),
    ids=("timeout", "provider"),
)
@pytest.mark.asyncio
async def test_v0_logs_provider_outcome(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    exc: ExpertProviderError,
    outcome: str,
    status_code: int,
) -> None:
    """V0 provider faults keep SafeApiError codes and record the class."""

    async def failing(*_args: object, **_kwargs: object) -> None:
        raise exc

    monkeypatch.setattr(
        "mcp_server_phytomni.api.app.select_agent_tool",
        failing,
    )
    caplog.set_level(logging.WARNING, logger=_ROUTE_LOGGER)
    payload = ExpertQueryRequest(
        user_query=_LEAK,
        allowed_tools=["ChatAgent"],
    )
    with pytest.raises(SafeApiError) as caught:
        await _route_expert_query(payload, debug=False)
    assert caught.value.status_code == status_code
    records = _route_records(caplog)
    assert len(records) == 1
    assert getattr(records[0], "outcome") == outcome
    assert getattr(records[0], "http_status") == status_code
    assert _LEAK not in caplog.text


def _context_fault_request(exc: BaseException) -> ContextLifecycleHttpRequest:
    """Build a lifecycle request whose executor raises ``exc``."""

    async def explode(**_kwargs: object) -> None:
        raise exc

    async def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("context wrapper must not call transport")

    return ContextLifecycleHttpRequest(
        executor=cast(Any, SimpleNamespace(execute=explode)),
        envelope=ConversationEnvelopeV1.model_validate(
            build_instant_chat_context_envelope("1")
        ),
        invoke=blocked,
        delegate_async=blocked,
        selection_failure_detail="router did not resolve one permitted agent",
    )


@pytest.mark.asyncio
async def test_context_wrapper_logs_declined_no_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Context decline without Chat stays HTTP 502 with that outcome."""
    caplog.set_level(logging.WARNING, logger=_ROUTE_LOGGER)
    with pytest.raises(HTTPException) as caught:
        await execute_context_lifecycle_http(
            _context_fault_request(ExpertRoutingDeclinedError(_LEAK))
        )
    assert caught.value.status_code == 502
    logged = _route_records(caplog)
    assert [getattr(item, "outcome") for item in logged] == [
        "declined_no_fallback"
    ]
    assert getattr(logged[0], "path") == "context"
    assert _LEAK not in caplog.text


@pytest.mark.asyncio
async def test_context_wrapper_logs_selection_contract(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Context allowlist faults stay HTTP 502 with selection_contract."""
    caplog.set_level(logging.WARNING, logger=_ROUTE_LOGGER)
    with pytest.raises(HTTPException) as caught:
        await execute_context_lifecycle_http(
            _context_fault_request(ToolSelectionError(_LEAK))
        )
    assert caught.value.status_code == 502
    logged = _route_records(caplog)
    assert [getattr(item, "outcome") for item in logged] == [
        "selection_contract"
    ]
    assert getattr(logged[0], "error_class") == "ToolSelectionError"
    assert _LEAK not in caplog.text
