# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP envelopes for MCP retrieval and traced unhandled failures."""

from __future__ import annotations

import json
from collections.abc import Awaitable
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData
from openai import APIConnectionError, APITimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from tests.support.http_fakes import open_asgi_client

from mcp_server_phytomni.agents.chat.completion_validation import (
    InvalidChatCompletionError,
)
from mcp_server_phytomni.agents.knowledge.retrieval_result import (
    RETRIEVAL_UNAVAILABLE_MESSAGE,
)
from mcp_server_phytomni.api import agent_runs
from mcp_server_phytomni.api.agent_runs import _invoke_prepared_agent_run
from mcp_server_phytomni.api.app_support import (
    _ErrorResponseOptions,
    error_response,
    request_context_middleware,
)
from mcp_server_phytomni.api.error_handlers import register_error_handlers
from mcp_server_phytomni.api.lifecycle_contract import (
    LifecycleInvariantError,
    SafeApiError,
    SafeErrorCode,
)
from mcp_server_phytomni.api.stage_errors import project_data_stage_error
from mcp_server_phytomni.common.http import retry_network_or_raise
from mcp_server_phytomni.runtime.stage_trace import (
    DataStage,
    StageTraceEvent,
    bind_stage_trace,
    trace_data_stage,
)

pytestmark = pytest.mark.server

_TRANSPORT_SECRET = "https://private-upstream.invalid/private?token=hidden"


@pytest.mark.parametrize(
    ("exc", "status", "code", "message"),
    [
        *[
            pytest.param(
                error_type(_TRANSPORT_SECRET),
                502,
                "upstream_failed",
                "upstream service failed",
                id=error_type.__name__,
            )
            for error_type in (
                httpx.ConnectError,
                httpx.ReadError,
                httpx.WriteError,
                httpx.CloseError,
                httpx.RemoteProtocolError,
                httpx.ProxyError,
            )
        ],
        *[
            pytest.param(
                error_type(_TRANSPORT_SECRET),
                504,
                "upstream_timeout",
                "upstream service timed out",
                id=error_type.__name__,
            )
            for error_type in (
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.PoolTimeout,
            )
        ],
        pytest.param(
            APIConnectionError(
                message=_TRANSPORT_SECRET,
                request=httpx.Request("POST", _TRANSPORT_SECRET),
            ),
            502,
            "upstream_failed",
            "upstream service failed",
            id="APIConnectionError",
        ),
        pytest.param(
            APITimeoutError(httpx.Request("POST", _TRANSPORT_SECRET)),
            504,
            "upstream_timeout",
            "upstream service timed out",
            id="APITimeoutError",
        ),
    ],
)
async def test_exhausted_transport_has_safe_http_classification(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
    status: int,
    code: str,
    message: str,
) -> None:
    """Direct transport types survive MCP wrapping without leaking details."""
    exc.__cause__ = (
        httpx.ReadTimeout(_TRANSPORT_SECRET)
        if status == 502
        else httpx.ConnectError(_TRANSPORT_SECRET)
    )
    app = FastAPI()
    register_error_handlers(app, lambda name: error_response)

    @app.get("/failure")
    async def fail() -> None:
        await retry_network_or_raise(exc, attempt=0, max_retries=0)

    async with open_asgi_client(
        monkeypatch,
        request_context_middleware(app),
        base_url="http://test",
    ) as client:
        response = await client.get("/failure")

    assert response.status_code == status
    request_id = response.headers["X-Request-Id"]
    assert request_id and request_id != "unknown"
    assert response.json() == {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
            "retryable": True,
        }
    }
    assert _TRANSPORT_SECRET not in response.text
    assert "private-upstream" not in str(response.headers)


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("Network error: timed out"),
        InvalidChatCompletionError("missing_choices"),
        httpx.LocalProtocolError("Network error"),
        httpx.UnsupportedProtocol("timeout"),
        McpError(ErrorData(code=INTERNAL_ERROR, message="Network error")),
    ],
    ids=lambda exc: type(exc).__name__,
)
async def test_untyped_failures_with_transport_causes_stay_internal(
    exc: Exception,
) -> None:
    """Messages and nested causes cannot promote unknown failures to 502."""
    exc.__cause__ = httpx.ConnectError(_TRANSPORT_SECRET)
    with pytest.raises(McpError) as raised:
        await retry_network_or_raise(exc, attempt=0, max_retries=0)
    assert raised.value.__class__ is McpError
    assert raised.value.__cause__ is exc
    app = FastAPI()
    register_error_handlers(app, lambda name: error_response)
    for unknown in (raised.value, exc):
        handler = app.exception_handlers[
            McpError if isinstance(unknown, McpError) else Exception
        ]
        response = await cast(Awaitable[Any], handler(_request(), unknown))
        assert response.status_code == 500
        assert json.loads(response.body) == {
            "error": {
                "code": "internal_invariant_failed",
                "message": "internal server error",
                "request_id": "unknown",
                "retryable": False,
            }
        }


