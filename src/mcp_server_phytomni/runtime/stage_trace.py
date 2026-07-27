# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Payload-free, request-local stage tracing for DataAgent."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from contextvars import Token
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol, cast

from .request_context import (
    _append_request_stage_event,
    _bind_stage_trace,
    _current_stage_trace,
    current_request_id,
)

__all__ = [
    "DataStage",
    "StageTraceEvent",
    "StageTraceSink",
    "bind_stage_trace",
    "classify_stage_error",
    "current_stage_trace",
    "stage_failure_from_exception",
    "trace_data_stage",
]

_LOGGER = logging.getLogger(__name__)
_SAFE_LABEL = re.compile(r"^[a-z0-9_:-]{1,64}$")
_SAFE_ERROR_CLASS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_STAGE_FAILURE_ATTRIBUTE = "_phytomni_stage_failure"


class DataStage(StrEnum):
    """The six observable boundaries of one DataAgent request."""

    NATIVE_REQUEST = "native_request"
    DATA_REWRITE = "data_rewrite"
    NL2SQL_REQUEST = "nl2sql_request"
    DATABASE_QUERY = "database_query"
    RESULT_FORMAT = "result_format"
    RUN_PERSIST = "run_persist"


@dataclass(frozen=True, slots=True)
class _StageTraceCore:
    """Core identity and classification fields for one stage event."""

    request_id: str
    agent: Literal["data"]
    stage: str
    dependency: str
    duration_ms: int
    error_code: str | None
    error_class: str | None


@dataclass(frozen=True, slots=True)
class StageTraceEvent(_StageTraceCore):
    """Safe summary of one DataAgent stage attempt."""

    final_http_status: int | None


class StageTraceSink(Protocol):
    """Consumer for one already-redacted stage event."""

    def emit(self, event: StageTraceEvent) -> None:
        """Persist or log one safe stage event."""

    # Keep the one-method structural contract while avoiding Pylint's
    # data-holder heuristic for protocol classes.
    if not TYPE_CHECKING:

        @property
        def contract_name(self) -> str:
            """Identify the runtime-only tracing contract."""
            return "stage_trace"


ErrorClassifier = Callable[[BaseException], tuple[str, int]]


def _emit_logged_stage_event(event: StageTraceEvent) -> None:
    """Emit only the event's primitive, bounded fields."""
    _LOGGER.info("DataAgent stage completed", extra=asdict(event))


def bind_stage_trace(
    events: tuple[StageTraceEvent, ...] = (),
) -> Token[tuple[StageTraceEvent, ...]]:
    """Bind a request-local trace tuple and return its reset token."""
    return cast(Token[tuple[StageTraceEvent, ...]], _bind_stage_trace(events))


def current_stage_trace() -> tuple[StageTraceEvent, ...]:
    """Return the ordered stage events in the current request context."""
    return cast(tuple[StageTraceEvent, ...], _current_stage_trace())


def _append_stage_event(event: StageTraceEvent) -> None:
    """Append one immutable event without exposing a mutable store."""
    _append_request_stage_event(event)


def _safe_label(value: str, *, fallback: str) -> str:
    """Keep caller-provided taxonomy labels bounded and payload-free."""
    if _SAFE_LABEL.fullmatch(value):
        return value
    return fallback


def _safe_error_class(value: str) -> str:
    """Keep exception class names as identifiers, never messages."""
    if _SAFE_ERROR_CLASS.fullmatch(value):
        return value
    return "Exception"


def _safe_http_status(value: int) -> int:
    """Keep classifier output within the public HTTP status range."""
    if isinstance(value, bool) or not 100 <= value <= 599:
        return 500
    return value


def stage_failure_from_exception(
    exc: BaseException,
) -> tuple[str, str, int | None] | None:
    """Find fixed stage metadata attached by a child execution context."""
    seen: set[int] = set()

    def walk(current: BaseException) -> tuple[str, str, int | None] | None:
        """Search one exception and its safe causal children."""
        marker = id(current)
        if marker in seen:
            return None
        seen.add(marker)
        value = getattr(current, _STAGE_FAILURE_ATTRIBUTE, None)
        if (
            isinstance(value, tuple)
            and len(value) == 3
            and all(isinstance(item, str) for item in value[:2])
            and (value[2] is None or isinstance(value[2], int))
        ):
            return value
        if isinstance(current, BaseExceptionGroup):
            for child in current.exceptions:
                found = walk(child)
                if found is not None:
                    return found
        for child in (current.__cause__, current.__context__):
            if child is not None:
                found = walk(child)
                if found is not None:
                    return found
        return None

    return walk(exc)


def classify_stage_error(exc: BaseException) -> tuple[str, int]:
    """Classify common failures without inspecting their messages."""
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "upstream_timeout", 504
    if isinstance(exc, (ConnectionError, OSError)):
        return "upstream_unavailable", 502
    if isinstance(exc, ValueError):
        return "invalid_stage_input", 400
    return "stage_failed", 500


@asynccontextmanager
async def trace_data_stage(
    stage: DataStage,
    *,
    dependency: str,
    sink: StageTraceSink | None = None,
    error_classifier: ErrorClassifier = classify_stage_error,
) -> AsyncIterator[None]:
    """Trace one stage, re-raising failures after recording safe metadata."""
    started = time.monotonic_ns()
    error_code: str | None = None
    error_class: str | None = None
    http_status: int | None = None
    try:
        yield
    except Exception as exc:
        classified_code, classified_status = error_classifier(exc)
        error_code = _safe_label(classified_code, fallback="stage_failed")
        error_class = _safe_error_class(exc.__class__.__name__)
        http_status = _safe_http_status(classified_status)
        with suppress(AttributeError, TypeError):
            setattr(
                exc,
                _STAGE_FAILURE_ATTRIBUTE,
                (stage.value, error_code, http_status),
            )
        raise
    finally:
        event = StageTraceEvent(
            request_id=current_request_id() or "unknown",
            agent="data",
            stage=stage.value,
            dependency=_safe_label(dependency, fallback="unknown"),
            duration_ms=max(
                0,
                (time.monotonic_ns() - started) // 1_000_000,
            ),
            error_code=error_code,
            error_class=error_class,
            final_http_status=http_status,
        )
        if sink is None:
            _emit_logged_stage_event(event)
        else:
            sink.emit(event)
        _append_stage_event(event)
