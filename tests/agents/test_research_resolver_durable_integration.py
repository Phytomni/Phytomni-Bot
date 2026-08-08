# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Integration coverage for the concrete Research resolver durability seam."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mcp_server_phytomni.agents.research.description_resolver import (
    ResearchDescriptionResolver,
    _request_input_digest,
    _resolver_request_binding,
)
from mcp_server_phytomni.agents.research.recovery import (
    ResearchWorkBinding,
    ResearchWorkExecutor,
)
from mcp_server_phytomni.runtime.research_input_store import (
    ResearchWorkUnitRecord,
)
from tests.agents.test_research_description_resolver import (
    _RecordingProvider,
    _single_dataset_request,
    _single_observation_output,
)
from tests.unit.test_research_work_recovery import _store

pytestmark = pytest.mark.agent


class _DurableProvider:
    """Provider shape used by the lease-owning executor boundary."""

    def __init__(self, output: Mapping[str, object]) -> None:
        self.output = output
        self.query_calls: list[str] = []
        self.invocations = 0

    async def invoke(self, work_input: object, policy: object) -> object:
        """Reject replacement calls; recovery must query the saved identity."""
        del work_input, policy
        self.invocations += 1
        raise AssertionError("recovery must not invoke a replacement")

    async def query(self, request_identity: str) -> object | None:
        """Return the saved provider result for one opaque request identity."""
        self.query_calls.append(request_identity)
        return self.output


def _succeeded_record(
    binding: ResearchWorkBinding,
    output: Mapping[str, object],
    now: datetime,
) -> ResearchWorkUnitRecord:
    """Build one durable success row from the resolver's current binding."""
    return ResearchWorkUnitRecord(
        unit_id="unit_001",
        run_id="run-1",
        kind="resolve_root",
        state="succeeded",
        input_digest=binding.input_digest,
        policy_digest=binding.policy_digest,
        lease_owner=None,
        lease_expires_at=None,
        attempt=1,
        revision=2,
        provider_request_digest=binding.provider_request_digest,
        provider_idempotency_digest=binding.provider_request_digest,
        execution_fingerprint=binding.execution_fingerprint,
        evidence_digest=binding.evidence_digest,
        output=dict(output),
        sent_at=now - timedelta(seconds=2),
        completed_at=now - timedelta(seconds=1),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    (
        "input_digest",
        "policy_digest",
        "provider_request_digest",
        "execution_fingerprint",
        "evidence_digest",
    ),
)
async def test_stale_success_binding_never_reuses_old_output(
    tmp_path: Path, field: str
) -> None:
    """Every stale durable binding must fail closed before provider I/O."""
    output = _single_observation_output()
    request = _single_dataset_request()
    unit = request.work_plan.observation_units[0]
    current = _resolver_request_binding(request, unit)
    stale = replace(current, **{field: f"stale-{field}"})
    store = _store(tmp_path)
    store.add_work_unit(_succeeded_record(stale, output, datetime.now(UTC)))
    provider = _DurableProvider(output)
    resolver = ResearchDescriptionResolver(
        _RecordingProvider(()),
        store,
        executor=ResearchWorkExecutor(
            store,
            provider,
            result_validator=lambda value, _record: value,
        ),
    )

    with pytest.raises(Exception) as caught:
        await resolver.resolve(request, "resolver-owner")

    assert getattr(caught.value, "code", None) == (
        "research_input_resolution_unavailable"
    )
    assert provider.invocations == 0
    assert not provider.query_calls


@pytest.mark.asyncio
async def test_matching_success_binding_reuses_without_provider_call(
    tmp_path: Path,
) -> None:
    """A success with all current bindings remains a zero-I/O replay."""
    output = _single_observation_output()
    request = _single_dataset_request()
    unit = request.work_plan.observation_units[0]
    binding = _resolver_request_binding(request, unit)
    store = _store(tmp_path)
    store.add_work_unit(_succeeded_record(binding, output, datetime.now(UTC)))
    provider = _DurableProvider(output)
    resolver = ResearchDescriptionResolver(
        _RecordingProvider(()),
        store,
        executor=ResearchWorkExecutor(
            store,
            provider,
            result_validator=lambda value, _record: value,
        ),
    )

    result = await resolver.resolve(request, "resolver-owner")

    assert result.datasets[0].id == "dataset_001"
    assert provider.invocations == 0
    assert not provider.query_calls


@pytest.mark.asyncio
async def test_real_store_executor_resolver_recovers_four_binding_output(
    tmp_path: Path,
) -> None:
    """The resolver projects output recovered through all durable bindings."""
    output = _single_observation_output()
    request = _single_dataset_request()
    store = _store(tmp_path)
    database = store.db_path
    unit = request.work_plan.observation_units[0]
    binding = _resolver_request_binding(request, unit)
    now = datetime.now(UTC)
    store.add_work_unit(
        ResearchWorkUnitRecord(
            unit_id=unit.unit_id,
            run_id="run-1",
            kind="resolve_root",
            state="sent",
            input_digest=_request_input_digest(request, unit),
            policy_digest=binding.policy_digest,
            lease_owner="crashed-worker",
            lease_expires_at=now - timedelta(seconds=1),
            attempt=1,
            revision=1,
            provider_request_digest=binding.provider_request_digest,
            provider_idempotency_digest=binding.provider_request_digest,
            execution_fingerprint=binding.execution_fingerprint,
            evidence_digest=binding.evidence_digest,
            sent_at=now - timedelta(seconds=2),
        )
    )
    resolver_provider = _RecordingProvider(())
    provider = _DurableProvider(output)
    executor = ResearchWorkExecutor(
        store,
        provider,
        result_validator=lambda value, _record: value,
    )
    resolver = ResearchDescriptionResolver(
        resolver_provider,
        store,
        executor=executor,
    )

    result = await resolver.resolve(request, "resolver-owner")

    assert resolver.durable_execution_enabled() is True
    assert result.datasets[0].id == "dataset_001"
    assert provider.query_calls == [binding.provider_request_digest]
    assert provider.invocations == 0
    assert not resolver_provider.requests
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT state, execution_fingerprint, evidence_digest "
            "FROM research_work_units WHERE unit_id = ?",
            (unit.unit_id,),
        ).fetchone()
    assert row == (
        "succeeded",
        binding.execution_fingerprint,
        binding.evidence_digest,
    )
