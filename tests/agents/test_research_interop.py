# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Contract tests for Research's opt-in external MCP evidence seam."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

import mcp_server_phytomni.agents.research.interop as interop_module
from mcp_server_phytomni.agents.analyst.agent import AnalystAgent
from mcp_server_phytomni.agents.research.agent import (
    InSilicoResearchAgents,
    InSilicoResearchConfig,
    ResearchTaskContext,
    ResearchTaskInterop,
)
from mcp_server_phytomni.agents.research.interop import (
    RESEARCH_MCP_CAPABILITY,
    ResearchInteropDependencies,
    bound_research_evidence,
    collect_research_evidence,
)
from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.interop.capabilities import (
    DiscoveryResult,
    InteropCapability,
)
from mcp_server_phytomni.interop.mcp_client import InteropMCPError
from mcp_server_phytomni.interop.models import MCPStreamableHttpTarget
from mcp_server_phytomni.interop.planner import InteropPlanningError
from mcp_server_phytomni.interop.registry import InteropRegistry

pytestmark = pytest.mark.agent


def _registry() -> InteropRegistry:
    """Build one operator-owned MCP target for offline tests."""
    target = MCPStreamableHttpTarget.model_validate(
        {
            "id": "peer",
            "kind": "mcp",
            "transport": "streamable_http",
            "url": "https://peer.example.test/mcp",
            "allowed_tools": [RESEARCH_MCP_CAPABILITY],
        }
    )
    return InteropRegistry(enabled=True, _targets={target.id: target})


def _capability() -> InteropCapability:
    """Build a compatible metadata-only capability DTO."""
    properties = {
        "goal_description": {"type": "string"},
        "context": {"type": "string"},
        "data_list": {"type": "object"},
        "task_name": {"type": "string"},
    }
    return InteropCapability(
        target_id="peer",
        kind="mcp",
        remote_name=RESEARCH_MCP_CAPABILITY,
        qualified_name=f"peer__{RESEARCH_MCP_CAPABILITY}",
        description="research evidence",
        input_schema={
            "type": "object",
            "properties": properties,
            "required": ["goal_description"],
        },
    )


def _task() -> dict[str, Any]:
    """Return one deterministic Research task mapping."""
    return {
        "goal_description": "Characterize PHYB",
        "context": "rice drought response",
        "data_list": {"rice.tsv": "expression"},
        "task_name": "research_goal_0",
    }


async def test_off_mode_never_discovers_or_invokes() -> None:
    """The default path remains entirely local."""

    async def forbidden(*_: Any, **__: Any) -> DiscoveryResult:
        """Fail if the disabled request reaches the peer boundary."""
        raise AssertionError("off mode must not discover")

    result = await collect_research_evidence(
        _task(),
        mode="off",
        target_ids=["peer"],
        dependencies=ResearchInteropDependencies(
            discover=forbidden,
            invoke=forbidden,
        ),
    )
    assert result is None


async def test_auto_mode_falls_back_without_a_compatible_capability() -> None:
    """Auto mode does not invoke an unplanned tool."""
    discover = AsyncMock(return_value=DiscoveryResult())
    invoke = AsyncMock()

    result = await collect_research_evidence(
        _task(),
        mode="auto",
        target_ids=["peer"],
        dependencies=ResearchInteropDependencies(
            registry=_registry(),
            discover=discover,
            invoke=invoke,
        ),
    )

    assert result is None
    discover.assert_awaited_once()
    invoke.assert_not_awaited()


async def test_required_mode_fails_closed_without_a_candidate() -> None:
    """Required mode surfaces the planner error instead of local fallback."""
    with pytest.raises(
        InteropPlanningError,
        match="required interop request has no eligible capability",
    ):
        await collect_research_evidence(
            _task(),
            mode="required",
            target_ids=["peer"],
            dependencies=ResearchInteropDependencies(
                registry=_registry(),
                discover=AsyncMock(return_value=DiscoveryResult()),
                invoke=AsyncMock(),
            ),
        )


