# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Cross-agent interop policy matrix for the C4.7 public contract."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, Literal, cast
from unittest.mock import AsyncMock

import pytest
from a2a.types import TaskState

from mcp_server_phytomni.agents.design.interop import (
    DESIGN_A2A_CAPABILITY,
    DESIGN_MCP_CAPABILITY,
    DesignInteropDependencies,
    collect_design_a2a,
    collect_design_evidence,
)
from mcp_server_phytomni.agents.research.interop import (
    RESEARCH_A2A_CAPABILITY,
    RESEARCH_MCP_CAPABILITY,
    ResearchInteropDependencies,
    collect_research_a2a,
    collect_research_evidence,
)
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
from mcp_server_phytomni.interop.planner import InteropMode
from mcp_server_phytomni.interop.registry import InteropRegistry

pytestmark = pytest.mark.agent

AgentName = Literal["research", "design"]
Transport = Literal["mcp", "a2a"]
Outcome = Literal["completed", "failure", "timeout"]


def _capability(agent: AgentName, transport: Transport) -> InteropCapability:
    """Build one metadata-only capability for the matrix target."""
    capability = (
        RESEARCH_MCP_CAPABILITY
        if agent == "research" and transport == "mcp"
        else (
            DESIGN_MCP_CAPABILITY
            if agent == "design" and transport == "mcp"
            else (
                RESEARCH_A2A_CAPABILITY
                if agent == "research"
                else DESIGN_A2A_CAPABILITY
            )
        )
    )
    target_id = f"{agent}-{transport}-peer"
    return InteropCapability(
        target_id=target_id,
        kind=transport,
        remote_name=capability,
        qualified_name=f"{target_id}__{capability}",
        description=f"{agent} {transport} matrix capability",
        input_schema={
            "type": "object",
            "properties": {"goal_description": {"type": "string"}},
            "required": ["goal_description"],
        },
    )


def _registry(agent: AgentName, transport: Transport) -> InteropRegistry:
    """Build an operator-owned target for one matrix cell."""
    target_id = f"{agent}-{transport}-peer"
    target: A2ATarget | MCPStreamableHttpTarget
    if transport == "mcp":
        target = MCPStreamableHttpTarget.model_validate(
            {
                "id": target_id,
                "kind": "mcp",
                "transport": "streamable_http",
                "url": f"https://{target_id}.example.test/mcp",
                "allowed_tools": [
                    (
                        RESEARCH_MCP_CAPABILITY
                        if agent == "research"
                        else DESIGN_MCP_CAPABILITY
                    )
                ],
            }
        )
    else:
        target = A2ATarget.model_validate(
            {
                "id": target_id,
                "kind": "a2a",
                "transport": "a2a",
                "card_base_url": f"https://{target_id}.example.test/card",
                "allowed_interface_origins": [
                    f"https://{target_id}.example.test"
                ],
                "allowed_interface_bindings": ["JSONRPC"],
                "allowed_skills": [
                    (
                        RESEARCH_A2A_CAPABILITY
                        if agent == "research"
                        else DESIGN_A2A_CAPABILITY
                    )
                ],
            }
        )
    return InteropRegistry(enabled=True, _targets={target_id: target})


def _research_task() -> dict[str, Any]:
    """Build the smallest Research planning payload."""
    return {
        "goal_description": "Characterize PHYB",
        "context": "rice drought response",
        "data_list": {},
        "task_name": "research_goal_0",
    }


def _design_task() -> dict[str, Any]:
    """Build the smallest Design planning payload."""
    return {
        "analysis_type": "protein_design_analysis",
        "species_code": "ath",
        "gene_id": "AT1G01010",
        "goal_description": "Design a stable protein workflow.",
        "context": "prioritize stable folding",
        "data_list": {},
        "output_dir": "/obs/local/design-out",
        "thread_id": "design-thread",
        "is_polling": False,
    }


def _event(
    agent: AgentName,
    state: TaskState,
) -> ExternalA2AEvent:
    """Build one normalized A2A terminal or input-required event."""
    capability = (
        RESEARCH_A2A_CAPABILITY
        if agent == "research"
        else DESIGN_A2A_CAPABILITY
    )
    return ExternalA2AEvent(
        kind="status_update",
        identity=ExternalA2AIdentity(
            f"{agent}-a2a-peer",
            capability,
        ),
        task_id=f"{agent}-remote-task",
        context_id=f"{agent}-remote-context",
        state=TaskState.Name(state),
        parts=(ExternalA2APart("text", "bounded peer evidence"),),
    )


def _a2a_stream(
    agent: AgentName,
    outcome: Outcome,
) -> AsyncIterator[ExternalA2AEvent]:
    """Return a stream that models a completed or failed peer call."""

    async def stream(*_: Any, **__: Any) -> AsyncIterator[ExternalA2AEvent]:
        if outcome == "failure":
            raise RuntimeError("peer failed")
        if outcome == "timeout":
            raise TimeoutError("peer timed out")
        yield _event(agent, TaskState.TASK_STATE_COMPLETED)

    return stream()


