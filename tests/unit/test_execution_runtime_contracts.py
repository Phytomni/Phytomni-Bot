# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Transport-neutral contracts at the execution Runtime/Driver boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from tests.support.execution_dispatch_fixtures import (
    execution_context_fixture,
)

from mcp_server_phytomni.public_agent_catalog import PUBLIC_AGENT_CATALOG
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    DriverOperation,
    DriverOutcome,
    ExecutionArtifactRef,
    ExecutionContext,
    ExecutionDriver,
    ExecutionServices,
    TerminalSettlementAuthority,
    TransportNeutralResult,
)


def test_context_keeps_identity_across_nested_invocation() -> None:
    """Verify context keeps identity across nested invocation."""

    spec = next(
        item for item in PUBLIC_AGENT_CATALOG if item.slug == "research"
    )
    root = execution_context_fixture(
        spec,
        "owner-1",
        "turn-1",
        "http",
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
    """Verify driver outcome and result contract are finite."""

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
    """Verify driver protocol normalizes operations and services."""

    @dataclass(eq=False)
    class FakeDriver:
        """Driver double that accepts every normalized operation."""

        async def execute(
            self,
            operation,
            context: ExecutionContext,
            command,
            services: ExecutionServices,
        ) -> DriverOutcome:
            """Return this test driver's configured outcome."""
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
    """Verify terminal settlement authority is revision bound."""

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
