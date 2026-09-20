# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Fixture-driven tests for the public execution-event V1 contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from mcp_server_phytomni.runtime.execution_events import (
    PUBLIC_EVENT_KINDS,
    ExecutionEventIntent,
    ExecutionEventValidationError,
    parse_execution_event,
    parse_execution_event_intent,
    parse_run_event_projection,
)

FIXTURES = (
    Path(__file__).parents[2]
    / "docs"
    / "contracts"
    / "execution-events"
    / "v1"
    / "fixtures.json"
)


def _fixtures() -> dict[str, Any]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def _event(
    fixtures: dict[str, Any], override: dict[str, object], index: int = 1
) -> dict[str, object]:
    base = dict(fixtures["base_event"])
    base.update(override)
    base["seq"] = index
    base["event_id"] = f"evt-fixture-{index}"
    base["idempotency_key"] = f"fixture:event:{index}"
    return base


def test_shared_fixture_covers_the_finite_public_vocabulary() -> None:
    """Verify shared fixture covers the finite public vocabulary."""

    fixtures = _fixtures()
    contract = fixtures["contract"]
    cases = fixtures["valid_events"]
    assert isinstance(contract, dict)
    assert isinstance(cases, list)
    fixture_kinds = {case["event"]["kind"] for case in cases}
    assert fixture_kinds == set(contract["event_kinds"])
    assert fixture_kinds == set(PUBLIC_EVENT_KINDS)


def test_shared_fixture_freezes_the_single_public_execution_identity() -> None:
    """Verify shared fixture freezes the single public execution identity."""
    identity = _fixtures()["contract"]["execution_identity"]
    assert identity == {
        "source_field": "client_turn_id",
        "public_field": "execution_id",
        "transport_header": "X-Phyto-Execution-Id",
        "opaque_pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
        "stable_retry": "same_fingerprint",
        "replacement": "new_identity",
        "second_client_uuid_allowed": False,
    }


def test_every_valid_fixture_decodes_and_round_trips() -> None:
    """Verify every valid fixture decodes and round trips."""

    fixtures = _fixtures()
    cases = fixtures["valid_events"]
    assert isinstance(cases, list)
    for index, case in enumerate(cases, start=1):
        raw = _event(fixtures, case["event"], index)
        decoded = parse_execution_event(raw)
        assert decoded.to_public_dict() == raw, case["name"]


def test_ignorable_unknown_fixture_preserves_sequence_and_cursor() -> None:
    """Verify ignorable unknown fixture preserves sequence and cursor."""

    fixtures = _fixtures()
    raw = _event(fixtures, fixtures["ignorable_extension"], 22)
    decoded = parse_execution_event(raw)
    assert decoded.seq == 22
    assert decoded.ignorable is True
    assert decoded.is_known is False


def test_projection_fixture_decodes_and_round_trips() -> None:
    """Verify projection fixture decodes and round trips."""

    raw = _fixtures()["projection"]
    decoded = parse_run_event_projection(raw)
    assert decoded.to_public_dict() == raw


@pytest.mark.parametrize("case", _fixtures()["invalid_events"])
def test_invalid_public_fixture_fails_closed(case: dict[str, Any]) -> None:
    """Verify invalid public fixture fails closed."""

    fixtures = _fixtures()
    raw = _event(fixtures, case["event"])
    with pytest.raises(ExecutionEventValidationError, match=case["reason"]):
        parse_execution_event(raw)


@pytest.mark.parametrize(
    ("_name", "override", "reason"),
    [
        (
            "credential value",
            {
                "kind": "decision.note",
                "payload": {"text": "password=hunter2"},
            },
            "forbidden_public_value",
        ),
        (
            "authorization value",
            {
                "kind": "reasoning.summary",
                "payload": {"text": "Bearer private-token"},
            },
            "forbidden_public_value",
        ),
        (
            "provider payload map",
            {"payload": {"provider_payload": {"private": "value"}}},
            "forbidden_public_payload",
        ),
        (
            "raw reasoning",
            {"payload": {"raw_reasoning": "private scratchpad"}},
            "forbidden_public_payload",
        ),
        (
            "absolute posix path in allowed text",
            {
                "kind": "decision.note",
                "payload": {"text": "/srv/private/result.csv"},
            },
            "forbidden_public_value",
        ),
        (
            "absolute windows path in allowed text",
            {
                "kind": "decision.note",
                "payload": {"text": "C:\\private\\result.csv"},
            },
            "forbidden_public_value",
        ),
        (
            "storage key",
            {"payload": {"storage_key": "bucket/private/object"}},
            "forbidden_public_payload",
        ),
        (
            "arbitrary URL in summary",
            {
                "summary": {
                    "key": "activity.run.started",
                    "text": "Open https://storage.invalid/private",
                }
            },
            "forbidden_public_value",
        ),
        (
            "unbounded exception text",
            {
                "kind": "run.failed",
                "status": "failed",
                "payload": {"code": "x" * 513, "retryable": False},
            },
            "public_string_too_large",
        ),
    ],
)
def test_sensitive_values_cannot_hide_in_allowed_fields(
    _name: str, override: dict[str, object], reason: str
) -> None:
    """Verify sensitive values cannot hide in allowed fields."""

    fixtures = _fixtures()
    raw = _event(fixtures, override)
    with pytest.raises(ExecutionEventValidationError, match=reason):
        parse_execution_event(raw)


def test_known_event_payloads_decode_to_finite_public_types() -> None:
    """Verify known event payloads decode to finite public types."""

    fixtures = _fixtures()
    cases = fixtures["valid_events"]
    assert isinstance(cases, list)
    for index, case in enumerate(cases, start=1):
        decoded = parse_execution_event(_event(fixtures, case["event"], index))
        assert isinstance(decoded.payload, BaseModel), case["name"]
        assert decoded.to_public_dict()["payload"] == case["event"]["payload"]


def test_event_intent_materializes_a_valid_ordered_event() -> None:
    """Verify event intent materializes a valid ordered event."""

    intent = parse_execution_event_intent(
        {
            "kind": "tool.completed",
            "status": "succeeded",
            "summary": {
                "key": "activity.tool.completed",
                "text": "Search completed",
            },
            "payload": {
                "tool_key": "tool.search",
                "call_id": "call-1",
                "duration_ms": 42,
            },
            "idempotency_key": "tool:call-1:completed",
        }
    )
    assert isinstance(intent, ExecutionEventIntent)

    event = intent.materialize(
        run_id="run-1",
        seq=7,
        event_id="evt-7",
        occurred_at="2026-08-18T00:00:00Z",
    )

    assert event.run_id == "run-1"
    assert event.seq == 7
    assert event.event_id == "evt-7"
    assert event.kind == "tool.completed"
    assert event.to_public_dict()["payload"]["duration_ms"] == 42


def test_event_intent_rejects_unknown_or_private_payloads() -> None:
    """Verify event intent rejects unknown or private payloads."""

    base = {
        "status": "running",
        "summary": {"key": "activity.test", "text": "Safe"},
        "payload": {},
    }
    with pytest.raises(
        ExecutionEventValidationError, match="unknown_required_kind"
    ):
        parse_execution_event_intent({**base, "kind": "unknown.required"})
    with pytest.raises(
        ExecutionEventValidationError, match="forbidden_public_payload"
    ):
        parse_execution_event_intent(
            {
                **base,
                "kind": "decision.note",
                "payload": {"raw_reasoning": "private"},
            }
        )
