# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Remote/fan-out/hybrid Agents share durable Runtime semantics."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest
from tests.support.execution_dispatch_fixtures import run_migrated_agent

from mcp_server_phytomni.config.models.agents import DigitalDesignConfig
from mcp_server_phytomni.public_agent_catalog import (
    PUBLIC_AGENT_CATALOG,
    public_agent_spec,
)
from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_journal_v2 import WorkUnitStatus
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    ExecutionCommand,
)
from mcp_server_phytomni.runtime.execution_work_store_v2 import (
    SpanSpec,
    SQLiteExecutionWorkRepository,
    WorkUnitSpec,
)
from mcp_server_phytomni.runtime.fanout_join_v2 import reduce_fanout_join


@pytest.mark.parametrize(
    ("slug", "driver", "topology", "join"),
    [
        ("analyst", "remote_task", "serial", "not_applicable"),
        ("design", "remote_fanout", "parallel", "best_effort"),
        ("network", "remote_fanout", "parallel", "best_effort"),
        ("deep_genome", "hybrid", "hybrid", "best_effort"),
        ("research", "hybrid", "hybrid", "best_effort"),
    ],
)
def test_remote_cohort_catalog_declares_canonical_driver_and_join(
    slug: str, driver: str, topology: str, join: str
) -> None:
    """Verify remote cohort catalog declares canonical driver and join."""

    spec = public_agent_spec(slug)
    assert spec is not None
    assert (spec.driver, spec.topology, spec.join) == (driver, topology, join)
    assert spec.max_attempts == 3
    assert spec.retry == "provider_bounded"


def test_result_delivering_analysis_agents_cover_provider_job_timeout() -> (
    None
):
    """Verify result delivering analysis agents cover provider job timeout."""

    provider_timeout = int(DigitalDesignConfig().ANALYSIS_JOB_TIMEOUT)
    settlement_grace = 3600
    affected = [
        spec
        for spec in PUBLIC_AGENT_CATALOG
        if spec.result_delivery
        and "analysis_task_platform" in spec.remote_providers
    ]

    assert {spec.slug for spec in affected} == {
        "analyst",
        "design",
        "network",
        "research",
    }
    assert all(
        spec.deadline_seconds >= provider_timeout + settlement_grace
        for spec in affected
    )


@pytest.mark.parametrize(
    "slug", ["analyst", "design", "network", "deep_genome", "research"]
)
def test_remote_submission_is_admitted_once_and_replayed_from_journal(
    tmp_path: Path, slug: str
) -> None:
    """Verify remote submission is admitted once and replayed from journal."""

    calls = 0
    envelope = {"task_id": f"private-{slug}-task", "status": "running"}

    async def submit():
        nonlocal calls
        calls += 1
        return envelope, 202

    db_path = tmp_path / f"remote-{slug}.db"
    execution_id = f"turn-remote-{slug}"
    result = run_migrated_agent(
        db_path,
        execution_id,
        slug,
        "background",
        submit,
    )
    assert result == (envelope, 202)
    assert calls == 1
    record = SQLiteExecutionReservationRepository(str(db_path)).get(
        owner="alice", execution_id=execution_id
    )
    assert record.status.value == "running"
    assert record.driver in {"remote_task", "remote_fanout", "hybrid"}
    projection = SQLiteExecutionJournal(str(db_path)).get_projection(
        execution_id, owner="alice"
    )
    assert projection.status.value == "running"
    assert "private-" not in str(projection.model_dump(mode="json"))


