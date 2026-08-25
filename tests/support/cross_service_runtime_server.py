# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Deterministic real Bot process for Web/Bot acceptance tests.

Deterministic provider edges and explicit lifecycle observations replace
external systems.  HTTP admission, the detached command worker, Runtime,
SQLite journal/projection, service auth, actions, and SSE remain the
production implementations used by the normal Bot process.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import uvicorn
from httpx import ConnectError

from mcp_server_phytomni.agents.chat import service as chat_service
from mcp_server_phytomni.api import app as api_app
from mcp_server_phytomni.api.app import create_app
from mcp_server_phytomni.mcp import app as mcp_app
from mcp_server_phytomni.mcp import handlers
from mcp_server_phytomni.mcp.schemas import ChatAgent
from mcp_server_phytomni.runtime import (
    execution_supervisor_service_v2 as supervisor_service,
)
from mcp_server_phytomni.runtime import run_registry as run_registry_module
from mcp_server_phytomni.runtime.checkpoint_instrumentation_v2 import (
    record_input_required,
)
from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
)
from mcp_server_phytomni.runtime.execution_command_dispatcher_v2 import (
    SQLiteExecutionCommandQueueV2,
)
from mcp_server_phytomni.runtime.execution_instrumentation_v2 import (
    current_execution_boundary,
)
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    ExecutionCommand,
)
from mcp_server_phytomni.runtime.operation_instrumentation_v2 import (
    instrument_operation_invocation,
)
from mcp_server_phytomni.runtime.provider_instrumentation_v2 import (
    instrument_provider_submission,
)
from tests.support.all_agent_runtime_cases import REAL_HANDLER_FIXTURES

_remaining_provider_failures = int(
    os.environ.get("PHYTOMNI_CROSS_SERVICE_PROVIDER_FAILURES", "0")
)
_successful_provider_calls = 0
_lifecycle_scenario = os.environ.get(
    "PHYTOMNI_CROSS_SERVICE_SCENARIO", ""
).strip()
_selected_agent = os.environ.get(
    "PHYTOMNI_CROSS_SERVICE_AGENT", "chat"
).strip()
_topology_scenario = os.environ.get(
    "PHYTOMNI_CROSS_SERVICE_TOPOLOGY", ""
).strip()
_provider_terminal = os.environ.get(
    "PHYTOMNI_CROSS_SERVICE_PROVIDER_TERMINAL", ""
).strip()
_legacy_stuck_fixture = os.environ.get(
    "PHYTOMNI_CROSS_SERVICE_LEGACY_STUCK", ""
).strip()
_real_chat_handler = mcp_app.TOOL_HANDLERS["ChatAgent"]
_waiting_execution_id: str | None = None


async def _exercise_parallel_siblings() -> None:
    """Create two overlapping sibling units through production instrumentation."""
    arrived = 0
    both_arrived = asyncio.Event()

    async def branch() -> str:
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            both_arrived.set()
        await asyncio.wait_for(both_arrived.wait(), timeout=1)
        await asyncio.sleep(0.02)
        return "done"

    await asyncio.gather(
        instrument_operation_invocation("review.retrieve_dimension", branch),
        instrument_operation_invocation("review.retrieve_dimension", branch),
    )


async def _deterministic_chat_provider(**_kwargs: object) -> dict[str, Any]:
    global _remaining_provider_failures, _successful_provider_calls
    if _remaining_provider_failures > 0:
        _remaining_provider_failures -= 1
        raise ConnectError("deterministic transient provider failure")
    delay_ms = int(
        os.environ.get("PHYTOMNI_CROSS_SERVICE_PROVIDER_DELAY_MS", "0")
    )
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000)
    _successful_provider_calls += 1
    if _topology_scenario == "parallel" and _successful_provider_calls == 1:
        await _exercise_parallel_siblings()
    content = (
        os.environ.get("PHYTOMNI_CROSS_SERVICE_ANSWER", "cross-service")
        if _successful_provider_calls == 1
        else "[]"
    )
    return {
        "choices": [
            {
                "message": {
                    "content": content,
                }
            }
        ]
    }


async def _scenario_chat_handler(args: Any) -> Any:
    """Wrap the real Chat handler with a deterministic lifecycle observation."""
    global _waiting_execution_id
    value = await _real_chat_handler(args)
    if not _lifecycle_scenario:
        return value
    body = dict(value) if isinstance(value, Mapping) else {}
    if _lifecycle_scenario == "partial":
        body["status"] = "partial"
        return body, 206
    if _lifecycle_scenario == "timed_out":
        body["status"] = "timed_out"
        return body, 504
    if _lifecycle_scenario == "degraded":
        body.update({"status": "running", "degraded_tracking": True})
        return body, 202
    if _lifecycle_scenario == "waiting_input":
        boundary = current_execution_boundary()
        if boundary is None:
            raise RuntimeError(
                "waiting-input scenario requires Runtime boundary"
            )
        _waiting_execution_id = boundary.context.execution_id
        record_input_required(
            surface_id="cross-service-confirmation",
            widget="confirm",
        )
        body["status"] = "input_required"
        return body, 202
    raise ValueError("unsupported cross-service lifecycle scenario")


