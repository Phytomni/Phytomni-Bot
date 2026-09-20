# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""End-to-end durable start for an autonomously routed Expert execution."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.support.execution_dispatch_fixtures import (
    canonical_test_identity,
    invoke_dispatched_design,
)

from mcp_server_phytomni.runtime.execution_command_dispatcher_v2 import (
    SQLiteExecutionCommandQueueV2,
    dispatch_one_execution_command,
)
from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
    bind_canonical_reservation_identity,
    bind_routed_reservation_identity,
)
from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_journal_v2 import ExecutionStatus
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    EXPERT_ROUTER_AGENT_SLUG,
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    ExecutionCommand,
)
from mcp_server_phytomni.runtime.provider_instrumentation_v2 import (
    instrument_provider_submission,
)
from mcp_server_phytomni.runtime.sqlite import sqlite_transaction

pytestmark = pytest.mark.unit

_ROUTED_EXECUTION_ID = "turn-autonomous-routed-design"
_ROUTED_FINGERPRINT = "b" * 64


@pytest.mark.asyncio
async def test_autonomous_expert_routes_to_one_durable_design_start(
    tmp_path: Path,
) -> None:
    """Verify autonomous expert routes to one durable design start."""
    db_path = str(tmp_path / "autonomous-routed-design.db")
    outer_arguments = {
        "__query": "design the protein structure for Os01g0177400",
        "__allowed_tools": ["DigitalDesignAgent"],
        "__conversation": {
            "schema_version": 1,
            "conversation_key": "018fdf9e-1f0b-7a63-a5a3-5e4625b43ad6",
            "turn_id": "63",
            "operation": "append",
            "mode": "expert",
            "requested_agent_id": None,
            "allowed_agent_ids": ["DigitalDesignAgent"],
        },
    }
    router_command = ExecutionCommand(
        agent_slug=EXPERT_ROUTER_AGENT_SLUG,
        arguments=outer_arguments,
    )
    selected_command = ExecutionCommand(
        agent_slug="design",
        arguments={
            "user_query": "design the protein structure for Os01g0177400",
            "obs_file_list": [],
            "resolve_gene_id": True,
        },
    )
    repository = SQLiteExecutionReservationRepository(db_path)
    admitted = repository.reserve(
        owner="alice",
        execution_id=_ROUTED_EXECUTION_ID,
        fingerprint_version=2,
        fingerprint=_ROUTED_FINGERPRINT,
        command=router_command,
        durable_command={
            "agent": EXPERT_ROUTER_AGENT_SLUG,
            "arguments": outer_arguments,
            "execution_id": _ROUTED_EXECUTION_ID,
            "owner_ref": "alice",
            "fingerprint_version": 2,
            "fingerprint": _ROUTED_FINGERPRINT,
        },
    )
    provider_calls = 0

    async def invoke(
        tool: str, arguments: dict[str, object], **kwargs: object
    ) -> None:
        assert tool == "ExpertRouter"
        conversation = arguments["__conversation"]
        assert isinstance(conversation, dict)
        assert conversation["requested_agent_id"] is None
        outer = canonical_test_identity(
            _ROUTED_EXECUTION_ID,
            _ROUTED_FINGERPRINT,
            ExecutionCommand(
                agent_slug=EXPERT_ROUTER_AGENT_SLUG,
                arguments=arguments,
            ),
        )

        async def submit() -> dict[str, str]:
            nonlocal provider_calls
            provider_calls += 1
            return {"status": "running", "task_id": "design-provider-1"}

        async def business_call() -> dict[str, str]:
            return await instrument_provider_submission(
                provider_kind="analysis_task_platform",
                operation_key="provider.analysis.submit",
                call=submit,
                identity_from_result=lambda value: value["task_id"],
            )

        with (
            bind_canonical_reservation_identity(outer),
            bind_routed_reservation_identity(
                db_path=db_path,
                owner="alice",
                execution_id=str(kwargs["execution_id"]),
                command=selected_command,
            ),
        ):
            await invoke_dispatched_design(
                db_path,
                {
                    "user_query": (
                        "design the protein structure for Os01g0177400"
                    )
                },
                kwargs,
                business_call,
                status_mapper=lambda _value: ExecutionStatus.RUNNING,
            )

    assert await dispatch_one_execution_command(
        queue=SQLiteExecutionCommandQueueV2(db_path),
        worker_id="autonomous-router-worker",
        db_path=db_path,
        invoke=invoke,
    )

    current = repository.get(owner="alice", execution_id=_ROUTED_EXECUTION_ID)
    projection = SQLiteExecutionJournal(db_path).get_projection(
        _ROUTED_EXECUTION_ID, owner="alice"
    )
    with sqlite_transaction(db_path) as connection:
        command_state = connection.execute(
            "SELECT state FROM execution_commands_v2 "
            "WHERE owner_ref = ? AND execution_id = ?",
            ("alice", _ROUTED_EXECUTION_ID),
        ).fetchone()
        run_count = connection.execute(
            "SELECT COUNT(*) FROM runs WHERE user_id = ? "
            "AND execution_id = ?",
            ("alice", _ROUTED_EXECUTION_ID),
        ).fetchone()[0]
        provider_identity_count = connection.execute(
            "SELECT COUNT(*) FROM execution_work_units "
            "WHERE owner_ref = ? AND execution_id = ? "
            "AND provider_task_id IS NOT NULL",
            ("alice", _ROUTED_EXECUTION_ID),
        ).fetchone()[0]

    assert current.run_id == admitted.run_id
    assert current.agent_slug == "design"
    assert current.status.value == "running"
    assert projection.latest_seq > 0
    assert command_state == ("acknowledged",)
    assert run_count == 1
    assert provider_calls == 1
    assert provider_identity_count == 1