async def test_selected_result_is_bounded_redacted_and_marked() -> None:
    """A selected peer result becomes bounded, non-secret evidence."""
    discover = AsyncMock(return_value=DiscoveryResult(data=(_capability(),)))
    invoke = AsyncMock(
        return_value={
            "answer": "token=peer-secret https://peer.example.test/private "
            + "x" * 40_000,
        }
    )

    result = await collect_research_evidence(
        _task(),
        mode="required",
        target_ids=["peer"],
        dependencies=ResearchInteropDependencies(
            registry=_registry(),
            discover=discover,
            invoke=invoke,
        ),
    )

    assert result is not None
    assert result["target_id"] == "peer"
    assert result["capability"] == RESEARCH_MCP_CAPABILITY
    assert result["truncated"] is True
    assert "peer-secret" not in result["content"]
    assert "https://peer.example.test" not in result["content"]
    assert "<redacted-secret>" in result["content"]
    invoke.assert_awaited_once()
    invoke_call = invoke.await_args
    assert invoke_call is not None
    assert invoke_call.kwargs["registry"].enabled is True


def test_bound_evidence_respects_utf8_byte_cap() -> None:
    """The evidence cap is measured in bytes, not Python code points."""
    evidence = bound_research_evidence(
        "水" * 100,
        target_id="peer",
        capability=RESEARCH_MCP_CAPABILITY,
        max_bytes=32,
    )

    assert len(evidence["content"].encode("utf-8")) <= 32
    assert evidence["truncated"] is True


