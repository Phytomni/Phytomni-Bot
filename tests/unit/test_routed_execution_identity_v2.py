# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Fenced canonical identity handoff for Expert-routed executions."""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import pytest

from mcp_server_phytomni.api import app as api_app
from mcp_server_phytomni.api import factory
from mcp_server_phytomni.runtime import execution_entrypoint_v2 as entrypoint
from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
    CanonicalReservationIdentity,
    bind_canonical_reservation_identity,
    invoke_public_agent,
)
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    EXPERT_ROUTER_AGENT_SLUG,
    ExecutionReservationRecord,
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    ExecutionCommand,
    ExecutionRuntimeError,
)

pytestmark = pytest.mark.unit


def _router_command(label: str) -> ExecutionCommand:
    return ExecutionCommand(
        agent_slug=EXPERT_ROUTER_AGENT_SLUG,
        arguments={"__query": label},
    )


def _selected_command(label: str) -> ExecutionCommand:
    return ExecutionCommand(
        agent_slug="design",
        arguments={"user_query": label, "obs_file_list": []},
    )


def _reserve_router(
    db_path: str,
    *,
    owner: str,
    execution_id: str,
    fingerprint: str,
    label: str,
) -> tuple[SQLiteExecutionReservationRepository, ExecutionCommand]:
    repository = SQLiteExecutionReservationRepository(db_path)
    command = _router_command(label)
    repository.reserve(
        owner=owner,
        execution_id=execution_id,
        fingerprint_version=2,
        fingerprint=fingerprint,
        command=command,
    )
    return repository, command


def _routed_scope(
    *,
    db_path: str,
    owner: str,
    execution_id: str,
    command: ExecutionCommand,
) -> AbstractContextManager[ExecutionReservationRecord | None]:
    helper = getattr(entrypoint, "bind_routed_reservation_identity", None)
    assert helper is not None, "routed identity handoff is not implemented"
    return helper(
        db_path=db_path,
        owner=owner,
        execution_id=execution_id,
        command=command,
    )


def test_api_adapters_use_only_the_shared_routed_binding_helper() -> None:
    root = Path(__file__).resolve().parents[2]
    adapter_paths = (
        root / "src/mcp_server_phytomni/api/factory.py",
        root / "src/mcp_server_phytomni/api/app_support.py",
        root / "src/mcp_server_phytomni/api/agent_runs.py",
    )

    for path in adapter_paths:
        source = path.read_text(encoding="utf-8")
        assert ".bind_routed_agent(" not in source, path.name
        assert "bind_routed_reservation_identity" in source, path.name


@pytest.mark.asyncio
async def test_first_routed_binding_starts_selected_runtime_once(
    tmp_path: Path,
) -> None:
    db_path = str(tmp_path / "first-routed-binding.db")
    fingerprint = "1" * 64
    repository, router_command = _reserve_router(
        db_path,
        owner="alice",
        execution_id="turn-first-routed",
        fingerprint=fingerprint,
        label="design rice protein",
    )
    selected = _selected_command("design rice protein")
    calls = 0

    async def business_call() -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"status": "succeeded"}

    outer = CanonicalReservationIdentity(
        owner="alice",
        execution_id="turn-first-routed",
        fingerprint_version=2,
        fingerprint=fingerprint,
        command=router_command,
    )
    with (
        bind_canonical_reservation_identity(outer),
        _routed_scope(
            db_path=db_path,
            owner="alice",
            execution_id="turn-first-routed",
            command=selected,
        ) as bound,
    ):
        assert bound is not None
        result = await invoke_public_agent(
            db_path=db_path,
            owner="alice",
            execution_id="turn-first-routed",
            agent_slug="design",
            arguments={"user_query": "design rice protein"},
            transport="service_dispatcher",
            call=business_call,
            fingerprint_version=2,
            fingerprint=fingerprint,
        )

    current = repository.get(owner="alice", execution_id="turn-first-routed")
    assert result == {"status": "succeeded"}
    assert calls == 1
    assert current.agent_slug == "design"
    assert current.status.value == "succeeded"


