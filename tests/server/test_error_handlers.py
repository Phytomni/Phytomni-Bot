# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP envelopes for MCP retrieval and traced unhandled failures."""

from __future__ import annotations

import json
from collections.abc import Awaitable
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData
from starlette.requests import Request

from mcp_server_phytomni.agents.knowledge.retrieval_result import (
    RETRIEVAL_UNAVAILABLE_MESSAGE,
)
from mcp_server_phytomni.api.app_support import _ErrorResponseOptions
from mcp_server_phytomni.api.error_handlers import register_error_handlers
from mcp_server_phytomni.runtime.stage_trace import (
    StageTraceEvent,
    bind_stage_trace,
)

pytestmark = pytest.mark.server


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