def test_evidence_handles_bytes_short_values_and_tiny_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The boundary normalizes bytes and handles both cap edge cases."""
    bytes_evidence = interop_module.bound_research_evidence(
        SimpleNamespace(content=b"peer-bytes"),
        target_id="peer",
        capability=RESEARCH_MCP_CAPABILITY,
    )
    assert bytes_evidence["content"] == "peer-bytes"

    short_evidence = interop_module.bound_research_evidence(
        "short",
        target_id="peer",
        capability=RESEARCH_MCP_CAPABILITY,
        max_bytes=32,
    )
    assert short_evidence["truncated"] is False

    tiny_evidence = interop_module.bound_research_evidence(
        "long",
        target_id="peer",
        capability=RESEARCH_MCP_CAPABILITY,
        max_bytes=1,
    )
    assert len(tiny_evidence["content"].encode("utf-8")) <= 1
    assert tiny_evidence["truncated"] is True

    def fail_json(*_: Any, **__: Any) -> str:
        """Force the deterministic string fallback for malformed results."""
        raise ValueError("serialization failed")

    monkeypatch.setattr(interop_module.json, "dumps", fail_json)
    fallback_evidence = interop_module.bound_research_evidence(
        SimpleNamespace(),
        target_id="peer",
        capability=RESEARCH_MCP_CAPABILITY,
    )
    assert fallback_evidence["content"] == "namespace()"


def test_evidence_rejects_non_positive_caps() -> None:
    """A non-positive cap cannot produce a valid evidence payload."""
    with pytest.raises(ValueError, match="max_bytes must be positive"):
        interop_module.bound_research_evidence(
            "result",
            target_id="peer",
            capability=RESEARCH_MCP_CAPABILITY,
            max_bytes=0,
        )


async def test_discovery_skips_unknown_targets_and_reuses_cache() -> None:
    """Unknown ids are ignored and configured discovery caches are reused."""
    discover = AsyncMock(return_value=DiscoveryResult())
    dependencies = ResearchInteropDependencies(
        registry=_registry(),
        caches={},
        discover=discover,
    )

    assert (
        await collect_research_evidence(
            _task(),
            mode="auto",
            target_ids=["unknown", "peer"],
            dependencies=dependencies,
        )
        is None
    )
    assert (
        await collect_research_evidence(
            _task(),
            mode="auto",
            target_ids=["peer"],
            dependencies=dependencies,
        )
        is None
    )
    assert discover.await_count == 2


async def test_active_request_without_registry_stays_local() -> None:
    """An enabled request still falls back when the registry is disabled."""
    assert (
        await collect_research_evidence(
            _task(),
            mode="auto",
            target_ids=["peer"],
            dependencies=ResearchInteropDependencies(
                registry=InteropRegistry.disabled(),
            ),
        )
        is None
    )


async def test_discovery_failure_isolated_in_auto_mode() -> None:
    """A target discovery failure preserves the local auto fallback."""
    discover = AsyncMock(side_effect=TimeoutError("peer unavailable"))
    assert (
        await collect_research_evidence(
            _task(),
            mode="auto",
            target_ids=["peer"],
            dependencies=ResearchInteropDependencies(
                registry=_registry(),
                discover=discover,
            ),
        )
        is None
    )


async def test_auto_mode_falls_back_when_invocation_fails() -> None:
    """Auto mode converts an invocation failure into local execution."""
    assert (
        await collect_research_evidence(
            _task(),
            mode="auto",
            target_ids=["peer"],
            dependencies=ResearchInteropDependencies(
                registry=_registry(),
                discover=AsyncMock(
                    return_value=DiscoveryResult(data=(_capability(),))
                ),
                invoke=AsyncMock(side_effect=RuntimeError("peer failed")),
            ),
        )
        is None
    )


async def test_required_mode_surfaces_invocation_error() -> None:
    """Required mode reports a stable external MCP invocation error."""
    with pytest.raises(InteropMCPError, match="peer failed"):
        await collect_research_evidence(
            _task(),
            mode="required",
            target_ids=["peer"],
            dependencies=ResearchInteropDependencies(
                registry=_registry(),
                discover=AsyncMock(
                    return_value=DiscoveryResult(data=(_capability(),))
                ),
                invoke=AsyncMock(
                    side_effect=InteropMCPError(
                        "peer failed",
                        code="remote_tool_error",
                    )
                ),
            ),
        )


async def test_worker_keeps_local_analyst_dispatch_and_attaches_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """External evidence augments, but does not replace, local dispatch."""
    analyst_stub = SimpleNamespace(arun=AsyncMock())
    agent = InSilicoResearchAgents(
        in_silico_config=InSilicoResearchConfig(),
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, analyst_stub),
    )
    evidence = {
        "target_id": "peer",
        "kind": "mcp",
        "capability": RESEARCH_MCP_CAPABILITY,
        "content": "bounded evidence",
        "truncated": False,
    }
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.research.agent.collect_research_evidence",
        AsyncMock(return_value=evidence),
    )
    submit = AsyncMock(
        return_value={
            "task_id": "local-task",
            "output_dir": "/tmp/research-out",
            "task_status": "SUCCEEDED",
        }
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.research.agent."
        "submit_analyst_via_subgraph",
        submit,
    )

    submit_task = getattr(agent, "_submit_research_task")
    result = await submit_task(
        ResearchTaskContext(
            goal_description="Characterize PHYB",
            context="local context",
            data_list={},
            output_dir="/tmp/research-out",
            task_name="research_goal_0",
            thread_id="thread-0",
            interop=ResearchTaskInterop(mode="auto", targets=("peer",)),
        )
    )

    assert result["task_id"] == "local-task"
    assert result["evidence"] == evidence
    submit_call = submit.await_args
    assert submit_call is not None
    request = submit_call.args[3]
    assert "[UNTRUSTED EXTERNAL MCP EVIDENCE]" in request["prompt_parts"][1]

    updates = await agent.run_research_node(
        cast(
            Any,
            {
                "task_index": 0,
                "task_name": "research_goal_0",
                "goal_description": "Characterize PHYB",
                "context": "local context",
                "data_list": {},
                "output_dir": "/tmp/research-out",
                "thread_id": "thread-0",
                "interop_mode": "auto",
                "interop_targets": ["peer"],
                "task_ids": {},
                "evidence": [],
            },
        )
    )
    assert updates["evidence"] == [evidence]
