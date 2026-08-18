# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Payload-free Expert routing logs for outcome class and provider timing."""

from __future__ import annotations

import logging
import re
import time
from enum import StrEnum

from ...runtime.request_context import current_request_id

__all__ = [
    "ExpertProviderAttemptResult",
    "ExpertRouteOutcome",
    "ExpertRoutePath",
    "elapsed_ms",
    "record_expert_provider_attempt",
    "record_expert_route_outcome",
]

_LOGGER = logging.getLogger(__name__)
_SAFE_ERROR_CLASS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9_:-]{1,128}$")
_ROUTE_MESSAGE = "Expert route outcome"
_PROVIDER_MESSAGE = "Expert routing provider completed"


class ExpertRouteOutcome(StrEnum):
    """Closed set of selection-stage outcomes for ``POST /v1/query/route``."""

    SELECTED = "selected"
    DECLINED_CHAT_FALLBACK = "declined_chat_fallback"
    DECLINED_NO_FALLBACK = "declined_no_fallback"
    SELECTION_CONTRACT = "selection_contract"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_ERROR = "provider_error"


class ExpertRoutePath(StrEnum):
    """Which Expert HTTP surface produced the selection-stage outcome."""

    V0 = "v0"
    CONTEXT = "context"


class ExpertProviderAttemptResult(StrEnum):
    """Closed set of one Pangu routing-completion attempt results."""

    OK = "ok"
    TIMEOUT = "timeout"
    TRANSIENT_RETRY = "transient_retry"
    PROVIDER_ERROR = "provider_error"
    CONSTRAINED_DOWNGRADE = "constrained_downgrade"


def elapsed_ms(started_ns: int) -> int:
    """Return a non-negative millisecond span from ``time.monotonic_ns``."""
    return max(0, (time.monotonic_ns() - started_ns) // 1_000_000)


def record_expert_route_outcome(
    outcome: ExpertRouteOutcome,
    *,
    path: ExpertRoutePath,
    forced: bool,
    error_class: str | None = None,
    http_status: int | None = None,
) -> None:
    """Log one selection-stage outcome without query or provider payload."""
    extra: dict[str, object] = {
        "event": "expert_route",
        "outcome": outcome.value,
        "path": path.value,
        "forced": forced,
        "stage": "routing",
    }
    if error_class is not None:
        extra["error_class"] = _error_class_label(error_class)
    if http_status is not None:
        extra["http_status"] = _http_status_label(http_status)
    request_id = _safe_request_id(current_request_id())
    if request_id is not None:
        extra["request_id"] = request_id
    forced_label = "true" if forced else "false"
    message = (
        f"{_ROUTE_MESSAGE} outcome={outcome.value} "
        f"path={path.value} forced={forced_label}"
    )
    if request_id is not None:
        message = f"{message} request_id={request_id}"
    level = (
        logging.INFO
        if outcome is ExpertRouteOutcome.SELECTED
        else logging.WARNING
    )
    _LOGGER.log(level, message, extra=extra)


def record_expert_provider_attempt(
    result: ExpertProviderAttemptResult,
    *,
    duration_ms: int,
    attempt: int,
) -> None:
    """Log one Pangu routing completion hop, including successes."""
    safe_duration = max(0, duration_ms)
    safe_attempt = max(0, attempt)
    extra: dict[str, object] = {
        "event": "expert_provider",
        "result": result.value,
        "duration_ms": safe_duration,
        "attempt": safe_attempt,
        "retry_count": safe_attempt,
        "stage": "routing",
    }
    request_id = _safe_request_id(current_request_id())
    if request_id is not None:
        extra["request_id"] = request_id
    message = (
        f"{_PROVIDER_MESSAGE} result={result.value} "
        f"duration_ms={safe_duration} attempt={safe_attempt}"
    )
    if request_id is not None:
        message = f"{message} request_id={request_id}"
    level = (
        logging.WARNING
        if result
        in {
            ExpertProviderAttemptResult.TIMEOUT,
            ExpertProviderAttemptResult.PROVIDER_ERROR,
        }
        else logging.INFO
    )
    _LOGGER.log(level, message, extra=extra)


def _error_class_label(name: str) -> str:
    """Return an identifier-shaped class name, never an exception message."""
    matched = _SAFE_ERROR_CLASS.fullmatch(name)
    if matched is None:
        return "Exception"
    return matched.group(0)


def _http_status_label(status: int) -> int:
    """Return status when it is a public HTTP code; otherwise 500."""
    if isinstance(status, bool):
        return 500
    try:
        code = int(status)
    except (TypeError, ValueError):
        return 500
    if 100 <= code <= 599:
        return code
    return 500


def _safe_request_id(value: str | None) -> str | None:
    """Keep request ids as bounded identifiers when the context has one."""
    if value is None or not _SAFE_REQUEST_ID.fullmatch(value):
        return None
    return value
