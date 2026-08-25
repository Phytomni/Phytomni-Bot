# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""A trace boundary observes every canonical handler without changing work."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.support.all_agent_runtime_cases import REAL_HANDLER_FIXTURES

from mcp_server_phytomni.mcp import app as mcp_app
from mcp_server_phytomni.mcp import handlers
from mcp_server_phytomni.public_agent_catalog import PUBLIC_AGENT_CATALOG
from mcp_server_phytomni.runtime import submit_recorder
from mcp_server_phytomni.runtime.execution_instrumentation_v2 import (
    bind_execution_boundary,
    instrument_tool_invocation,
)
from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    ExecutionCommand,
    ExecutionContext,
    ExecutionServices,
)
from mcp_server_phytomni.runtime.execution_work_store_v2 import (
    SpanSpec,
    SQLiteExecutionWorkRepository,
)
from mcp_server_phytomni.runtime.request_context import request_context

pytestmark = pytest.mark.server


def _collect_values(value: object, key: str) -> tuple[object, ...]:
    values: list[object] = []
    if isinstance(value, dict):
        for field, nested in value.items():
            if field == key:
                values.append(deepcopy(nested))
            values.extend(_collect_values(nested, key))
    elif isinstance(value, list):
        for nested in value:
            values.extend(_collect_values(nested, key))
    return tuple(values)


def _business_observation(spec, result: object) -> dict[str, object]:
    """Freeze every business/result surface that trace must not rewrite."""
    resource_fields = (
        "role",
        "name",
        "filename",
        "media_type",
        "target",
        "target_binding",
        "size_bytes",
        "bytes",
    )
    return {
        "handler_return": deepcopy(result),
        "assistant_content": _collect_values(result, "content"),
        "references": _collect_values(result, "doc_list"),
        "resource_facts": {
            field: _collect_values(result, field) for field in resource_fields
        },
        "terminal_settlement": (
            "running" if spec.lifecycle == "asynchronous" else "succeeded"
        ),
    }


@pytest.mark.parametrize(
    "spec", PUBLIC_AGENT_CATALOG, ids=lambda item: item.slug
)
async def test_trace_boundary_preserves_real_handler_business_contract(
    spec,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trace-on/off calls have identical results and domain side effects."""
    dependency, arguments, expected = REAL_HANDLER_FIXTURES[spec.slug]
    variant = "off"
    provider_calls: dict[str, list[dict[str, object]]] = {
        "off": [],
        "on": [],
    }
    provider_side_effects = {"off": 0, "on": 0}
    submission_records: dict[str, list[tuple[str, object]]] = {
        "off": [],
        "on": [],
    }

    async def deterministic_provider(**kwargs: object) -> dict[str, object]:
        provider_calls[variant].append(kwargs)

        async def side_effect() -> dict[str, object]:
            provider_side_effects[variant] += 1
            return deepcopy(expected)

        return await instrument_tool_invocation(spec.tool, side_effect)

    def record_submission(result: object, *, agent: str) -> None:
        submission_records[variant].append((agent, deepcopy(result)))

    monkeypatch.setattr(handlers, dependency, deterministic_provider)
    monkeypatch.setattr(
        handlers,
        "scratch_server_dir",
        lambda _config, scope: f"/safe/scratch/{scope}",
    )
    monkeypatch.setattr(
        submit_recorder,
        "record_submitted_task",
        record_submission,
    )
    monkeypatch.setenv("TASKS_DB_PATH", str(tmp_path / "tasks.db"))

    argument_model = mcp_app.TOOL_ARGUMENT_MODELS[spec.tool]
    handler = getattr(handlers, spec.handler)
    with request_context("alice", "request-business-parity", "business-run"):
        result_without_trace = await handler(argument_model(**arguments))

    db_path = str(tmp_path / f"trace-{spec.slug}.db")
    reservations = SQLiteExecutionReservationRepository(db_path)
    journal = SQLiteExecutionJournal(db_path)
    work = SQLiteExecutionWorkRepository(db_path)
    reservation = reservations.reserve(
        owner="alice",
        execution_id=f"execution-trace-parity-{spec.slug}",
        fingerprint_version=1,
        fingerprint=(spec.slug[0] * 64),
        command=ExecutionCommand(
            agent_slug=spec.slug,
            arguments=dict(arguments),
        ),
    )
    work.create_span(
        SpanSpec(
            owner=reservation.owner,
            execution_id=reservation.execution_id,
            span_id=reservation.root_span_id,
            kind="agent",
            label_key=f"agent.{spec.slug}",
        )
    )
    context = ExecutionContext(
        owner_ref=reservation.owner,
        execution_id=reservation.execution_id,
        fingerprint_version=reservation.fingerprint_version,
        fingerprint=reservation.fingerprint,
        agent=spec,
        root_span_id=reservation.root_span_id,
        current_span_id=reservation.root_span_id,
        transport="test",
        run_id=reservation.run_id,
        deadline_at=datetime.fromisoformat(reservation.deadline_at),
    )
    services = ExecutionServices(
        journal=journal,
        reservations=reservations,
        work=work,
        clock=lambda: datetime.now(UTC),
    )

    variant = "on"
    with (
        request_context(
            "alice",
            "request-business-parity",
            "business-run",
        ),
        bind_execution_boundary(context, services),
    ):
        result_with_trace = await handler(argument_model(**arguments))

    assert result_with_trace == result_without_trace == expected
    assert _business_observation(spec, result_with_trace) == (
        _business_observation(spec, result_without_trace)
    )
    assert provider_side_effects == {"off": 1, "on": 1}
    assert provider_calls["on"] == provider_calls["off"]
    assert submission_records["on"] == submission_records["off"]

    projection = journal.get_projection(
        reservation.execution_id,
        owner=reservation.owner,
    )
    assert [
        operation.operation_key for operation in projection.operations
    ] == [f"tool.{spec.slug}"]
    assert projection.operations[0].status == "succeeded"
