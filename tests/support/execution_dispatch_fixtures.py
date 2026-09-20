# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared identity and dispatch setup for execution V2 unit tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from mcp_server_phytomni.api import app as api_app
from mcp_server_phytomni.api import factory
from mcp_server_phytomni.api.lifecycle_contract import empty_agent_result
from mcp_server_phytomni.public_agent_catalog import PublicAgentSpec
from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
    CanonicalReservationIdentity,
    InvokePublicAgentKwargs,
    StatusMapper,
    invoke_public_agent,
)
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    ExecutionReservationRecord,
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    ExecutionCommand,
    ExecutionContext,
)


def reserve_test_execution(
    db_path: str,
    execution_id: str,
    fingerprint: str,
    command: ExecutionCommand,
    *,
    persist_command: bool = False,
) -> tuple[SQLiteExecutionReservationRepository, ExecutionReservationRecord]:
    """Reserve one canonical Alice execution for identity handoff tests."""
    repository = SQLiteExecutionReservationRepository(db_path)
    if persist_command:
        record = repository.reserve(
            owner="alice",
            execution_id=execution_id,
            fingerprint_version=2,
            fingerprint=fingerprint,
            command=command,
            durable_command={
                "agent": command.agent_slug,
                "arguments": dict(command.arguments),
                "execution_id": execution_id,
                "owner_ref": "alice",
                "fingerprint_version": 2,
                "fingerprint": fingerprint,
            },
        )
    else:
        record = repository.reserve(
            owner="alice",
            execution_id=execution_id,
            fingerprint_version=2,
            fingerprint=fingerprint,
            command=command,
        )
    return repository, record


def canonical_test_identity(
    execution_id: str,
    fingerprint: str,
    command: ExecutionCommand,
) -> CanonicalReservationIdentity:
    """Build the canonical Alice identity used by dispatch fixtures."""
    return CanonicalReservationIdentity(
        owner="alice",
        execution_id=execution_id,
        fingerprint_version=2,
        fingerprint=fingerprint,
        command=command,
    )


def canonical_dispatch_options(
    options: Mapping[str, object],
    fingerprint: str,
) -> dict[str, object]:
    """Add the canonical test fingerprint to native dispatch options."""
    return {
        **options,
        "fingerprint_version": 2,
        "fingerprint": fingerprint,
    }


async def invoke_dispatched_design(
    db_path: str,
    arguments: Mapping[str, object],
    options: Mapping[str, object],
    call: Callable[[], Awaitable[Any]],
    *,
    status_mapper: StatusMapper | None = None,
) -> Any:
    """Invoke the selected design Agent with canonical dispatcher metadata."""
    fingerprint_version = options["fingerprint_version"]
    assert isinstance(fingerprint_version, int)
    request = InvokePublicAgentKwargs(
        db_path=db_path,
        owner="alice",
        execution_id=str(options["execution_id"]),
        agent_slug="design",
        arguments={
            key: value
            for key, value in arguments.items()
            if not key.startswith("__")
        },
        transport="service_dispatcher",
        call=call,
        fingerprint_version=fingerprint_version,
        fingerprint=str(options["fingerprint"]),
    )
    if status_mapper is not None:
        request["status_mapper"] = status_mapper
    return await invoke_public_agent(**request)


def succeeded_agent_http_response(
    repository: SQLiteExecutionReservationRepository,
    execution_id: str,
    agent: str,
) -> tuple[dict[str, object], int]:
    """Project one successful native Agent invocation to its HTTP envelope."""
    return (
        {
            "id": repository.get(
                owner="alice",
                execution_id=execution_id,
            ).run_id,
            "agent": agent,
            "status": "succeeded",
            "result": empty_agent_result(),
        },
        200,
    )


def install_native_agent_invoker(
    monkeypatch: pytest.MonkeyPatch,
    db_path: str,
    invoke_agent_run: Callable[..., Awaitable[tuple[dict[str, object], int]]],
) -> Any:
    """Install a native Agent stub and return the app's bound dispatcher."""
    monkeypatch.setattr(factory, "_tasks_db_path", lambda: db_path)
    monkeypatch.setattr(api_app, "current_request_user", lambda: "alice")
    monkeypatch.setattr(api_app, "_invoke_agent_run", invoke_agent_run)
    app = factory.build_app()
    return app.state.agent_route_dependencies.native.invoke_agent_run


def run_migrated_agent(
    db_path: str | Path,
    execution_id: str,
    agent_slug: str,
    transport: str,
    call: Callable[[], Awaitable[Any]],
) -> Any:
    """Run one migration cohort invocation through its public entrypoint."""
    request = InvokePublicAgentKwargs(
        db_path=str(db_path),
        owner="alice",
        execution_id=execution_id,
        agent_slug=agent_slug,
        arguments={"query": "rice"},
        transport=transport,
        call=call,
    )
    return asyncio.run(invoke_public_agent(**request))


def execution_context_fixture(
    agent: PublicAgentSpec,
    owner_ref: str,
    execution_id: str,
    transport: str,
    deadline_at: datetime | None = None,
) -> ExecutionContext:
    """Build a canonical root-span context for Driver contract tests."""
    return ExecutionContext(
        owner_ref=owner_ref,
        execution_id=execution_id,
        fingerprint_version=1,
        fingerprint="a" * 64,
        agent=agent,
        root_span_id="span-root",
        current_span_id="span-root",
        transport=transport,
        deadline_at=deadline_at,
    )


__all__ = [
    "canonical_dispatch_options",
    "canonical_test_identity",
    "execution_context_fixture",
    "install_native_agent_invoker",
    "invoke_dispatched_design",
    "reserve_test_execution",
    "run_migrated_agent",
    "succeeded_agent_http_response",
]