def test_fanout_join_is_order_independent_and_preserves_partial_results(
    tmp_path: Path,
) -> None:
    """Verify fanout join is order independent and preserves partial
    results."""

    db_path = str(tmp_path / "fanout.db")
    reservations = SQLiteExecutionReservationRepository(db_path)
    SQLiteExecutionJournal(db_path)
    work = SQLiteExecutionWorkRepository(db_path)
    reservation = reservations.reserve(
        owner="alice",
        execution_id="turn-fanout",
        fingerprint_version=1,
        fingerprint="a" * 64,
        command=ExecutionCommand(agent_slug="design", arguments={}),
    )
    work.create_span(
        SpanSpec(
            owner="alice",
            execution_id="turn-fanout",
            span_id=reservation.root_span_id,
            kind="agent",
            label_key="agent.design",
        )
    )
    units = []
    for branch, status in (
        ("branch-z", WorkUnitStatus.SUCCEEDED),
        ("branch-a", WorkUnitStatus.FAILED),
    ):
        unit = work.create_work_unit(
            WorkUnitSpec(
                owner="alice",
                execution_id="turn-fanout",
                work_unit_id=branch,
                parent_span_id=reservation.root_span_id,
                operation_key="design.branch",
                driver="remote_fanout",
                join_policy="best_effort",
            )
        )
        units.append(
            work.update_work_unit_status(
                unit.execution_id,
                unit.work_unit_id,
                owner=unit.owner,
                status=status,
                expected_revision=unit.revision,
            )
        )
    forward = reduce_fanout_join(units, policy="best_effort")
    reverse = reduce_fanout_join(list(reversed(units)), policy="best_effort")
    assert forward == reverse
    assert forward.outcome == "partial"
    assert forward.succeeded == ("branch-z",)
    assert forward.failed == ("branch-a",)


def test_remote_provider_side_effect_has_one_canonical_boundary() -> None:
    """Verify remote provider side effect has one canonical boundary."""
    root = Path(__file__).parents[2] / "src/mcp_server_phytomni"
    occurrences: list[str] = []
    for source in (root / "agents").rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        if "instrument_provider_submission(" in text:
            occurrences.append(source.relative_to(root).as_posix())
    assert occurrences == ["agents/analyst/graph.py"]
    deep_source = (root / "agents/deep_genome/agent.py").read_text(
        encoding="utf-8"
    )
    assert "asyncio.create_task(" not in deep_source
    assert "schedule_public_agent_child_work(" in deep_source


@pytest.mark.parametrize(
    ("policy", "statuses", "quorum", "expected"),
    [
        ("all", ("succeeded", "succeeded"), None, "succeeded"),
        ("all", ("succeeded", "failed"), None, "failed"),
        ("fail_fast", ("failed", "running"), None, "failed"),
        ("best_effort", ("succeeded", "failed"), None, "partial"),
        ("quorum", ("succeeded", "succeeded", "running"), 2, "succeeded"),
        ("quorum", ("failed", "failed", "running"), 2, "failed"),
    ],
)
def test_remote_join_policies_are_finite(
    tmp_path: Path,
    policy: Literal["all", "fail_fast", "best_effort", "quorum"],
    statuses: tuple[str, ...],
    quorum: int | None,
    expected: str,
) -> None:
    """Verify remote join policies are finite."""

    db_path = str(tmp_path / f"join-{policy}-{expected}.db")
    reservations = SQLiteExecutionReservationRepository(db_path)
    SQLiteExecutionJournal(db_path)
    work = SQLiteExecutionWorkRepository(db_path)
    reservation = reservations.reserve(
        owner="alice",
        execution_id="turn-join",
        fingerprint_version=1,
        fingerprint="b" * 64,
        command=ExecutionCommand(agent_slug="design", arguments={}),
    )
    work.create_span(
        SpanSpec(
            owner="alice",
            execution_id="turn-join",
            span_id=reservation.root_span_id,
            kind="agent",
            label_key="agent.design",
        )
    )
    units = []
    for index, status in enumerate(statuses):
        unit = work.create_work_unit(
            WorkUnitSpec(
                owner="alice",
                execution_id="turn-join",
                work_unit_id=f"branch-{index}",
                parent_span_id=reservation.root_span_id,
                operation_key="design.branch",
                driver="remote_fanout",
                join_policy=policy,
            )
        )
        units.append(
            work.update_work_unit_status(
                unit.execution_id,
                unit.work_unit_id,
                owner=unit.owner,
                status=status,
                expected_revision=unit.revision,
            )
        )
    assert (
        reduce_fanout_join(
            units,
            policy=policy,
            quorum=quorum,
        ).outcome
        == expected
    )