async def _deterministic_resume_a2ui_run(
    *, run_id: str, body: Any, debug: bool = False
) -> Any:
    """Resume the test checkpoint through the production Runtime operation."""
    del run_id, debug
    if _waiting_execution_id is None:
        raise RuntimeError("waiting execution identity is unavailable")
    owner = api_app.current_request_user() or "anonymous"
    db_path = api_app.resolve_tasks_db_path()
    reservation = api_app.SQLiteExecutionReservationRepository(db_path).get(
        owner=owner,
        execution_id=_waiting_execution_id,
    )

    async def resume_domain() -> Any:
        value = await _real_chat_handler(
            ChatAgent(
                user_query="Continue the paused analysis.", obs_file_list=[]
            )
        )
        resumed = dict(value) if isinstance(value, Mapping) else {}
        resumed["status"] = "succeeded"
        return resumed

    await api_app.invoke_public_agent_operation(
        db_path=db_path,
        owner=owner,
        execution_id=_waiting_execution_id,
        agent_slug="chat",
        operation="resume",
        action_id=body.action_id,
        expected_revision=reservation.supervisor_revision,
        arguments=body.model_dump(mode="json"),
        transport="cross_service_action",
        call=resume_domain,
    )
    return (
        {
            "id": reservation.run_id,
            "run_id": reservation.run_id,
            "object": "agent.run",
            "agent": "chat",
            "status": "succeeded",
            "task_ids": [],
            "result": {"formatted": {"answer": "resumed answer"}},
        },
        200,
    )


async def _deterministic_agent_provider(**_kwargs: object) -> dict[str, Any]:
    result = dict(REAL_HANDLER_FIXTURES[_selected_agent][2])
    if _selected_agent != "design" or not _provider_terminal:
        return result

    async def submit() -> dict[str, Any]:
        return result

    return await instrument_provider_submission(
        provider_kind="analysis_task_platform",
        operation_key="provider.analysis.submit",
        call=submit,
        identity_from_result=lambda value: str(
            value["design_task_result"][0]["task_id"]
        ),
    )


async def _deterministic_task_reconcile(task_id: str) -> dict[str, Any]:
    if _provider_terminal not in {
        "succeeded",
        "partial",
        "failed",
        "cancelled",
        "timed_out",
    }:
        raise ValueError("unsupported deterministic provider terminal")
    return {
        "task_id": task_id,
        "status": _provider_terminal,
        "output_dir": "/safe/provider",
        "live_status": {
            "status": _provider_terminal,
        },
    }


def _seed_legacy_stuck_execution() -> None:
    if not _legacy_stuck_fixture:
        return
    fixture = json.loads(_legacy_stuck_fixture)
    arguments = fixture["arguments"]
    command = ExecutionCommand(
        agent_slug=fixture["agent_slug"], arguments=arguments
    )
    db_path = os.environ["API_TASKS_DB_PATH"]
    SQLiteExecutionReservationRepository(db_path).reserve(
        owner=fixture["owner_ref"],
        execution_id=fixture["execution_id"],
        fingerprint_version=fixture["fingerprint_version"],
        fingerprint=fixture["fingerprint"],
        command=command,
        durable_command={
            "agent": fixture["agent_slug"],
            "arguments": arguments,
            "execution_id": fixture["execution_id"],
            "owner_ref": fixture["owner_ref"],
            "fingerprint_version": fixture["fingerprint_version"],
            "fingerprint": fixture["fingerprint"],
        },
    )
    conversation = arguments["__conversation"]
    store = ConversationContextStore(db_path)
    store.begin_turn(
        conversation["conversation_key"],
        conversation["turn_id"],
        conversation["operation"],
        conversation["base_business_context_version"],
    )
    store.mark_turn_failed(
        conversation["conversation_key"], conversation["turn_id"]
    )
    queue = SQLiteExecutionCommandQueueV2(db_path)
    claim = queue.claim(worker_id="legacy-fixture-dispatcher")
    if claim is None or not queue.reconcile(
        claim, code="dispatch_unknown_after_boundary"
    ):
        raise RuntimeError("failed to seed legacy reconcile command")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    _seed_legacy_stuck_execution()
    chat_service.run_phyto_chat_cached = _deterministic_chat_provider
    if _selected_agent != "chat":
        dependency = REAL_HANDLER_FIXTURES[_selected_agent][0]
        setattr(handlers, dependency, _deterministic_agent_provider)
    if _provider_terminal:
        supervisor_service.reconcile_task = _deterministic_task_reconcile
        run_registry_module.reconcile_task = _deterministic_task_reconcile
    if _lifecycle_scenario:
        mcp_app.TOOL_HANDLERS["ChatAgent"] = _scenario_chat_handler
    if _lifecycle_scenario == "waiting_input":
        api_app._resume_a2ui_run = _deterministic_resume_a2ui_run
    scratch_root = Path(os.environ["TEMP_DIR"])
    setattr(
        handlers,
        "scratch_server_dir",
        lambda _config, scope: str(scratch_root / scope),
    )
    uvicorn.run(
        create_app(),
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
