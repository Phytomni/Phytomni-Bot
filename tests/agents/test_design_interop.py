# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Contract tests for Design's external planning-evidence seam."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from a2a.types import TaskState

import mcp_server_phytomni.agents.design.agent as design_agent_module
from mcp_server_phytomni.agents.analyst.agent import AnalystAgent
from mcp_server_phytomni.agents.design.agent import (
    DigitalDesignAgents,
    DigitalDesignConfig,
)
from mcp_server_phytomni.agents.design.interop import (
    DESIGN_A2A_CAPABILITY,
    DESIGN_MCP_CAPABILITY,
    DesignA2APending,
    DesignInteropDependencies,
    collect_design_a2a,
    collect_design_evidence,
)
from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.interop.a2a_mapping import (
    ExternalA2AEvent,
    ExternalA2AIdentity,
    ExternalA2APart,
)
from mcp_server_phytomni.interop.capabilities import (
    DiscoveryResult,
    InteropCapability,
)
from mcp_server_phytomni.interop.mcp_client import InteropMCPError
from mcp_server_phytomni.interop.models import (
    A2ATarget,
    MCPStreamableHttpTarget,
)
from mcp_server_phytomni.interop.registry import InteropRegistry

pytestmark = pytest.mark.agent


def _mcp_registry() -> InteropRegistry:
    """Build an operator-owned MCP target for offline Design tests."""
    target = MCPStreamableHttpTarget.model_validate(
        {
            "id": "design-mcp-peer",
            "kind": "mcp",
            "transport": "streamable_http",
            "url": "https://design-mcp.example.test/mcp",
            "allowed_tools": [DESIGN_MCP_CAPABILITY],
        }
    )
    return InteropRegistry(_targets={target.id: target})


def _a2a_registry() -> InteropRegistry:
    """Build an operator-owned A2A target for offline Design tests."""
    target = A2ATarget.model_validate(
        {
            "id": "design-a2a-peer",
            "kind": "a2a",
            "transport": "a2a",
            "card_base_url": "https://design-a2a.example.test/card",
            "allowed_interface_origins": ["https://design-a2a.example.test"],
            "allowed_interface_bindings": ["JSONRPC"],
            "allowed_skills": [DESIGN_A2A_CAPABILITY],
        }
    )
    return InteropRegistry(_targets={target.id: target})


def _mcp_capability() -> InteropCapability:
    """Return a metadata-only Design MCP capability."""
    return InteropCapability(
        target_id="design-mcp-peer",
        kind="mcp",
        remote_name=DESIGN_MCP_CAPABILITY,
        qualified_name=f"design-mcp-peer__{DESIGN_MCP_CAPABILITY}",
        description="design planning peer",
        input_schema={},
    )


def _a2a_capability() -> InteropCapability:
    """Return a metadata-only Design A2A skill."""
    return InteropCapability(
        target_id="design-a2a-peer",
        kind="a2a",
        remote_name=DESIGN_A2A_CAPABILITY,
        qualified_name=f"design-a2a-peer__{DESIGN_A2A_CAPABILITY}",
        description="design planning peer",
        input_schema={},
    )


def _task() -> dict[str, Any]:
    """Return a Design task with local-only side-effect fields."""
    return {
        "analysis_type": "protein_design_analysis",
        "species_code": "ath",
        "gene_id": "AT1G01010",
        "goal_description": "Design a protein workflow for AT1G01010.",
        "context": "prioritize stable folding",
        "data_list": {"/obs/design/reference.fasta": "reference"},
        "output_dir": "/obs/local/design-out",
        "thread_id": "design-thread",
        "is_polling": False,
    }


def _event(state: TaskState, text: str) -> ExternalA2AEvent:
    """Build one normalized A2A event for the fake stream."""
    return ExternalA2AEvent(
        kind="status_update",
        identity=ExternalA2AIdentity("design-a2a-peer", DESIGN_A2A_CAPABILITY),
        task_id="design-remote-task",
        context_id="design-remote-context",
        state=TaskState.Name(state),
        parts=(ExternalA2APart("text", text),),
    )


def _a2a_dependencies(stream: Any) -> DesignInteropDependencies:
    """Build injected A2A discovery and stream seams for a test."""
    return DesignInteropDependencies(
        registry=_a2a_registry(),
        discover_a2a=AsyncMock(
            return_value=DiscoveryResult(data=(_a2a_capability(),))
        ),
        stream_a2a=stream,
    )


