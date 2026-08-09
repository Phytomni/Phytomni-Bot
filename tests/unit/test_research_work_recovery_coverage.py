# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Small direct tests for Research recovery contract guards."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from tests.unit.test_research_work_recovery import (
    _NOW,
    _Provider,
    _record,
    _store,
)

from mcp_server_phytomni.agents.research import recovery as module

pytestmark = pytest.mark.unit


def test_recovery_output_and_identity_guards() -> None:
    """Provider output and request identity are bounded before persistence."""
    contract_error = getattr(module, "_WorkContractError")
    normalise = getattr(module, "_normalise_output")
    with pytest.raises(contract_error):
        normalise([])
    with pytest.raises(contract_error):
        normalise({"private": object()})

    record = _record(provider_request_digest="provider-digest")
    identity = getattr(module, "_request_identity")
    assert identity(record) == "provider-digest"
    derived = identity(_record(provider_request_digest=None))
    assert len(derived) == 64


def test_query_projection_accepts_success_and_rejects_invalid_contracts() -> (
    None
):
    """Status wrappers only expose successful, serialisable result mappings."""
    query_payload = getattr(module, "_query_payload")
    assert query_payload({"status": "FAILED"}) is None
    assert query_payload({"status": "success", "result": {"ok": 1}}) == {
        "ok": 1
    }
    assert query_payload({"status": "success", "result": object()}) is None


@pytest.mark.asyncio
async def test_recovery_await_helper_handles_sync_and_async_values() -> None:
    """Injected provider seams may be synchronous or awaitable."""
    maybe_await = getattr(module, "_maybe_await")
    assert await maybe_await("sync") == "sync"

    async def value() -> str:
        return "async"

    assert await maybe_await(value()) == "async"


def test_recovery_option_factories_reject_unknown_keys() -> None:
    """Historical keyword adapters remain closed to typoed options."""
    with pytest.raises(TypeError, match="unexpected executor"):
        getattr(module, "_ExecutorOptions").from_kwargs({"typo": True})
    with pytest.raises(TypeError, match="unexpected recovery"):
        getattr(module, "_RecoveryOptions").from_kwargs({"typo": True})


def test_executor_and_service_reject_mixed_option_surfaces(
    tmp_path: Path,
) -> None:
    """Typed option bundles cannot be combined with legacy keywords."""
    store = _store(tmp_path)
    executor_options = getattr(module, "_ExecutorOptions")(now=lambda: _NOW)
    with pytest.raises(TypeError, match="options cannot"):
        module.ResearchWorkExecutor(
            store,
            _Provider(),
            options=executor_options,
            now=lambda: _NOW,
        )
    recovery_options = getattr(module, "_RecoveryOptions")(now=lambda: _NOW)
    with pytest.raises(TypeError, match="options cannot"):
        module.ResearchRecoveryService(
            store,
            _Provider(),
            options=recovery_options,
            batch_size=1,
        )
    assert getattr(module, "_utc_now")().tzinfo is not None


@pytest.mark.asyncio
async def test_executor_missing_row_and_preparation_failure_are_terminal(
    tmp_path: Path,
) -> None:
    """Missing rows and local preparation errors never reach provider I/O."""
    store = _store(tmp_path)
    executor = module.ResearchWorkExecutor(
        store, _Provider(), now=lambda: _NOW
    )
    missing = await executor.execute("missing-unit", "worker")
    assert missing.state == "terminal_failed"

    store.add_work_unit(_record())

    async def fail_input(_record: object) -> object:
        raise RuntimeError("private input detail")

    failed = await module.ResearchWorkExecutor(
        store,
        _Provider(),
        now=lambda: _NOW,
        work_input=cast(Any, fail_input),
    ).execute("unit-1", "worker")
    assert failed.state == "terminal_failed"


@pytest.mark.asyncio
async def test_provider_exception_stays_ambiguous_after_sent_mark(
    tmp_path: Path,
) -> None:
    """Unknown provider acceptance is left for identity-based recovery."""
    store = _store(tmp_path)
    store.add_work_unit(_record())

    class CrashingProvider(_Provider):
        """Provider fixture that fails after the durable sent transition."""

        async def invoke(
            self, *_args: object, **_kwargs: object
        ) -> dict[str, object]:
            raise RuntimeError("transport detail")

    outcome = await module.ResearchWorkExecutor(
        store,
        CrashingProvider(),
        now=lambda: _NOW,
    ).execute("unit-1", "worker")
    assert outcome.state == "ambiguous"


@pytest.mark.asyncio
async def test_sent_recovery_query_without_identity_or_provider_is_safe(
    tmp_path: Path,
) -> None:
    """Recovery does not invent a provider call when identity is absent."""
    store = _store(tmp_path)
    service = module.ResearchRecoveryService(
        store, _Provider(), now=lambda: _NOW
    )
    assert await getattr(service, "_query_and_validate")(_record()) is None

    service = module.ResearchRecoveryService(
        store,
        cast(Any, SimpleNamespace(query=lambda _identity: None)),
        now=lambda: _NOW,
    )
    record = _record(provider_request_digest="request-1")
    assert await getattr(service, "_query_and_validate")(record) is None


def test_recovery_candidate_list_is_bounded(tmp_path: Path) -> None:
    """The storage scan short-circuits before opening a transaction at zero."""
    assert (
        getattr(module, "_list_recovery_candidates")(
            _store(tmp_path), datetime.now(UTC), 0
        )
        == []
    )
