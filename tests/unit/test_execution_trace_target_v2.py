# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Opaque trace targets are finite, stable, and cohort gated."""

from __future__ import annotations

import re


def test_trace_target_is_opaque_stable_and_catalog_scoped() -> None:
    from mcp_server_phytomni.runtime.execution_trace_target_v2 import (
        trace_target_for_operation,
    )

    target = trace_target_for_operation(
        agent_slug="network",
        operation_key="remote.analysis",
        execution_id="execution-private-identity",
        work_unit_id="work-private-identity",
    )
    assert target is not None
    assert target == trace_target_for_operation(
        agent_slug="network",
        operation_key="remote.analysis",
        execution_id="execution-private-identity",
        work_unit_id="work-private-identity",
    )
    assert target["kind"] == "trace"
    assert re.fullmatch(r"trc_[A-Za-z0-9_-]{16,80}", target["id"])
    assert "execution-private-identity" not in target["id"]
    assert "work-private-identity" not in target["id"]

    enabled = {
        slug: trace_target_for_operation(
            agent_slug=slug,
            operation_key="remote.analysis",
            execution_id="execution-private-identity",
            work_unit_id="work-private-identity",
        )
        for slug in (
            "analyst",
            "deep_genome",
            "research",
            "design",
            "network",
        )
    }
    assert all(value is not None for value in enabled.values())
    assert len(
        {value["id"] for value in enabled.values() if value is not None}
    ) == len(enabled)
    assert (
        trace_target_for_operation(
            agent_slug="chat",
            operation_key="remote.analysis",
            execution_id="execution-private-identity",
            work_unit_id="work-private-identity",
        )
        is None
    )
    assert (
        trace_target_for_operation(
            agent_slug="network",
            operation_key="remote.submit",
            execution_id="execution-private-identity",
            work_unit_id="work-private-identity",
        )
        is None
    )


def test_trace_target_is_admitted_and_grouped_on_remote_analysis() -> None:
    from mcp_server_phytomni.runtime.execution_journal_v2 import (
        parse_execution_event_v2,
    )
    from mcp_server_phytomni.runtime.execution_projection_v2 import (
        fold_execution_events_v2,
    )
    from mcp_server_phytomni.runtime.execution_trace_target_v2 import (
        trace_target_for_operation,
    )

    target = trace_target_for_operation(
        agent_slug="network",
        operation_key="remote.analysis",
        execution_id="execution-target",
        work_unit_id="private-work-id",
    )
    assert target is not None
    event = parse_execution_event_v2(
        {
            "schema_version": 2,
            "event_id": "event-target",
            "execution_id": "execution-target",
            "seq": 1,
            "type": "work_unit.registered",
            "status": "queued",
            "occurred_at": "2026-08-23T00:00:00Z",
            "source": "provider",
            "span_id": "span-analysis",
            "parent_span_id": "span-root",
            "work_unit_id": "private-work-id",
            "attempt": 1,
            "summary": {
                "key": "execution.operation.remote.analysis",
                "text": "Run analysis",
            },
            "public_payload": {"operation_key": "remote.analysis"},
            "target": target,
            "idempotency_key": "target-registration",
        }
    )
    projection = fold_execution_events_v2("execution-target", (event,))

    assert projection.operations[0].target == event.target
    assert projection.targets == (event.target,)
