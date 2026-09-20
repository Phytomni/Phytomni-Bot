# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit contract for detached execution conversation routing."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from importlib import import_module
from pathlib import Path
from typing import Literal, cast

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from tests.support.execution_dispatch_fixtures import (
    canonical_dispatch_options,
    canonical_test_identity,
    install_native_agent_invoker,
    invoke_dispatched_design,
    reserve_test_execution,
    succeeded_agent_http_response,
)
from tests.support.http_fakes import build_conversation_context_envelope

from mcp_server_phytomni.api import app_support
from mcp_server_phytomni.mcp import app as mcp_app
from mcp_server_phytomni.runtime.execution_command_dispatcher_v2 import (
    InvokeCommand,
)
from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
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

pytestmark = pytest.mark.unit


def _conversation(
    mode: Literal["instant", "expert"],
) -> dict[str, object]:
    return build_conversation_context_envelope(
        "1",
        mode=mode,
        content="Reply with OK.",
        requested_agent_id="ChatAgent",
        allowed_agent_ids=("ChatAgent",),
    )


def test_detached_dispatch_routes_expert_envelope_context() -> None:
    """Verify detached dispatch routes expert envelope context."""
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
    """Verify native dispatch keeps durable arguments out of agent schema."""
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
        raw = await cast(Callable[[], Awaitable[object]], call)()
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

    expert_context = import_module(
        "mcp_server_phytomni.api.routes.expert_context"
    )

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
    repository, original = reserve_test_execution(
        db_path,
        execution_id,
        fingerprint,
        router_command,
    )
    provider_calls = 0

    async def fake_invoke_agent_run(
        *, agent: str, arguments: dict[str, object], **options: object
    ) -> tuple[dict[str, object], int]:

        async def provider_call() -> dict[str, str]:
            nonlocal provider_calls
            provider_calls += 1
            return {"status": "succeeded"}

        await invoke_dispatched_design(
            db_path,
            arguments,
            canonical_dispatch_options(options, fingerprint),
            call=provider_call,
        )
        return succeeded_agent_http_response(repository, execution_id, agent)

    invoke_agent_run = install_native_agent_invoker(
        monkeypatch,
        db_path,
        fake_invoke_agent_run,
    )
    identity = canonical_test_identity(
        execution_id,
        fingerprint,
        router_command,
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