def _mcp_dependencies(
    agent: AgentName,
    outcome: Outcome,
) -> ResearchInteropDependencies | DesignInteropDependencies:
    """Return discovery/invocation fakes for one MCP matrix cell."""
    invoke = AsyncMock()
    if outcome == "completed":
        invoke.return_value = {"answer": "bounded peer evidence"}
    elif outcome == "failure":
        invoke.side_effect = InteropMCPError(
            "peer failed", code="remote_tool_error"
        )
    else:
        invoke.side_effect = TimeoutError("peer timed out")
    discovery = AsyncMock(
        return_value=DiscoveryResult(data=(_capability(agent, "mcp"),))
    )
    registry = _registry(agent, "mcp")
    if agent == "research":
        return ResearchInteropDependencies(
            registry=registry,
            discover=discovery,
            invoke=invoke,
        )
    return DesignInteropDependencies(
        registry=registry,
        discover=discovery,
        invoke=invoke,
    )


async def _collect(
    agent: AgentName,
    transport: Transport,
    mode: InteropMode,
    outcome: Outcome,
) -> Mapping[str, Any] | None:
    """Dispatch one matrix cell through the public agent adapter."""
    target_id = f"{agent}-{transport}-peer"
    target_ids = [target_id]
    if transport == "mcp":
        dependencies = _mcp_dependencies(agent, outcome)
        if agent == "research":
            return await collect_research_evidence(
                _research_task(),
                mode=mode,
                target_ids=target_ids,
                dependencies=cast(ResearchInteropDependencies, dependencies),
            )
        return await collect_design_evidence(
            _design_task(),
            mode=mode,
            target_ids=target_ids,
            dependencies=cast(DesignInteropDependencies, dependencies),
        )

    async def stream(*args: Any, **kwargs: Any):
        del args, kwargs
        async for item in _a2a_stream(agent, outcome):
            yield item

    registry = _registry(agent, "a2a")
    discovery = AsyncMock(
        return_value=DiscoveryResult(data=(_capability(agent, "a2a"),))
    )
    if agent == "research":
        return await collect_research_a2a(
            _research_task(),
            mode=mode,
            target_ids=target_ids,
            dependencies=ResearchInteropDependencies(
                registry=registry,
                discover_a2a=discovery,
                stream_a2a=stream,
            ),
        )
    return await collect_design_a2a(
        _design_task(),
        mode=mode,
        target_ids=target_ids,
        dependencies=DesignInteropDependencies(
            registry=registry,
            discover_a2a=discovery,
            stream_a2a=stream,
        ),
    )


@pytest.mark.parametrize("agent", ["research", "design"])
@pytest.mark.parametrize("mode", ["off", "auto", "required"])
@pytest.mark.parametrize("transport", ["mcp", "a2a"])
@pytest.mark.parametrize("outcome", ["completed", "failure", "timeout"])
async def test_research_and_design_policy_matrix(
    agent: AgentName,
    mode: InteropMode,
    transport: Transport,
    outcome: Outcome,
) -> None:
    """Both agents apply the same off/auto/required failure contract."""
    if mode == "required" and outcome != "completed":
        error: tuple[type[Exception], ...] = (InteropMCPError, RuntimeError)
        if transport == "a2a":
            error = (RuntimeError,)
        with pytest.raises(error):
            await _collect(agent, transport, mode, outcome)
        return

    result = await _collect(agent, transport, mode, outcome)
    if mode == "off" or outcome != "completed":
        assert result is None
    else:
        assert result is not None
        assert result["content"]


@pytest.mark.parametrize("agent", ["research", "design"])
@pytest.mark.parametrize("mode", ["off", "auto", "required"])
async def test_a2a_input_required_policy_matrix(
    agent: AgentName,
    mode: InteropMode,
) -> None:
    """A2A input-required is surfaced for enabled modes and skipped off."""
    if mode == "off":
        return

    target_id = f"{agent}-a2a-peer"
    dependencies = _registry(agent, "a2a")
    discovery = AsyncMock(
        return_value=DiscoveryResult(data=(_capability(agent, "a2a"),))
    )

    async def input_required_stream(*_: Any, **__: Any):
        yield _event(agent, TaskState.TASK_STATE_INPUT_REQUIRED)

    if agent == "research":
        result = await collect_research_a2a(
            _research_task(),
            mode=mode,
            target_ids=[target_id],
            dependencies=ResearchInteropDependencies(
                registry=dependencies,
                discover_a2a=discovery,
                stream_a2a=input_required_stream,
            ),
        )
    else:
        result = await collect_design_a2a(
            _design_task(),
            mode=mode,
            target_ids=[target_id],
            dependencies=DesignInteropDependencies(
                registry=dependencies,
                discover_a2a=discovery,
                stream_a2a=input_required_stream,
            ),
        )
    assert result is not None
    assert result["status"] == "input_required"
    assert result["task_id"]
