# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""A trace boundary observes every canonical handler without changing work."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from tests.support.all_agent_runtime_cases import REAL_HANDLER_FIXTURES
from tests.support.provider_trace_v2 import build_execution_trace_boundary

from mcp_server_phytomni.mcp import app as mcp_app
from mcp_server_phytomni.mcp import handlers
from mcp_server_phytomni.public_agent_catalog import PUBLIC_AGENT_CATALOG
from mcp_server_phytomni.runtime import submit_recorder
from mcp_server_phytomni.runtime.execution_instrumentation_v2 import (
    bind_execution_boundary,
    instrument_tool_invocation,
)
from mcp_server_phytomni.runtime.request_context import request_context

pytestmark = pytest.mark.server


@dataclass
class _TraceObservations:
    """Observations split between trace-disabled and trace-enabled runs."""

    variant: str = "off"
    provider_calls: dict[str, list[dict[str, object]]] = field(
        default_factory=lambda: {"off": [], "on": []}
    )
    provider_side_effects: dict[str, int] = field(
        default_factory=lambda: {"off": 0, "on": 0}
    )
    submission_records: dict[str, list[tuple[str, object]]] = field(
        default_factory=lambda: {"off": [], "on": []}
    )


def _collect_values(value: object, key: str) -> tuple[object, ...]:
    values: list[object] = []
    if isinstance(value, dict):
        for field_name, nested in value.items():
            if field_name == key:
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
            resource_field: _collect_values(result, resource_field)
            for resource_field in resource_fields
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
    observations = _TraceObservations()

    async def deterministic_provider(**kwargs: object) -> dict[str, object]:
        observations.provider_calls[observations.variant].append(kwargs)

        async def side_effect() -> dict[str, object]:
            observations.provider_side_effects[observations.variant] += 1
            return deepcopy(expected)

        return await instrument_tool_invocation(spec.tool, side_effect)

    def record_submission(result: object, *, agent: str) -> None:
        observations.submission_records[observations.variant].append(
            (agent, deepcopy(result))
        )

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

    boundary = build_execution_trace_boundary(
        str(tmp_path / f"trace-{spec.slug}.db"),
        spec=spec,
        execution_id=f"execution-trace-parity-{spec.slug}",
        fingerprint=spec.slug[0] * 64,
        arguments=dict(arguments),
    )

    observations.variant = "on"
    with (
        request_context(
            "alice",
            "request-business-parity",
            "business-run",
        ),
        bind_execution_boundary(boundary.context, boundary.services),
    ):
        result_with_trace = await handler(argument_model(**arguments))

    assert result_with_trace == result_without_trace == expected
    assert _business_observation(spec, result_with_trace) == (
        _business_observation(spec, result_without_trace)
    )
    assert observations.provider_side_effects == {"off": 1, "on": 1}
    assert (
        observations.provider_calls["on"] == observations.provider_calls["off"]
    )
    assert (
        observations.submission_records["on"]
        == observations.submission_records["off"]
    )

    projection = boundary.journal.get_projection(
        boundary.reservation.execution_id,
        owner=boundary.reservation.owner,
    )
    assert [
        operation.operation_key for operation in projection.operations
    ] == [f"tool.{spec.slug}"]
    assert projection.operations[0].status == "succeeded"