def test_matching_routed_binding_reuses_run_and_command_hash(
    tmp_path: Path,
) -> None:
    db_path = str(tmp_path / "matching-routed-replay.db")
    fingerprint = "2" * 64
    repository, router_command = _reserve_router(
        db_path,
        owner="alice",
        execution_id="turn-matching-routed",
        fingerprint=fingerprint,
        label="design maize protein",
    )
    selected = _selected_command("design maize protein")
    outer = CanonicalReservationIdentity(
        owner="alice",
        execution_id="turn-matching-routed",
        fingerprint_version=2,
        fingerprint=fingerprint,
        command=router_command,
    )

    with bind_canonical_reservation_identity(outer):
        with _routed_scope(
            db_path=db_path,
            owner="alice",
            execution_id="turn-matching-routed",
            command=selected,
        ) as first:
            assert first is not None
        with _routed_scope(
            db_path=db_path,
            owner="alice",
            execution_id="turn-matching-routed",
            command=selected,
        ) as replay:
            assert replay is not None

    current = repository.get(
        owner="alice", execution_id="turn-matching-routed"
    )
    assert replay.run_id == first.run_id == current.run_id
    assert replay.command_hash == first.command_hash == current.command_hash
    assert current.status.value == "admitted"


@pytest.mark.parametrize("mismatch", ["agent", "arguments"])
def test_routed_binding_rejects_changed_selected_command(
    tmp_path: Path,
    mismatch: str,
) -> None:
    db_path = str(tmp_path / f"changed-routed-{mismatch}.db")
    fingerprint = "3" * 64
    repository, router_command = _reserve_router(
        db_path,
        owner="alice",
        execution_id="turn-changed-routed",
        fingerprint=fingerprint,
        label="design wheat protein",
    )
    selected = _selected_command("design wheat protein")
    changed = (
        ExecutionCommand(agent_slug="chat", arguments=dict(selected.arguments))
        if mismatch == "agent"
        else _selected_command("design a different protein")
    )
    outer = CanonicalReservationIdentity(
        owner="alice",
        execution_id="turn-changed-routed",
        fingerprint_version=2,
        fingerprint=fingerprint,
        command=router_command,
    )

    with bind_canonical_reservation_identity(outer):
        with _routed_scope(
            db_path=db_path,
            owner="alice",
            execution_id="turn-changed-routed",
            command=selected,
        ):
            pass
        with (
            pytest.raises(ExecutionRuntimeError),
            _routed_scope(
                db_path=db_path,
                owner="alice",
                execution_id="turn-changed-routed",
                command=changed,
            ),
        ):
            pass

    current = repository.get(owner="alice", execution_id="turn-changed-routed")
    assert current.agent_slug == "design"


@pytest.mark.parametrize("mismatch", ["owner", "fingerprint"])
def test_routed_binding_rejects_outer_authority_mismatch(
    tmp_path: Path,
    mismatch: str,
) -> None:
    db_path = str(tmp_path / f"routed-authority-{mismatch}.db")
    fingerprint = "4" * 64
    _repository, router_command = _reserve_router(
        db_path,
        owner="alice",
        execution_id="turn-routed-authority",
        fingerprint=fingerprint,
        label="design barley protein",
    )
    outer = CanonicalReservationIdentity(
        owner="mallory" if mismatch == "owner" else "alice",
        execution_id="turn-routed-authority",
        fingerprint_version=2,
        fingerprint="5" * 64 if mismatch == "fingerprint" else fingerprint,
        command=router_command,
    )

    with (
        bind_canonical_reservation_identity(outer),
        pytest.raises(ExecutionRuntimeError),
        _routed_scope(
            db_path=db_path,
            owner="alice",
            execution_id="turn-routed-authority",
            command=_selected_command("design barley protein"),
        ),
    ):
        pass


@pytest.mark.asyncio
async def test_routed_identity_scope_restores_outer_identity_after_exception(
    tmp_path: Path,
) -> None:
    db_path = str(tmp_path / "routed-scope-reset.db")
    fingerprint = "6" * 64
    _repository, router_command = _reserve_router(
        db_path,
        owner="alice",
        execution_id="turn-routed-reset",
        fingerprint=fingerprint,
        label="design sorghum protein",
    )
    selected = _selected_command("design sorghum protein")
    outer = CanonicalReservationIdentity(
        owner="alice",
        execution_id="turn-routed-reset",
        fingerprint_version=2,
        fingerprint=fingerprint,
        command=router_command,
    )
    calls = 0

    async def business_call() -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"status": "succeeded"}

    with bind_canonical_reservation_identity(outer):
        with (
            pytest.raises(RuntimeError, match="selected failure"),
            _routed_scope(
                db_path=db_path,
                owner="alice",
                execution_id="turn-routed-reset",
                command=selected,
            ),
        ):
            raise RuntimeError("selected failure")
        with pytest.raises(ExecutionRuntimeError):
            await invoke_public_agent(
                db_path=db_path,
                owner="alice",
                execution_id="turn-routed-reset",
                agent_slug="design",
                arguments=dict(selected.arguments),
                transport="service_dispatcher",
                call=business_call,
                fingerprint_version=2,
                fingerprint=fingerprint,
            )

    assert calls == 0


