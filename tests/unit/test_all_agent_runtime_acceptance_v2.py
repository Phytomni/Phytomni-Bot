# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Every public Agent is admitted, driven, journaled, and settled once."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from tests.support.all_agent_runtime_cases import REAL_HANDLER_FIXTURES

from mcp_server_phytomni.public_agent_catalog import PUBLIC_AGENT_CATALOG


@pytest.mark.parametrize(
    ("spec", "transport"),
    [
        (spec, transport)
        for spec in PUBLIC_AGENT_CATALOG
        for transport in spec.transport_views
    ],
    ids=[
        f"real-{spec.slug}-{transport}"
        for spec in PUBLIC_AGENT_CATALOG
        for transport in spec.transport_views
    ],
)
@pytest.mark.asyncio
async def test_real_canonical_handler_enters_runtime_for_every_public_view(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spec,
    transport: str,
) -> None:
    """Invoke the registered real handler while faking only its provider edge.

    This deliberately leaves ``TOOL_HANDLERS``, schema validation, tool
    instrumentation, Runtime reservation/Driver selection, Todo declaration,
    remote-submission recording, and result formatting intact.  Replacing the
    catalog callback itself would only prove the wrapper and previously hid
    handler bypasses.
    """
    from mcp_server_phytomni.mcp import app as mcp_app
    from mcp_server_phytomni.mcp import handlers
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        SQLiteExecutionReservationRepository,
    )
    from mcp_server_phytomni.runtime.request_context import request_context

    dependency, arguments, expected = REAL_HANDLER_FIXTURES[spec.slug]
    calls: list[dict[str, object]] = []

    async def deterministic_provider(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return expected

    monkeypatch.setattr(handlers, dependency, deterministic_provider)
    monkeypatch.setattr(
        handlers,
        "scratch_server_dir",
        lambda _config, scope: str(tmp_path / scope),
    )
    db_path = tmp_path / f"real-{spec.slug}-{transport}.sqlite"
    execution_id = f"turn-real-{spec.slug}-{transport}"

    with request_context("alice", f"request-{spec.slug}-{transport}"):
        result = await mcp_app.invoke_tool_raw(
            spec.tool,
            arguments,
            db_path=str(db_path),
            execution_id=execution_id,
            transport=transport,
        )

    assert result == expected
    assert len(calls) == 1
    reservation = SQLiteExecutionReservationRepository(str(db_path)).get(
        owner="alice", execution_id=execution_id
    )
    assert reservation.driver == spec.driver
    assert reservation.status.value == (
        "running" if spec.lifecycle == "asynchronous" else "succeeded"
    )
    page = SQLiteExecutionJournal(str(db_path)).list_events(
        execution_id, owner="alice", limit=200
    )
    assert page is not None
    types = [event.type.value for event in page.items]
    assert types[0] == "execution.admitted"
    assert "todo.snapshot" not in types
    assert types.count("work_unit.attempt_started") == 1, types
    assert types.count("work_unit.succeeded") == 1, types
    assert types.count("execution.succeeded") == (
        0 if spec.lifecycle == "asynchronous" else 1
    )


@pytest.mark.parametrize(
    "spec", PUBLIC_AGENT_CATALOG, ids=lambda item: item.slug
)
def test_every_public_agent_uses_runtime_reservation_driver_and_terminal(
    tmp_path: Path, spec
) -> None:
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        SQLiteExecutionReservationRepository,
    )

    calls = 0

    async def business_call():
        nonlocal calls
        calls += 1
        return {"agent": spec.slug, "unchanged": True}

    db_path = tmp_path / f"all-agent-{spec.slug}.db"
    execution_id = f"turn-all-{spec.slug}"
    value = asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id=execution_id,
            agent_slug=spec.slug,
            arguments={"query": "characterized-input"},
            transport="acceptance",
            call=business_call,
        )
    )
    assert value == {"agent": spec.slug, "unchanged": True}
    assert calls == 1
    reservation = SQLiteExecutionReservationRepository(str(db_path)).get(
        owner="alice", execution_id=execution_id
    )
    assert reservation.driver == spec.driver
    assert reservation.status.value == "succeeded"
    page = SQLiteExecutionJournal(str(db_path)).list_events(
        execution_id, owner="alice", limit=30
    )
    assert page is not None
    types = [event.type.value for event in page.items]
    assert types[0] == "execution.admitted"
    assert types.count("execution.succeeded") == 1
    assert types.count("span.succeeded") == 1


@pytest.mark.parametrize(
    ("spec", "transport"),
    [
        (spec, transport)
        for spec in PUBLIC_AGENT_CATALOG
        for transport in spec.transport_views
    ],
    ids=[
        f"{spec.slug}-{transport}"
        for spec in PUBLIC_AGENT_CATALOG
        for transport in spec.transport_views
    ],
)
def test_every_declared_agent_transport_uses_the_same_runtime_driver(
    tmp_path: Path, spec, transport: str
) -> None:
    """A catalog transport is a view, never a second Agent implementation."""
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        SQLiteExecutionReservationRepository,
    )

    execution_id = f"turn-{spec.slug}-{transport}"
    db_path = tmp_path / f"{spec.slug}-{transport}.db"
    calls = 0

    async def business_call():
        nonlocal calls
        calls += 1
        return {"agent": spec.slug, "transport_view": transport}

    value = asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id=execution_id,
            agent_slug=spec.slug,
            arguments={"query": "characterized-input"},
            transport=transport,
            call=business_call,
        )
    )

    assert value == {"agent": spec.slug, "transport_view": transport}
    assert calls == 1
    reservation = SQLiteExecutionReservationRepository(str(db_path)).get(
        owner="alice", execution_id=execution_id
    )
    assert reservation.driver == spec.driver
    assert reservation.status.value == "succeeded"
    page = SQLiteExecutionJournal(str(db_path)).list_events(
        execution_id, owner="alice", limit=30
    )
    assert page is not None
    assert [event.type.value for event in page.items].count(
        "execution.succeeded"
    ) == 1
