# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for shared query-resolution primitives."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from mcp_server_phytomni.agents.shared.query_resolution import (
    ResolverFailure,
    ResolverInvocation,
    build_candidate_item_schema,
    invoke_chat_resolver,
    invoke_resolver,
    normalize_candidate_fields,
    normalize_confidence,
    normalize_species_code,
    parse_resolver_payload,
)

pytestmark = pytest.mark.unit


async def test_invoke_resolver_returns_mapping() -> None:
    """A successful resolver result is returned unchanged."""
    result = await invoke_resolver(
        lambda: _return_result({"gene_id": "AT1G01010"}),
        timeout_seconds=1.0,
    )

    assert result == {"gene_id": "AT1G01010"}


async def test_invoke_resolver_rejects_none_result() -> None:
    """A missing resolver result becomes a typed shared failure."""
    with pytest.raises(ResolverFailure, match="no result"):
        await invoke_resolver(_return_none, timeout_seconds=1.0)


async def test_invoke_resolver_maps_shared_failure() -> None:
    """A domain mapper may translate shared failure wording at the edge."""

    def map_failure(failure: ResolverFailure) -> RuntimeError:
        return RuntimeError(f"mapped: {failure}")

    with pytest.raises(
        RuntimeError, match="mapped: resolver returned no result"
    ):
        await invoke_resolver(
            _return_none,
            timeout_seconds=1.0,
            failure_mapper=map_failure,
        )


async def test_invoke_chat_resolver_maps_none_to_domain_error() -> None:
    """Structured invocation maps an empty provider response at the edge."""

    async def no_response(**_: Any) -> None:
        return None

    with pytest.raises(ValueError, match="retry exhausted"):
        await invoke_chat_resolver(
            no_response,
            "query",
            ResolverInvocation(
                chat_kwargs={},
                timeout_seconds=1.0,
                error_type=ValueError,
                empty_result_message="retry exhausted",
            ),
        )


async def test_invoke_resolver_translates_timeout() -> None:
    """Only a wall-clock timeout is translated into ResolverFailure."""
    with pytest.raises(
        ResolverFailure,
        match=r"resolver timeout after 0\.01 s",
    ):
        await invoke_resolver(_never_finishes, timeout_seconds=0.01)


async def test_invoke_resolver_propagates_cancellation() -> None:
    """Cancellation remains visible to the caller for task cleanup."""

    async def cancelled() -> Mapping[str, Any] | None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await invoke_resolver(cancelled, timeout_seconds=1.0)


@pytest.mark.parametrize("value, expected", [(-0.5, 0.0), (1.5, 1.0)])
def test_normalize_confidence_clamps_bounds(
    value: float,
    expected: float,
) -> None:
    """Confidence values outside the protocol range are clamped."""
    assert normalize_confidence(value) == expected


@pytest.mark.parametrize("value", [object(), True, None])
def test_normalize_confidence_rejects_non_numeric_values(value: Any) -> None:
    """Non-numeric confidence values fail closed."""
    with pytest.raises(ResolverFailure, match="confidence must be numeric"):
        normalize_confidence(value)


@pytest.mark.parametrize(
    "value, fallback, expected",
    [(" ath ", "osa", "ath"), (None, "osa", "osa"), ("   ", "osa", "osa")],
)
def test_normalize_species_code_uses_fallback(
    value: Any,
    fallback: str,
    expected: str,
) -> None:
    """Candidate species codes are stripped and fall back when blank."""
    assert normalize_species_code(value, fallback) == expected


def test_normalize_candidate_fields_defaults_invalid_confidence() -> None:
    """Candidate normalization keeps a zero-confidence fallback."""
    assert normalize_candidate_fields(
        {"confidence": "not-a-number", "species_code": " ath "},
        "osa",
    ) == (0.0, "ath")


def test_parse_resolver_payload_accepts_embedded_json() -> None:
    """Resolver payloads may be wrapped in short provider prose."""
    response = {
        "choices": [
            {"message": {"content": 'Result: {"gene_id": "AT1G01010"}'}}
        ]
    }

    assert parse_resolver_payload(response, ValueError) == {
        "gene_id": "AT1G01010"
    }


@pytest.mark.parametrize("content", ["", "not json"])
def test_parse_resolver_payload_rejects_missing_object(content: str) -> None:
    """Empty and non-JSON content use the same domain error boundary."""
    response = {"choices": [{"message": {"content": content}}]}

    with pytest.raises(
        ValueError, match="LLM returned empty content|non-parseable"
    ):
        parse_resolver_payload(response, ValueError)


def test_build_candidate_item_schema_keeps_identifier_and_bounds() -> None:
    """The shared schema builder preserves domain id names and limits."""
    assert build_candidate_item_schema(
        "gene_id", {"minimum": 0.0, "maximum": 1.0}
    ) == {
        "type": "object",
        "properties": {
            "gene_id": {"type": "string"},
            "species_code": {"type": "string"},
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
            },
        },
        "required": ["gene_id"],
    }


async def _return_result(result: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a mapping from an awaitable factory used by the tests."""
    return result


async def _return_none() -> None:
    """Return no resolver result."""
    return None


async def _never_finishes() -> Mapping[str, Any]:
    """Sleep until the invocation timeout cancels this coroutine."""
    await asyncio.sleep(60)
    return {}