@pytest.mark.asyncio
async def test_routed_identity_is_task_local_across_concurrent_executions(
    tmp_path: Path,
) -> None:
    assert getattr(
        entrypoint, "bind_routed_reservation_identity", None
    ) is not (None), "routed identity handoff is not implemented"
    entered: asyncio.Queue[str] = asyncio.Queue()
    release = asyncio.Event()
    calls: list[str] = []

    async def worker(label: str, fingerprint: str) -> str:
        db_path = str(tmp_path / f"concurrent-{label}.db")
        execution_id = f"turn-{label}"
        _repository, router_command = _reserve_router(
            db_path,
            owner=label,
            execution_id=execution_id,
            fingerprint=fingerprint,
            label=label,
        )
        selected = _selected_command(label)
        outer = CanonicalReservationIdentity(
            owner=label,
            execution_id=execution_id,
            fingerprint_version=2,
            fingerprint=fingerprint,
            command=router_command,
        )

        async def business_call() -> dict[str, str]:
            calls.append(label)
            return {"status": "succeeded"}

        with (
            bind_canonical_reservation_identity(outer),
            _routed_scope(
                db_path=db_path,
                owner=label,
                execution_id=execution_id,
                command=selected,
            ),
        ):
            await entered.put(label)
            await release.wait()
            value = await invoke_public_agent(
                db_path=db_path,
                owner=label,
                execution_id=execution_id,
                agent_slug="design",
                arguments=dict(selected.arguments),
                transport="service_dispatcher",
                call=business_call,
                fingerprint_version=2,
                fingerprint=fingerprint,
            )
        return str(value["status"])

    tasks = [
        asyncio.create_task(worker("alice", "8" * 64)),
        asyncio.create_task(worker("bob", "9" * 64)),
    ]
    assert {await entered.get(), await entered.get()} == {"alice", "bob"}
    release.set()

    assert await asyncio.gather(*tasks) == ["succeeded", "succeeded"]
    assert sorted(calls) == ["alice", "bob"]


@pytest.mark.asyncio
async def test_direct_selected_agent_does_not_apply_routed_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = str(tmp_path / "direct-selected.db")
    execution_id = "turn-direct-design"
    fingerprint = "a" * 64
    command = _selected_command("direct design")
    repository = SQLiteExecutionReservationRepository(db_path)
    repository.reserve(
        owner="alice",
        execution_id=execution_id,
        fingerprint_version=2,
        fingerprint=fingerprint,
        command=command,
    )
    route_binds = 0
    original_bind = SQLiteExecutionReservationRepository.bind_routed_agent

    def counted_bind(
        self: SQLiteExecutionReservationRepository, **kwargs: Any
    ) -> ExecutionReservationRecord:
        nonlocal route_binds
        route_binds += 1
        return original_bind(self, **kwargs)

    async def fake_invoke_agent_run(
        *, agent: str, arguments: dict[str, object], **options: object
    ) -> tuple[dict[str, object], int]:
        from mcp_server_phytomni.api.lifecycle_contract import (
            empty_agent_result,
        )

        await invoke_public_agent(
            db_path=db_path,
            owner="alice",
            execution_id=str(options["execution_id"]),
            agent_slug=agent,
            arguments=arguments,
            transport="service_dispatcher",
            call=lambda: asyncio.sleep(0, result={"status": "succeeded"}),
            fingerprint_version=2,
            fingerprint=fingerprint,
        )
        return {
            "id": repository.get(
                owner="alice", execution_id=execution_id
            ).run_id,
            "agent": agent,
            "status": "succeeded",
            "result": empty_agent_result(),
        }, 200

    monkeypatch.setattr(factory, "_tasks_db_path", lambda: db_path)
    monkeypatch.setattr(api_app, "current_request_user", lambda: "alice")
    monkeypatch.setattr(api_app, "_invoke_agent_run", fake_invoke_agent_run)
    monkeypatch.setattr(
        SQLiteExecutionReservationRepository,
        "bind_routed_agent",
        counted_bind,
    )
    app = factory.build_app()
    invoke_agent_run = (
        app.state.agent_route_dependencies.native.invoke_agent_run
    )
    identity = CanonicalReservationIdentity(
        owner="alice",
        execution_id=execution_id,
        fingerprint_version=2,
        fingerprint=fingerprint,
        command=command,
    )

    with bind_canonical_reservation_identity(identity):
        await invoke_agent_run(
            agent="design",
            arguments=dict(command.arguments),
            execution_id=execution_id,
        )

    assert route_binds == 0
    assert (
        repository.get(owner="alice", execution_id=execution_id).status.value
        == "succeeded"
    )
