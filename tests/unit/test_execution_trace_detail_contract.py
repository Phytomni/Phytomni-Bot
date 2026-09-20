# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared contract fixtures for grouped public execution records."""

from __future__ import annotations

import json
from typing import Any

from tests.support.execution_event_fixtures import (
    EXECUTION_RUNTIME_V2_FIXTURES,
    EXECUTION_TRACE_DETAIL_V1_FIXTURES,
)

from mcp_server_phytomni.api.agent_capabilities import (
    serialize_execution_runtime_capability,
)


def _fixtures() -> dict[str, Any]:
    assert (
        EXECUTION_TRACE_DETAIL_V1_FIXTURES.exists()
    ), "shared execution trace fixture is missing"
    return json.loads(
        EXECUTION_TRACE_DETAIL_V1_FIXTURES.read_text(encoding="utf-8")
    )


def test_shared_trace_detail_fixture_freezes_required_records() -> None:
    """Verify shared trace detail fixture freezes required records."""
    fixtures = _fixtures()

    assert fixtures["schema_version"] == 1
    assert set(fixtures["records"]) == {
        "grouped_operation",
        "unknown_presenter",
    }
    assert (
        fixtures["records"]["grouped_operation"]["attempts"][0]["status"]
        == "failed"
    )
    assert fixtures["records"]["grouped_operation"]["progress"] == {
        "completed": 2,
        "total": 4,
        "unit": "dimensions",
    }
    assert fixtures["records"]["unknown_presenter"]["detail"] == {}
    assert fixtures["execution_log"]["artifact_role"] == "execution_log"
    assert fixtures["execution_log"]["target"]["kind"] == "artifact"
    assert {case["reason"] for case in fixtures["invalid_payloads"]} == {
        "forbidden_prompt",
        "forbidden_source_passage",
        "forbidden_sql",
        "forbidden_tool_payload",
        "forbidden_path",
        "forbidden_url",
        "forbidden_credential",
        "forbidden_provider_payload",
        "forbidden_exception",
    }


def test_shared_trace_detail_redacted_record_contains_only_safe_fields() -> (
    None
):
    """Verify shared trace detail redacted record contains only safe fields."""
    record = _fixtures()["redacted_record"]

    assert set(record["detail"]) == {"ordinal", "total"}
    serialized = json.dumps(record, ensure_ascii=False).lower()
    for forbidden in (
        "prompt",
        "source_passage",
        "sql",
        "tool_arguments",
        "provider_body",
        "exception_text",
        "password=",
        "https://",
        "/srv/",
    ):
        assert forbidden not in serialized


def test_runtime_fixture_matches_the_operation_record_capability() -> None:
    """Verify runtime fixture matches the operation record capability."""

    runtime_fixture = json.loads(
        EXECUTION_RUNTIME_V2_FIXTURES.read_text(encoding="utf-8")
    )
    expected = serialize_execution_runtime_capability()["operation_records"]

    assert runtime_fixture["capabilities"]["operation_records"] == expected


def test_stage_fixture_freezes_terminal_surface_and_clocks() -> None:
    """Verify stage fixture freezes terminal surface and clocks."""
    fixtures = _fixtures()
    contract = fixtures["stage_contract"]
    cases = {case["name"]: case for case in fixtures["stage_transition_cases"]}

    assert contract["stages"] == [
        "orchestration",
        "scientific_execution",
        "consolidation",
        "response_settlement",
    ]
    submitted = cases["child_submission_succeeded"]
    assert submitted["state"]["child_status"] == "succeeded"
    assert submitted["state"]["root_status"] == "running"
    assert (
        submitted["state"]["pending_status_key"]
        == "execution.pending.submitted"
    )

    provider_poll = cases["unchanged_provider_poll"]
    assert provider_poll["previous"]["clocks"]["last_execution_fact_at"] == (
        provider_poll["state"]["clocks"]["last_execution_fact_at"]
    )
    assert provider_poll["previous"]["clocks"]["last_provider_contact_at"] < (
        provider_poll["state"]["clocks"]["last_provider_contact_at"]
    )

    heartbeat = cases["stream_heartbeat"]
    assert heartbeat["previous"]["clocks"]["last_execution_fact_at"] == (
        heartbeat["state"]["clocks"]["last_execution_fact_at"]
    )
    assert heartbeat["previous"]["clocks"]["last_provider_contact_at"] == (
        heartbeat["state"]["clocks"]["last_provider_contact_at"]
    )
    assert heartbeat["previous"]["clocks"]["last_stream_contact_at"] < (
        heartbeat["state"]["clocks"]["last_stream_contact_at"]
    )


def test_shared_stage_fixture_rejects_impossible_or_regressive_states() -> (
    None
):
    """Verify shared stage fixture rejects impossible or regressive states."""
    fixtures = _fixtures()

    assert {case["reason"] for case in fixtures["invalid_stage_cases"]} == {
        "stage_regression",
        "child_terminal_closes_root",
        "todo_stage_mismatch",
        "pending_answer_mismatch",
        "stream_contact_mutates_execution_clocks",
        "unchanged_provider_mutates_execution_clock",
        "root_terminal_before_settlement",
    }