@pytest.mark.parametrize(
    "exc", [httpx.ConnectError("x"), httpx.ReadTimeout("x")]
)
async def test_data_stage_projection_precedes_transport_http_mapping(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
) -> None:
    """Native Data's existing classified stage wins over generic transport."""

    async def fail_tool(_name: str, _arguments: Any) -> None:
        async with trace_data_stage(DataStage.DATA_REWRITE, dependency="llm"):
            await retry_network_or_raise(exc, attempt=0, max_retries=0)

    monkeypatch.setattr(
        agent_runs,
        "_app_module",
        lambda: SimpleNamespace(
            invoke_tool_enveloped=fail_tool,
            _factory=SimpleNamespace(
                project_data_stage_error=project_data_stage_error
            ),
        ),
    )
    with pytest.raises(SafeApiError) as raised:
        await _invoke_prepared_agent_run(
            {"agent": "data", "arguments": {}},
            cast(Any, SimpleNamespace(tool_name="DataAgent")),
        )
    assert isinstance(raised.value.__cause__, McpError)
    assert raised.value.__cause__.__class__ is not McpError
    app = FastAPI()
    register_error_handlers(app, lambda name: error_response)
    response = await cast(
        Awaitable[Any],
        app.exception_handlers[SafeApiError](_request(), raised.value),
    )
    assert response.status_code == 500
    assert json.loads(response.body) == {
        "error": {
            "code": "stage_failed",
            "message": "internal server error",
            "request_id": "unknown",
            "stage": "data_rewrite",
            "retryable": False,
        }
    }


def _error_response(
    status_code: int,
    message: str,
    options: _ErrorResponseOptions | None = None,
) -> JSONResponse:
    """Render one bounded error envelope for handler tests."""
    payload: dict[str, Any] = {"message": message}
    if options is not None:
        payload["code"] = options.code
        payload["stage"] = options.stage
        payload["retryable"] = options.retryable
    return JSONResponse(payload, status_code=status_code)


def _app() -> FastAPI:
    """Build a tiny app that only exercises the shared handlers."""
    app = FastAPI()
    register_error_handlers(app, lambda name: _error_response)
    return app


def _request() -> Request:
    """Build a minimal HTTP request for handler invocation."""
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [],
            "client": ("test", 0),
            "server": ("test", 80),
        }
    )


async def test_mcp_retrieval_and_traced_failures_keep_safe_envelopes() -> None:
    """Retrieval unavailability and traced 502s stay public-safe."""
    app = _app()
    request = _request()
    mcp_handler = app.exception_handlers[McpError]
    unhandled = app.exception_handlers[Exception]
    retrieval = await cast(
        Awaitable[Any],
        mcp_handler(
            request,
            McpError(
                ErrorData(
                    code=INTERNAL_ERROR, message=RETRIEVAL_UNAVAILABLE_MESSAGE
                )
            ),
        ),
    )
    other = await cast(
        Awaitable[Any],
        mcp_handler(
            request, McpError(ErrorData(code=INTERNAL_ERROR, message="other"))
        ),
    )
    bind_stage_trace(
        (
            StageTraceEvent(
                request_id="req",
                agent="data",
                stage="routing",
                dependency="pangu",
                duration_ms=1,
                error_code="upstream_failed",
                error_class="RuntimeError",
                final_http_status=502,
            ),
        )
    )
    traced = await cast(
        Awaitable[Any], unhandled(request, RuntimeError("hidden"))
    )
    assert retrieval.status_code == 500
    assert json.loads(retrieval.body)["code"] == (
        "knowledge_retrieval_unavailable"
    )
    assert other.status_code == 500
    assert json.loads(other.body)["message"] == "internal server error"
    assert traced.status_code == 502
    assert json.loads(traced.body)["retryable"] is True
    bind_stage_trace(())
    untraced = await cast(
        Awaitable[Any], unhandled(request, RuntimeError("hidden"))
    )
    assert untraced.status_code == 500
    assert json.loads(untraced.body)["message"] == "internal server error"


async def test_http_safe_validation_and_lifecycle_envelopes() -> None:
    """HTTP, SafeApi, validation, and lifecycle handlers stay public-safe."""
    app = _app()
    request = _request()
    http_body = await cast(
        Awaitable[Any],
        app.exception_handlers[StarletteHTTPException](
            request, StarletteHTTPException(status_code=404)
        ),
    )
    assert http_body.status_code == 404
    safe_body = await cast(
        Awaitable[Any],
        app.exception_handlers[SafeApiError](
            request,
            SafeApiError(
                status_code=409,
                code="conflict",
                message="busy",
                stage="resume",
            ),
        ),
    )
    assert json.loads(safe_body.body)["code"] == "conflict"
    val_handler = app.exception_handlers[RequestValidationError]
    locale_body = await cast(
        Awaitable[Any],
        val_handler(
            request,
            RequestValidationError(
                [
                    {
                        "loc": ("body", "locale"),
                        "msg": "x",
                        "type": "value_error",
                    }
                ]
            ),
        ),
    )
    assert json.loads(locale_body.body)["code"] == "unsupported_locale"
    purpose_body = await cast(
        Awaitable[Any],
        val_handler(
            request,
            RequestValidationError(
                [
                    {
                        "loc": ("body", "purpose"),
                        "msg": "x",
                        "type": "literal_error",
                    }
                ]
            ),
        ),
    )
    assert json.loads(purpose_body.body)["code"] == (
        "attachment_purpose_invalid"
    )
    generic_body = await cast(
        Awaitable[Any],
        val_handler(
            request,
            RequestValidationError(
                [{"loc": ("body", "query"), "msg": "x", "type": "missing"}]
            ),
        ),
    )
    assert json.loads(generic_body.body)["message"] == (
        "request validation failed"
    )
    life_body = await cast(
        Awaitable[Any],
        app.exception_handlers[LifecycleInvariantError](
            request, LifecycleInvariantError(SafeErrorCode.PROJECTION_FAILED)
        ),
    )
    assert life_body.status_code == 500