async def test_design_mcp_evidence_never_forwards_local_output_dir() -> None:
    """External Design planning receives no local OBS side-effect handle."""
    calls: list[dict[str, Any]] = []

    async def invoke(*args: Any, **kwargs: Any) -> str:
        calls.append({"args": args, "kwargs": kwargs})
        return "token=hidden https://peer.example.test/private " + "x" * 40_000

    result = await collect_design_evidence(
        _task(),
        mode="required",
        target_ids=["design-mcp-peer"],
        dependencies=DesignInteropDependencies(
            registry=_mcp_registry(),
            discover=AsyncMock(
                return_value=DiscoveryResult(data=(_mcp_capability(),))
            ),
            invoke=invoke,
        ),
    )

    assert result is not None
    assert result["kind"] == "mcp"
    assert result["truncated"] is True
    assert "hidden" not in result["content"]
    assert "https://peer.example.test" not in result["content"]
    arguments = calls[0]["args"][2]
    assert arguments["task_name"] == "protein_design_analysis"
    assert "output_dir" not in arguments


async def test_design_a2a_stream_preserves_correlations() -> None:
    """A2A Design stream maps to bounded evidence and resumable ids."""
    calls: list[dict[str, Any]] = []

    async def stream(*args: Any, **kwargs: Any):
        calls.append({"args": args, "kwargs": kwargs})
        yield _event(TaskState.TASK_STATE_COMPLETED, "peer plan")

    result = await collect_design_a2a(
        _task(),
        mode="required",
        target_ids=["design-a2a-peer"],
        dependencies=_a2a_dependencies(stream),
    )

    assert result is not None
    assert result["status"] == "completed"
    assert result["task_id"] == "design-remote-task"
    assert result["context_id"] == "design-remote-context"
    assert calls[0]["kwargs"]["text"] == _task()["goal_description"]
    assert calls[0]["kwargs"]["data"]["task_name"] == (
        "protein_design_analysis"
    )
    assert calls[0]["kwargs"]["data"]["context"] == (
        "prioritize stable folding"
    )


async def test_design_a2a_resume_reuses_task_and_context_ids() -> None:
    """A2A input-required continuation does not mint new correlations."""
    calls: list[dict[str, Any]] = []
    events = [
        _event(TaskState.TASK_STATE_INPUT_REQUIRED, "choose a model"),
        _event(TaskState.TASK_STATE_COMPLETED, "model selected"),
    ]

    async def stream(*args: Any, **kwargs: Any):
        calls.append({"args": args, "kwargs": kwargs})
        yield events[len(calls) - 1]

    dependencies = DesignInteropDependencies(
        registry=_a2a_registry(),
        discover_a2a=AsyncMock(
            return_value=DiscoveryResult(data=(_a2a_capability(),))
        ),
        stream_a2a=stream,
    )
    first = await collect_design_a2a(
        _task(),
        mode="required",
        target_ids=["design-a2a-peer"],
        dependencies=dependencies,
    )
    assert first is not None
    pending = DesignA2APending(
        analysis_type="protein_design_analysis",
        species_code="ath",
        gene_id="AT1G01010",
        goal_description=_task()["goal_description"],
        context="prioritize stable folding",
        data_list=dict(_task()["data_list"]),
        output_dir="/obs/local/design-out",
        thread_id="design-thread",
        is_polling=False,
        target_id="design-a2a-peer",
        capability=DESIGN_A2A_CAPABILITY,
        task_id=first["task_id"] or "",
        context_id=first["context_id"],
        draft=first["content"],
    )
    second = await collect_design_a2a(
        _task(),
        mode="required",
        target_ids=["design-a2a-peer"],
        dependencies=dependencies,
        resume={"text": "use model A"},
        pending=pending,
    )

    assert second is not None
    assert second["status"] == "completed"
    assert calls[1]["kwargs"]["task_id"] == "design-remote-task"
    assert calls[1]["kwargs"]["context_id"] == "design-remote-context"
    assert calls[1]["kwargs"]["text"] == "use model A"


@pytest.mark.parametrize("mode", ["auto", "required"])
async def test_design_mcp_failure_respects_mode(mode: str) -> None:
    """Required mode fails closed while auto mode returns local fallback."""
    dependencies = DesignInteropDependencies(
        registry=_mcp_registry(),
        discover=AsyncMock(
            return_value=DiscoveryResult(data=(_mcp_capability(),))
        ),
        invoke=AsyncMock(
            side_effect=InteropMCPError(
                "peer failed",
                code="remote_tool_error",
            )
        ),
    )
    if mode == "auto":
        assert (
            await collect_design_evidence(
                _task(),
                mode="auto",
                target_ids=["design-mcp-peer"],
                dependencies=dependencies,
            )
            is None
        )
    else:
        with pytest.raises(InteropMCPError, match="peer failed"):
            await collect_design_evidence(
                _task(),
                mode="required",
                target_ids=["design-mcp-peer"],
                dependencies=dependencies,
            )


