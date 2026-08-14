# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Strict Knowledge retrieval results and provider-payload validation."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Literal, TypedDict

from httpx import ConnectError, HTTPStatusError, RequestError, TimeoutException
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

RetrievalOutcome = Literal["complete", "no_match", "partial"]
RetrievalFailureKind = Literal[
    "timeout", "transport", "upstream", "protocol", "auth", "unknown"
]


class RetrievalFailure(TypedDict):
    """Bounded internal detail for one failed retrieval source."""

    source: str
    kind: RetrievalFailureKind
    retryable: bool


class RetrievalResult(TypedDict):
    """Knowledge-local evidence result with explicit reliability semantics."""

    doc_list: list[dict[str, Any]]
    total: int
    outcome: RetrievalOutcome
    failures: list[RetrievalFailure]


class RetrievalProtocolError(ValueError):
    """Signal a fixed, non-sensitive provider response-shape failure."""


RETRIEVAL_UNAVAILABLE_MESSAGE = "Knowledge retrieval temporarily unavailable"
_INVALID_RETRIEVAL_RESPONSE = "Invalid retrieval response"


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def require_retrieval_docs(
    payload: Any,
    field: str = "doc_list",
) -> list[dict[str, Any]]:
    """Validate and detach retrieval documents from a provider payload."""
    if not isinstance(payload, Mapping) or field not in payload:
        raise RetrievalProtocolError(_INVALID_RETRIEVAL_RESPONSE)
    value = payload[field]
    if not isinstance(value, list):
        raise RetrievalProtocolError(_INVALID_RETRIEVAL_RESPONSE)

    docs: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise RetrievalProtocolError(_INVALID_RETRIEVAL_RESPONSE)
        chunk_id = item.get("chunk_id")
        title = item.get("title")
        big_content = item.get("big_content")
        content = item.get("content")
        if not isinstance(chunk_id, str) or not chunk_id.strip():
            raise RetrievalProtocolError(_INVALID_RETRIEVAL_RESPONSE)
        if not isinstance(title, str):
            raise RetrievalProtocolError(_INVALID_RETRIEVAL_RESPONSE)
        if not isinstance(big_content, str) and not isinstance(content, str):
            raise RetrievalProtocolError(_INVALID_RETRIEVAL_RESPONSE)
        if "score" in item and not _finite_number(item["score"]):
            raise RetrievalProtocolError(_INVALID_RETRIEVAL_RESPONSE)
        docs.append(dict(item))
    return docs


def classify_retrieval_failure(
    exc: Exception,
    source: str,
) -> RetrievalFailure:
    """Classify one failure without retaining exception text or endpoints."""
    kind: RetrievalFailureKind
    retryable = False
    if isinstance(exc, TimeoutException):
        kind = "timeout"
        retryable = True
    elif isinstance(exc, HTTPStatusError):
        status_code = exc.response.status_code
        if status_code in (401, 403):
            kind = "auth"
        else:
            kind = "upstream"
            retryable = status_code == 429 or status_code >= 500
    elif isinstance(exc, (ConnectError, RequestError)):
        kind = "transport"
        retryable = True
    elif isinstance(exc, RetrievalProtocolError):
        kind = "protocol"
    elif isinstance(exc, McpError):
        kind = "upstream"
        retryable = True
    else:
        kind = "unknown"
    return {"source": source, "kind": kind, "retryable": retryable}


def retrieval_unavailable_error() -> McpError:
    """Build the fixed public failure for unavailable reliable evidence."""
    return McpError(
        ErrorData(code=INTERNAL_ERROR, message=RETRIEVAL_UNAVAILABLE_MESSAGE)
    )


def cacheable_retrieval_result(result: Any) -> bool:
    """Return whether a complete non-empty result is safe to memoize."""
    if not isinstance(result, Mapping):
        return False
    if result.get("outcome") != "complete" or result.get("failures") != []:
        return False
    try:
        docs = require_retrieval_docs(result)
    except RetrievalProtocolError:
        return False
    return bool(docs)


__all__ = [
    "RETRIEVAL_UNAVAILABLE_MESSAGE",
    "RetrievalFailure",
    "RetrievalFailureKind",
    "RetrievalOutcome",
    "RetrievalProtocolError",
    "RetrievalResult",
    "cacheable_retrieval_result",
    "classify_retrieval_failure",
    "require_retrieval_docs",
    "retrieval_unavailable_error",
]
