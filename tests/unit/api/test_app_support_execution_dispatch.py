# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit contract for detached execution conversation routing."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from mcp_server_phytomni.api import app_support
from mcp_server_phytomni.mcp import app as mcp_app
from mcp_server_phytomni.runtime.execution_command_dispatcher_v2 import (
    InvokeCommand,
)

pytestmark = pytest.mark.unit


def _conversation(mode: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "conversation_key": "018fdf9e-1f0b-7a63-a5a3-5e4625b43ad6",
        "dialogue_id": "018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7",
        "turn_id": "1",
        "request_id": "request-1",
        "operation": "append",
        "mode": mode,
        "current_message": {"content": "Reply with OK.", "locale": "en-US"},
        "requested_agent_id": "ChatAgent",
        "allowed_agent_ids": ["ChatAgent"],
        "ledger_cursor": 0,
        "ledger_version": "a" * 64,
        "base_business_context_version": 0,
    }


def test_detached_dispatch_routes_expert_envelope_context() -> None:
    assert (
        app_support.execution_conversation_dispatch_kind(
            _conversation("expert")
        )
        == "expert"
    )
    assert (
        app_support.execution_conversation_dispatch_kind(
            _conversation("instant")
        )
        == "native"
    )


@pytest.mark.asyncio
async def test_native_dispatch_keeps_durable_arguments_out_of_agent_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_handler(_arguments: object) -> dict[str, object]:
        return {
            "choices": [
                {
                    "message": {
                        "content": "OK",
                        "follow_up_questions": ["What next?"],
                    }
                }
            ]
        }

    async def fake_runtime(**kwargs: object) -> object:
        captured["arguments"] = kwargs["arguments"]
        call = kwargs["call"]
        assert callable(call)
        raw = await call()
        mapper = kwargs["public_result_mapper"]
        assert callable(mapper)
        captured["projected"] = mapper(raw)
        return raw

    monkeypatch.setitem(mcp_app.TOOL_HANDLERS, "ChatAgent", fake_handler)
    monkeypatch.setattr(mcp_app, "invoke_public_agent", fake_runtime)
    business_arguments = {"user_query": "Reply with OK.", "obs_file_list": []}
    durable_arguments = {
        **business_arguments,
        "__query": "Reply with OK.",
        "__conversation": _conversation("instant"),
    }

    await mcp_app.invoke_tool_raw(
        "ChatAgent",
        business_arguments,
        runtime_arguments=durable_arguments,
    )

    assert captured["arguments"] == durable_arguments
    assert captured["projected"] == {
        "result": {
            "formatted": {
                "answer": "OK",
                "follow_up_questions": ("What next?",),
                "metadata": {},
                "references": (),
                "tabular": None,
                "output_dirs": (),
            }
        }
    }


