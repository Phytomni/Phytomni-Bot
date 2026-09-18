# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unified exception handlers for the FastAPI application."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..agents.knowledge.retrieval_result import RETRIEVAL_UNAVAILABLE_MESSAGE
from ..common.http import UpstreamConnectionError, UpstreamTimeoutError
from ..runtime.locale import message_for
from ..runtime.stage_trace import current_stage_trace
from .app_support import _SAFE_DEFAULT_MESSAGES, _ErrorResponseOptions
from .lifecycle_contract import LifecycleInvariantError, SafeApiError
from .stage_errors import safe_api_error_for_lifecycle as _safe_error
from .upload_runtime import register_upload_error_handler

__all__ = ["register_error_handlers"]


def _is_invalid_upload_purpose_error(error: Mapping[str, Any]) -> bool:
    """Match only the upload request's Pydantic purpose literal failure."""
    location = error.get("loc", ())
    return bool(
        isinstance(location, (list, tuple))
        and tuple(location) == ("body", "purpose")
        and error.get("type") in {"missing", "literal_error"}
    )


def register_error_handlers(
    app: FastAPI,
    app_attr: Callable[[str], Any],
) -> None:
    """Install the unified HTTP error envelope handlers."""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        _request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        """Render HTTP exceptions through the unified envelope."""
        return app_attr("_error_response")(
            exc.status_code,
            _SAFE_DEFAULT_MESSAGES.get(exc.status_code, "request failed"),
            options=_ErrorResponseOptions(
                headers=getattr(exc, "headers", None)
            ),
        )

    @app.exception_handler(SafeApiError)
    async def safe_api_error_handler(
        _request: Request,
        exc: SafeApiError,
    ) -> JSONResponse:
        """Render typed public-safe API errors through the unified envelope."""
        return app_attr("_error_response")(
            exc.status_code,
            exc.message,
            options=_ErrorResponseOptions(
                code=exc.code,
                stage=exc.stage,
                retryable=exc.retryable,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """Render request validation errors as 422 envelopes."""
        if any(
            "locale" in {str(part) for part in error.get("loc", ())}
            for error in exc.errors()
        ):
            return app_attr("_error_response")(
                422,
                message_for("unsupported_locale", "en-US"),
                options=_ErrorResponseOptions(
                    code="unsupported_locale",
                    stage="request_validation",
                    retryable=False,
                ),
            )
        if any(
            _is_invalid_upload_purpose_error(error) for error in exc.errors()
        ):
            return app_attr("_error_response")(
                422,
                "attachment purpose is invalid",
                options=_ErrorResponseOptions(
                    code="attachment_purpose_invalid",
                    stage="request_validation",
                    retryable=False,
                ),
            )
        return app_attr("_error_response")(422, "request validation failed")

    @app.exception_handler(LifecycleInvariantError)
    async def lifecycle_exception_handler(
        _request: Request,
        exc: LifecycleInvariantError,
    ) -> JSONResponse:
        """Render lifecycle violations as safe internal error envelopes."""
        safe_error = _safe_error(exc)
        return app_attr("_error_response")(
            safe_error.status_code,
            safe_error.message,
            options=_ErrorResponseOptions(
                code=safe_error.code,
                stage=safe_error.stage,
                retryable=safe_error.retryable,
            ),
        )

    @app.exception_handler(McpError)
    async def mcp_error_handler(
        _request: Request,
        exc: McpError,
    ) -> JSONResponse:
        """Project only fixed MCP failures into safe HTTP errors."""
        if isinstance(exc, (UpstreamConnectionError, UpstreamTimeoutError)):
            is_timeout = isinstance(exc, UpstreamTimeoutError)
            status = 504 if is_timeout else 502
            return app_attr("_error_response")(
                status,
                _SAFE_DEFAULT_MESSAGES[status],
                options=_ErrorResponseOptions(
                    code=(
                        "upstream_timeout" if is_timeout else "upstream_failed"
                    ),
                    retryable=True,
                ),
            )
        if (
            exc.error.code == INTERNAL_ERROR
            and exc.error.message == RETRIEVAL_UNAVAILABLE_MESSAGE
        ):
            return app_attr("_error_response")(
                500,
                RETRIEVAL_UNAVAILABLE_MESSAGE,
                options=_ErrorResponseOptions(
                    code="knowledge_retrieval_unavailable",
                    stage="retrieval",
                    retryable=True,
                ),
            )
        return app_attr("_error_response")(500, "internal server error")

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        _request: Request,
        _exc: Exception,
    ) -> JSONResponse:
        """Render unexpected errors as 500 envelopes."""
        failed_events = tuple(
            event
            for event in current_stage_trace()
            if event.error_code is not None
        )
        if not failed_events:
            return app_attr("_error_response")(500, "internal server error")
        event = failed_events[0]
        status_code = event.final_http_status or 500
        return app_attr("_error_response")(
            status_code,
            _SAFE_DEFAULT_MESSAGES.get(status_code, "internal server error"),
            options=_ErrorResponseOptions(
                code=event.error_code,
                stage=event.stage,
                retryable=status_code in {502, 503, 504},
            ),
        )

    register_upload_error_handler(app, app_attr("_error_response"))
