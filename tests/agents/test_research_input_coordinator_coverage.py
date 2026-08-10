# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Boundary coverage for the coordinator's adapter-only helper seams."""

from __future__ import annotations

from types import MappingProxyType, SimpleNamespace
from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.research import input_coordinator as module
from mcp_server_phytomni.agents.research.input_contracts import (
    ResearchCoordinatorDependencies,
)
from mcp_server_phytomni.agents.research.input_preparation import (
    PreparedResearchInput,
)
from tests.agents.test_research_input_coordinator import _Harness, _request

pytestmark = pytest.mark.agent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method",
    ("_metadata", "_extract", "_resolve", "_revalidate"),
)
async def test_missing_coordinator_callback_fails_closed(method: str) -> None:
    """Every required callback has the same safe failure boundary."""
    request = _request(_Harness())
    coordinator = module.ResearchInputCoordinator(request)
    with pytest.raises(Exception) as caught:
        await getattr(coordinator, method)(
            ResearchCoordinatorDependencies(), request
        )
    assert getattr(caught.value, "code", None) == (
        "research_input_resolution_failed"
    )


def test_join_rejects_callback_errors_and_invalid_projection() -> None:
    """The join seam rejects arbitrary callback values."""
    request = _request(_Harness())

    def broken(_inventory: object, _resolution: object) -> object:
        raise ValueError("private join detail")

    for callback in (broken, lambda _inventory, _resolution: object()):
        coordinator = module.ResearchInputCoordinator(
            request,
            dependencies=ResearchCoordinatorDependencies(
                join_prepared=callback
            ),
        )
        with pytest.raises(Exception) as caught:
            getattr(coordinator, "_join")(
                coordinator.dependencies, object(), object()
            )
        assert getattr(caught.value, "code", None) == (
            "research_input_resolution_failed"
        )


@pytest.mark.asyncio
async def test_plan_builder_failures_are_staged_as_planning() -> None:
    """Injected synchronous and asynchronous planner errors are sanitized."""
    request = _request(_Harness())

    def broken(_prepared: object, _request: object) -> object:
        raise RuntimeError("planner private detail")

    coordinator = module.ResearchInputCoordinator(request, plan_builder=broken)
    with pytest.raises(Exception) as caught:
        await getattr(coordinator, "_build_plan")(cast(Any, object()), request)
    assert getattr(caught.value, "last_stage", None) == "planning"

    async def broken_async(_prepared: object, _request: object) -> object:
        raise ValueError("planner async detail")

    coordinator = module.ResearchInputCoordinator(
        request, plan_builder=broken_async
    )
    with pytest.raises(Exception) as caught:
        await getattr(coordinator, "_build_plan")(cast(Any, object()), request)
    assert getattr(caught.value, "last_stage", None) == "planning"


def test_port_aliases_reject_unknown_names() -> None:
    """Adapter aliases remain closed to accidental dependency injection."""
    with pytest.raises(TypeError, match="unexpected Research"):
        getattr(module, "_dependencies_from_ports")({"unexpected": object()})


@pytest.mark.asyncio
async def test_sqlite_resume_loader_validates_store_rows_and_factory() -> None:
    """Restart loaders reject malformed durable metadata."""
    with pytest.raises(TypeError):
        module.build_sqlite_resume_loader(
            object(), cast(Any, lambda _metadata: None)
        )
    with pytest.raises(TypeError):
        module.build_sqlite_resume_loader(
            SimpleNamespace(load_resolution=lambda _run_id: None),
            cast(Any, object()),
        )

    def store_for(row_value: object) -> Any:
        """Bind one malformed row to the loader storage seam."""

        def load(_run_id: str) -> object:
            """Return the bound malformed row."""
            return row_value

        return SimpleNamespace(load_resolution=load)

    rows: tuple[object, ...] = ("invalid", {"source_map_json": {}})
    for row in rows:
        loader = module.build_sqlite_resume_loader(
            store_for(row),
            cast(Any, lambda _metadata: object()),
        )
        with pytest.raises(TypeError):
            await loader("run-1")


def test_evidence_identity_rejects_non_sequence_metadata() -> None:
    """Persisted evidence projections must retain sequence-shaped IDs."""
    assert getattr(module, "_evidence_identity")({"units": "bad"}) is None
    assert (
        getattr(module, "_evidence_identity")(
            {
                "units": ({"dataset_ids": "bad"},),
                "document_digests": (),
                "coverage_digest": "d",
            }
        )
        is None
    )
    assert (
        getattr(module, "_evidence_identity")(
            {
                "units": (),
                "document_digests": ({"evidence_ids": "bad"},),
                "coverage_digest": "d",
            }
        )
        is None
    )


@pytest.mark.asyncio
async def test_dispatch_records_and_native_empty_query_are_guarded() -> None:
    """Dispatch and native validation retain their safe terminal boundaries."""
    request = _request(_Harness())
    coordinator = module.ResearchInputCoordinator(request)
    await getattr(coordinator, "_dispatch_records")((), "worker")

    async def dispatch_once(_dispatch_id: str, _owner: str) -> Any:
        return SimpleNamespace(state="ambiguous")

    coordinator.outbox = SimpleNamespace(dispatch_once=dispatch_once)
    with pytest.raises(Exception) as caught:
        await getattr(coordinator, "_dispatch_records")(
            (SimpleNamespace(run_id="run-001", dispatch_id="dispatch-1"),),
            "worker",
        )
    assert (
        getattr(caught.value, "code", None) == "research_run_tracking_failed"
    )

    empty = PreparedResearchInput(
        effective_query=" ",
        obs_file_list=(),
        data_list=MappingProxyType({}),
        inventory_digest="i",
        evidence_digest="e",
        execution_fingerprint="x",
        authority_ids=(),
    )
    with pytest.raises(ValueError):
        getattr(module, "_validate_native_payload")(empty)

    uploaded = empty.__class__(
        effective_query="",
        obs_file_list=("obs://managed/document.pdf",),
        data_list=MappingProxyType({}),
        inventory_digest="i",
        evidence_digest="e",
        execution_fingerprint="x",
        authority_ids=(),
    )
    getattr(module, "_validate_native_payload")(uploaded)