@pytest.mark.asyncio
async def test_detached_expert_dispatch_preserves_selected_design_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Web-admitted Design turn keeps its resolver command arguments."""
    captured: dict[str, object] = {}
    dispatched = asyncio.Event()

    async def noop(*_args: object, **_kwargs: object) -> None:
        return None

    async def dispatcher(
        *,
        db_path: str,
        invoke: InvokeCommand,
        stop: asyncio.Event,
    ) -> None:
        from mcp_server_phytomni.runtime.execution_reservation_v2 import (
            SQLiteExecutionReservationRepository,
        )
        from mcp_server_phytomni.runtime.execution_runtime_contracts import (
            ExecutionCommand,
        )

        durable_arguments = {
            "__allowed_tools": ["DigitalDesignAgent"],
            "__attachment_owner": "alice",
            "__conversation": {
                **_conversation("expert"),
                "current_message": {
                    "content": (
                        "Please help me design the protein structure "
                        "based on evolution information for gene "
                        "Os01g0177400."
                    ),
                    "locale": "en-US",
                },
                "requested_agent_id": "DigitalDesignAgent",
                "allowed_agent_ids": ["DigitalDesignAgent"],
            },
            "__dialogue_id": "018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7",
            "__forced_tool": "DigitalDesignAgent",
            "__query": (
                "Please help me design the protein structure based on "
                "evolution information for gene Os01g0177400."
            ),
            "interop_mode": "off",
            "interop_targets": [],
            "locale": "en-US",
            "obs_file_list": [],
            "resolve_gene_id": True,
            "user_query": (
                "Please help me design the protein structure based on "
                "evolution information for gene Os01g0177400."
            ),
        }
        fingerprint = "d" * 64
        SQLiteExecutionReservationRepository(db_path).reserve(
            owner="anonymous",
            execution_id="turn-design-context",
            fingerprint_version=2,
            fingerprint=fingerprint,
            command=ExecutionCommand(
                agent_slug="design", arguments=durable_arguments
            ),
        )
        captured["result"] = await invoke(
            "DigitalDesignAgent",
            durable_arguments,
            execution_id="turn-design-context",
            agent_slug="design",
            fingerprint_version=2,
            fingerprint=fingerprint,
        )
        dispatched.set()
        await stop.wait()

    async def supervisor(*, db_path: str, stop: asyncio.Event) -> None:
        del db_path
        await stop.wait()

    async def execute_context_expert(
        _payload: object,
        _dependencies: object,
        **kwargs: object,
    ) -> JSONResponse:
        from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
            invoke_public_agent,
        )

        selected_arguments = kwargs.get("selected_arguments")
        assert isinstance(selected_arguments, dict)
        captured["selected_arguments"] = selected_arguments
        captured["runtime_value"] = await invoke_public_agent(
            db_path=str(tmp_path / "design-context.db"),
            owner="anonymous",
            execution_id="turn-design-context",
            agent_slug="design",
            arguments=dict(selected_arguments),
            transport="service_dispatcher",
            call=lambda: asyncio.sleep(
                0, result={"status": "running", "task_ids": ["task-1"]}
            ),
            fingerprint_version=2,
            fingerprint="d" * 64,
        )
        return JSONResponse({"status": "running"}, status_code=202)

    monkeypatch.setattr(
        app_support, "validate_citation_database", lambda: None
    )
    monkeypatch.setattr(app_support, "init_outbound_runtime", noop)
    monkeypatch.setattr(app_support, "aclose_outbound_runtime", noop)
    monkeypatch.setattr(app_support, "refresh_research_relay_capability", noop)
    monkeypatch.setattr(
        app_support, "ensure_research_input_runtime", lambda: None
    )
    monkeypatch.setattr(app_support, "recover_registered_startup", noop)
    monkeypatch.setattr(app_support, "aclose_gauss_pool", noop)
    monkeypatch.setattr(
        app_support,
        "resolve_tasks_db_path",
        lambda: tmp_path / "design-context.db",
    )
    monkeypatch.setattr(
        app_support, "run_execution_command_dispatcher", dispatcher
    )
    monkeypatch.setattr(
        app_support, "run_execution_supervisor_service", supervisor
    )

    from mcp_server_phytomni.api.routes import expert_context

    monkeypatch.setattr(
        expert_context, "execute_context_expert", execute_context_expert
    )
    app = FastAPI()
    app.state.agent_route_dependencies = object()

    lifespan = getattr(app_support, "_http_lifespan")
    async with lifespan(app):
        await asyncio.wait_for(dispatched.wait(), timeout=1)

    selected = captured["selected_arguments"]
    assert isinstance(selected, dict)
    assert selected == {
        "interop_mode": "off",
        "interop_targets": [],
        "obs_file_list": [],
        "resolve_gene_id": True,
        "user_query": (
            "Please help me design the protein structure based on evolution "
            "information for gene Os01g0177400."
        ),
    }
    assert captured["runtime_value"] == {
        "status": "running",
        "task_ids": ["task-1"],
    }


@pytest.mark.asyncio
async def test_routed_expert_binds_selected_identity_before_runtime_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A routed async Agent starts below the existing router admission."""
    from mcp_server_phytomni.api import app as api_app
    from mcp_server_phytomni.api import factory
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        CanonicalReservationIdentity,
        bind_canonical_reservation_identity,
        invoke_public_agent,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        EXPERT_ROUTER_AGENT_SLUG,
        SQLiteExecutionReservationRepository,
    )
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        ExecutionCommand,
    )

    db_path = str(tmp_path / "routed-expert-start.db")
    execution_id = "turn-routed-design"
    fingerprint = "7" * 64
    router_command = ExecutionCommand(
        agent_slug=EXPERT_ROUTER_AGENT_SLUG,
        arguments={"__query": "design Os01g0177400"},
    )
    selected_arguments = {
        "user_query": "design Os01g0177400",
        "obs_file_list": [],
        "resolve_gene_id": True,
    }
    repository = SQLiteExecutionReservationRepository(db_path)
    original = repository.reserve(
        owner="alice",
        execution_id=execution_id,
        fingerprint_version=2,
        fingerprint=fingerprint,
        command=router_command,
    )
    provider_calls = 0

    async def fake_invoke_agent_run(
        *, agent: str, arguments: dict[str, object], **options: object
    ) -> tuple[dict[str, object], int]:
        from mcp_server_phytomni.api.lifecycle_contract import (
            empty_agent_result,
        )

        async def provider_call() -> dict[str, str]:
            nonlocal provider_calls
            provider_calls += 1
            return {"status": "succeeded"}

        await invoke_public_agent(
            db_path=db_path,
            owner="alice",
            execution_id=str(options["execution_id"]),
            agent_slug=agent,
            arguments=arguments,
            transport="service_dispatcher",
            call=provider_call,
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
    app = factory.build_app()
    invoke_agent_run = (
        app.state.agent_route_dependencies.native.invoke_agent_run
    )
    identity = CanonicalReservationIdentity(
        owner="alice",
        execution_id=execution_id,
        fingerprint_version=2,
        fingerprint=fingerprint,
        command=router_command,
    )

    with bind_canonical_reservation_identity(identity):
        await invoke_agent_run(
            agent="design",
            arguments=selected_arguments,
            execution_id=execution_id,
        )

    current = repository.get(owner="alice", execution_id=execution_id)
    assert provider_calls == 1
    assert current.run_id == original.run_id
    assert current.agent_slug == "design"
    assert current.status.value == "succeeded"
