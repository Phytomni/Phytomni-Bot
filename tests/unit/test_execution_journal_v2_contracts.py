# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Finite and safe public contracts for execution journal V2."""

from __future__ import annotations

import json
from typing import Any, cast, get_args

import pytest
from pydantic import ValidationError
from tests.support.execution_event_fixtures import (
    EXECUTION_RUNTIME_V2_FIXTURES,
)

from mcp_server_phytomni.runtime.execution_journal_v2 import (
    ActionRevision,
    ExecutionEventType,
    ExecutionJournalValidationError,
    ExecutionStatus,
    OutputRevision,
    PublicTarget,
    PublicTargetKind,
    SpanStatus,
    TerminalStatusV2,
    TrackingHealth,
    WorkUnitStatus,
    parse_execution_event_v2,
)


def _durable_event() -> dict[str, Any]:
    fixtures = json.loads(
        EXECUTION_RUNTIME_V2_FIXTURES.read_text(encoding="utf-8")
    )
    return dict(fixtures["durable_event"])


def test_frozen_v2_event_fixture_round_trips() -> None:
    """Verify frozen V2 event fixture round trips."""

    raw = _durable_event()
    event = parse_execution_event_v2(raw)

    assert event.type is ExecutionEventType.SPAN_STARTED
    assert event.to_public_dict() == raw


def test_v2_contracts_expose_only_finite_lifecycle_vocabularies() -> None:
    """Verify V2 contracts expose only finite lifecycle vocabularies."""

    expected = {
        "admitted",
        "queued",
        "dispatching",
        "running",
        "waiting_input",
    } | set(get_args(TerminalStatusV2))
    assert {status.value for status in ExecutionStatus} == expected
    assert "retry_scheduled" in {status.value for status in WorkUnitStatus}
    assert "skipped" in {status.value for status in SpanStatus}
    assert {health.value for health in TrackingHealth} == {
        "healthy",
        "degraded",
        "stale",
        "contract_degraded",
    }


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    [
        (
            ("public_payload", "raw_reasoning"),
            "private",
            "forbidden_public_payload",
        ),
        (
            ("public_payload", "provider_payload"),
            {"raw": "x"},
            "forbidden_public_payload",
        ),
        (
            ("public_payload", "tool_result"),
            {"rows": []},
            "forbidden_public_payload",
        ),
        (
            ("public_payload", "text"),
            "Bearer private-token",
            "forbidden_public_value",
        ),
        (
            ("summary", "text"),
            "https://storage.invalid/private",
            "forbidden_public_value",
        ),
        (
            ("summary", "text"),
            "C:\\private\\report.txt",
            "forbidden_public_value",
        ),
        (("summary", "text"), "\ud800", "invalid_public_utf8"),
    ],
)
def test_v2_public_contract_rejects_private_or_unsafe_data(
    path: tuple[str, str], value: object, reason: str
) -> None:
    """Verify V2 public contract rejects private or unsafe data."""

    raw = _durable_event()
    parent = dict(raw[path[0]])
    parent[path[1]] = value
    raw[path[0]] = parent

    with pytest.raises(ExecutionJournalValidationError, match=reason):
        parse_execution_event_v2(raw)


def test_v2_event_rejects_unknown_type_and_oversized_public_data() -> None:
    """Verify V2 event rejects unknown type and oversized public data."""

    unknown = _durable_event()
    unknown["type"] = "provider.private_update"
    with pytest.raises(
        ExecutionJournalValidationError, match="unknown_required_event_type"
    ):
        parse_execution_event_v2(unknown)

    oversized = _durable_event()
    oversized["summary"] = {"key": "activity.test", "text": "x" * 513}
    with pytest.raises(
        ExecutionJournalValidationError, match="public_string_too_large"
    ):
        parse_execution_event_v2(oversized)


def test_event_type_selects_a_strict_payload_model() -> None:
    """Verify event type selects a strict payload model."""

    raw = _durable_event()
    raw["public_payload"] = {"phase": "retrieval", "provider": "private"}
    with pytest.raises(
        ExecutionJournalValidationError, match="invalid_public_payload"
    ):
        parse_execution_event_v2(raw)


def test_output_action_and_target_revisions_are_bounded() -> None:
    """Verify output action and target revisions are bounded."""

    assert OutputRevision(revision=3, offset=9).model_dump() == {
        "revision": 3,
        "offset": 9,
    }
    assert ActionRevision(expected_revision=4).expected_revision == 4
    assert (
        PublicTarget(
            kind=PublicTargetKind.ARTIFACT, id="artifact-safe"
        ).kind.value
        == "artifact"
    )
    with pytest.raises(ValidationError):
        OutputRevision(revision=-1, offset=0)
    with pytest.raises(ValidationError):
        ActionRevision(expected_revision=-1)
    with pytest.raises(ValidationError):
        PublicTarget(kind=cast(Any, "url"), id="https://private.invalid")
