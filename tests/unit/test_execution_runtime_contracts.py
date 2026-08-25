# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Transport-neutral contracts at the execution Runtime/Driver boundary."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest


def test_execution_context_keeps_public_identity_across_nested_invocation() -> (
    None
):
    from mcp_server_phytomni.public_agent_catalog import PUBLIC_AGENT_CATALOG
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        ExecutionContext,
    )

    spec = next(
        item for item in PUBLIC_AGENT_CATALOG if item.slug == "research"
    )
    root = ExecutionContext(
        owner_ref="owner-1",
        execution_id="turn-1",
        fingerprint_version=1,
        fingerprint="a" * 64,
        agent=spec,
        root_span_id="span-root",
        current_span_id="span-root",
        transport="http",
    )
    nested = root.nested(
        agent=next(
            item for item in PUBLIC_AGENT_CATALOG if item.slug == "brief_gene"
        ),
        span_id="span-brief-gene",
    )

    assert nested.owner_ref == root.owner_ref
    assert nested.execution_id == root.execution_id
    assert nested.fingerprint == root.fingerprint
    assert nested.root_span_id == root.root_span_id
    assert nested.parent_span_id == "span-root"
    assert nested.current_span_id == "span-brief-gene"
    assert nested.agent.slug == "brief_gene"


def test_driver_outcome_and_result_contract_are_finite() -> None:
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOutcome,
        ExecutionArtifactRef,
        TransportNeutralResult,
    )

    result = TransportNeutralResult(
        answer="bounded answer",
        follow_up_questions=("next?",),
        artifacts=(
            ExecutionArtifactRef(
                role="report", target_kind="report", target_id="report-1"
            ),
        ),
    )
    outcome = DriverOutcome.succeeded(result)

    assert outcome.status.value == "succeeded"
    assert outcome.terminal is True
    assert outcome.result == result
    assert DriverOutcome.running().terminal is False
    with pytest.raises(ValueError):
        DriverOutcome.failed(code="")


def test_driver_protocol_normalizes_operations_and_services() -> None:
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOperation,
        DriverOutcome,
        ExecutionContext,
        ExecutionDriver,
        ExecutionServices,
    )

    class FakeDriver:
        async def execute(
            self,
            operation,
            context: ExecutionContext,
            command,
            services: ExecutionServices,
        ) -> DriverOutcome:
            del operation, context, command, services
            return DriverOutcome.running()

    assert isinstance(FakeDriver(), ExecutionDriver)
    assert {operation.value for operation in DriverOperation} == {
        "start",
        "resume",
        "cancel",
        "recover",
        "reconcile",
    }


def test_terminal_settlement_authority_is_revision_bound() -> None:
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        TerminalSettlementAuthority,
    )

    authority = TerminalSettlementAuthority(
        owner_ref="owner-1",
        execution_id="turn-1",
        expected_revision=3,
        actor="runtime",
        issued_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    assert authority.expected_revision == 3
    with pytest.raises(ValueError):
        TerminalSettlementAuthority(
            owner_ref="owner-1",
            execution_id="turn-1",
            expected_revision=-1,
            actor="runtime",
            issued_at=datetime(2026, 8, 20, tzinfo=UTC),
        )
