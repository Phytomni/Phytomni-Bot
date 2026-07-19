# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Typed, domain-neutral primitives for bounded query resolution."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ...common.responses import message_content, parse_json_object_fragment

__all__ = [
    "ResolverFailure",
    "ResolverInvocation",
    "build_candidate_item_schema",
    "invoke_chat_resolver",
    "invoke_resolver",
    "normalize_candidate_fields",
    "normalize_confidence",
    "normalize_species_code",
    "parse_resolver_payload",
    "translate_resolver_failure",
]


class ResolverFailureError(ValueError):
    """Raised when a shared resolver primitive cannot produce valid output."""


ResolverFailure = ResolverFailureError


@dataclass(frozen=True)
class ResolverInvocation:
    """Provider options and error policy for one structured resolver call."""

    chat_kwargs: Mapping[str, Any]
    timeout_seconds: float
    error_type: type[ValueError]
    empty_result_message: str = "LLM returned no content after retries"


def parse_resolver_payload(
    response: Mapping[str, Any],
    error_type: type[ValueError],
) -> dict[str, Any]:
    """Extract a JSON object from one resolver response.

    Args:
        response: OpenAI-style response mapping returned by the chat call.
        error_type: Domain-specific error class for malformed output.

    Returns:
        Parsed resolver payload.

    Raises:
        ValueError: ``error_type`` with stable empty/non-parseable messages.
    """
    content = message_content(response)
    if not content:
        raise error_type("LLM returned empty content")
    payload = parse_json_object_fragment(content)
    if not payload:
        raise error_type("non-parseable LLM output")
    return payload


def build_candidate_item_schema(
    identifier_field: str,
    confidence_schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the JSON-schema item shared by gene and trait candidates.

    Args:
        identifier_field: Domain-specific candidate id field name.
        confidence_schema: Provider schema fragment carrying ``minimum`` and
            ``maximum`` confidence bounds.

    Returns:
        A JSON-schema object without provider-specific title metadata.
    """
    return {
        "type": "object",
        "properties": {
            identifier_field: {"type": "string"},
            "species_code": {"type": "string"},
            "confidence": {
                "type": "number",
                "minimum": confidence_schema["minimum"],
                "maximum": confidence_schema["maximum"],
            },
        },
        "required": [identifier_field],
    }


def normalize_confidence(value: Any) -> float:
    """Convert a numeric confidence to the inclusive 0.0-1.0 range.

    Args:
        value: Candidate confidence supplied by a model response.

    Returns:
        A finite confidence value clamped to the protocol range.

    Raises:
        ResolverFailure: If value is not a finite numeric value.
    """
    if isinstance(value, bool):
        raise ResolverFailure("confidence must be numeric")
    try:
        confidence = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ResolverFailure("confidence must be numeric") from exc
    if not math.isfinite(confidence):
        raise ResolverFailure("confidence must be numeric")
    return max(0.0, min(1.0, confidence))


def normalize_species_code(value: Any, fallback: str) -> str:
    """Return a stripped candidate species code or its domain fallback.

    Args:
        value: Candidate species code supplied by a model response.
        fallback: Top-level species code selected by the domain resolver.

    Returns:
        A non-blank candidate code when supplied, otherwise ``fallback``.
    """
    candidate = value.strip() if isinstance(value, str) else ""
    return candidate or fallback


def normalize_candidate_fields(
    candidate: Mapping[str, Any],
    fallback_species_code: str,
) -> tuple[float, str]:
    """Normalize confidence and per-candidate species metadata together.

    Args:
        candidate: Candidate mapping supplied by a model response.
        fallback_species_code: Top-level species code selected by the domain.

    Returns:
        A bounded confidence and a stripped species code.

    Notes:
        Invalid confidence values preserve the legacy resolver behavior of
        falling back to ``0.0``; the strict primitive remains available for
        callers that need fail-closed validation.
    """
    try:
        confidence = normalize_confidence(candidate.get("confidence", 0.0))
    except ResolverFailure:
        confidence = 0.0
    return confidence, normalize_species_code(
        candidate.get("species_code"), fallback_species_code
    )


def translate_resolver_failure(
    failure: ResolverFailureError,
    timeout_seconds: float,
    error_type: type[ValueError],
    empty_result_message: str,
) -> ValueError:
    """Translate shared failure reasons into a domain error type.

    Args:
        failure: Shared timeout or empty-result failure.
        timeout_seconds: Domain timeout used for stable error wording.
        error_type: Domain-specific ``ValueError`` subclass to construct.
        empty_result_message: Domain-specific message for a missing result.

    Returns:
        A domain-specific error instance ready to be raised by the caller.
    """
    if str(failure).startswith("resolver timeout after"):
        return error_type(f"resolver timeout after {timeout_seconds:.1f} s")
    if str(failure) == "resolver returned no result":
        return error_type(empty_result_message)
    return error_type(str(failure))


async def invoke_chat_resolver(
    chat_call: Callable[..., Awaitable[Mapping[str, Any] | None]],
    user_query: str,
    invocation: ResolverInvocation,
) -> Mapping[str, Any]:
    """Invoke one OpenAI-style resolver call with domain error translation.

    Args:
        chat_call: Async chat function accepting ``user_query`` and kwargs.
        user_query: Fully rendered user prompt.
        invocation: Provider options and domain error policy for the call.

    Returns:
        The non-empty provider response mapping.
    """
    return await invoke_resolver(
        lambda: chat_call(
            user_query=user_query, **dict(invocation.chat_kwargs)
        ),
        invocation.timeout_seconds,
        failure_mapper=lambda failure: translate_resolver_failure(
            failure,
            invocation.timeout_seconds,
            invocation.error_type,
            invocation.empty_result_message,
        ),
    )


async def invoke_resolver(
    call: Callable[[], Awaitable[Mapping[str, Any] | None]],
    timeout_seconds: float,
    *,
    failure_mapper: Callable[[ResolverFailureError], Exception] | None = None,
) -> Mapping[str, Any]:
    """Run a resolver call under a wall-clock timeout.

    Args:
        call: Zero-argument awaitable factory for the resolver request.
        timeout_seconds: Maximum time allowed for the request.
        failure_mapper: Optional domain-specific translator for shared
            timeout and empty-result failures.

    Returns:
        The resolver's non-empty mapping response.

    Raises:
        ResolverFailure: If the call times out or returns None.
        asyncio.CancelledError: Propagated unchanged for task cleanup.
    """
    try:
        result = await asyncio.wait_for(call(), timeout=timeout_seconds)
    except TimeoutError as exc:
        failure = ResolverFailure(
            f"resolver timeout after {timeout_seconds:.2f} s"
        )
        if failure_mapper is not None:
            raise failure_mapper(failure) from exc
        raise failure from exc
    if result is None:
        failure = ResolverFailure("resolver returned no result")
        if failure_mapper is not None:
            raise failure_mapper(failure) from failure
        raise failure
    return result