async def test_required_design_never_pseudo_succeeds_without_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Required mode rejects an empty external-evidence result."""
    agent = _build_agent(DesignInteropDependencies(registry=InteropRegistry()))
    monkeypatch.setattr(
        design_agent_module,
        "collect_design_evidence",
        AsyncMock(return_value=None),
    )

    with pytest.raises(
        RuntimeError, match="required Design interop produced no external"
    ):
        await getattr(agent, "_collect_design_external")(
            {
                **_task(),
                "interop_mode": "required",
                "interop_targets": ["design-mcp-peer"],
            }
        )


def _build_agent(
    dependencies: DesignInteropDependencies | None = None,
) -> DigitalDesignAgents:
    """Build Design with a non-executable Analyst stand-in."""
    return DigitalDesignAgents(
        digital_design_config=DigitalDesignConfig(),
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(
            AnalystAgent,
            SimpleNamespace(identifier=lambda: "design-test-analyst"),
        ),
        interop_dependencies=dependencies,
    )


async def test_design_worker_persists_a2a_pending_before_resume() -> None:
    """The worker returns a resume Command without local submission."""
    agent = _build_agent()
    pending = DesignA2APending(
        analysis_type="protein_design_analysis",
        species_code="ath",
        gene_id="AT1G01010",
        goal_description="goal",
        context="context",
        data_list={},
        output_dir="/obs/local/design-out",
        thread_id="design-thread",
        is_polling=False,
        target_id="design-a2a-peer",
        capability=DESIGN_A2A_CAPABILITY,
        task_id="remote-task",
        context_id="remote-context",
        draft="choose",
    )
    collect_mock = AsyncMock(return_value=pending)
    setattr(agent, "_collect_design_external", collect_mock)
    state: dict[str, Any] = {
        "task_index": 0,
        "analysis_type": "protein_design_analysis",
        "species_code": "ath",
        "gene_id": "AT1G01010",
        "output_dir": "/obs/local/design-out",
        "interop_mode": "required",
        "interop_targets": ["design-a2a-peer"],
        "task_ids": {},
        "completed_count": 0,
        "error": None,
        "failures": [],
        "design_task_result": [],
    }

    command = cast(Any, await agent.run_design_node(cast(Any, state)))

    assert command.goto == "design_a2a_resume_node"
    assert command.update["a2a_task_ids"] == {
        "protein_design_analysis": "remote-task"
    }
    assert command.update["a2a_pending"] == [pending]


async def test_design_resume_submits_locally_with_untrusted_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed peer plan is input to local Analyst, never peer submit."""
    agent = _build_agent()
    pending = DesignA2APending(
        analysis_type="protein_design_analysis",
        species_code="ath",
        gene_id="AT1G01010",
        goal_description="goal",
        context="context",
        data_list={},
        output_dir="/obs/local/design-out",
        thread_id="design-thread",
        is_polling=False,
        target_id="design-a2a-peer",
        capability=DESIGN_A2A_CAPABILITY,
        task_id="remote-task",
        context_id="remote-context",
        draft="choose",
    )
    result = {
        "target_id": "design-a2a-peer",
        "kind": "a2a",
        "capability": DESIGN_A2A_CAPABILITY,
        "status": "completed",
        "task_id": "remote-task",
        "context_id": "remote-context",
        "content": "bounded peer plan",
        "truncated": False,
    }
    monkeypatch.setattr(
        design_agent_module, "interrupt", lambda _: {"text": "yes"}
    )
    monkeypatch.setattr(
        design_agent_module,
        "collect_design_a2a",
        AsyncMock(return_value=result),
    )
    submit = AsyncMock(
        return_value={
            "task_id": "local-design-task",
            "output_dir": "/obs/local/design-out",
            "task_status": "SUCCEEDED",
        }
    )
    monkeypatch.setattr(agent, "_dispatch_and_wait_analysis", submit)

    updates = await agent.resume_design_a2a(
        cast(
            Any,
            {
                "species_code": "ath",
                "gene_id": "AT1G01010",
                "task_ids": {},
                "completed_count": 0,
                "design_task_result": [],
                "a2a_pending": [pending],
                "a2a_task_ids": {"protein_design_analysis": "remote-task"},
            },
        )
    )

    assert updates["task_ids"] == {"protein_design": "local-design-task"}
    call = submit.await_args
    assert call is not None
    options = call.args[3]
    assert options.external_evidence["content"] == "bounded peer plan"
    assert options.external_evidence["target_id"] == "design-a2a-peer"
