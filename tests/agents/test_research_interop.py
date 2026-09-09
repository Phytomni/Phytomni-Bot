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
from a2a.types import TaskState
from langgraph.checkpoint.memory import InMemorySaver

import mcp_server_phytomni.agents.research.interop as interop_module
from mcp_server_phytomni.agents.analyst.agent import AnalystAgent
from mcp_server_phytomni.agents.research.agent import (
    InSilicoResearchAgents,
    InSilicoResearchConfig,
    ResearchTaskContext,
    ResearchTaskInterop,
)
from mcp_server_phytomni.agents.research.interop import (
    RESEARCH_A2A_CAPABILITY,
    RESEARCH_MCP_CAPABILITY,
    ResearchA2APending,
    ResearchInteropDependencies,
    bound_research_evidence,
    collect_research_a2a,
    collect_research_evidence,
)
from mcp_server_phytomni.agents.shared.remote_analysis import (
    RemoteAnalysisRequest,
)
from mcp_server_phytomni.config.models.agents import ComputeResourceName
from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.interop.a2a_client import InteropA2AClientError
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
from mcp_server_phytomni.interop.planner import (
    InteropMode,
    InteropPlanningError,
)
from mcp_server_phytomni.interop.registry import InteropRegistry
from mcp_server_phytomni.runtime.resume import aresume_graph, detect_interrupt

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
    return InteropRegistry(_targets={target.id: target})


def _a2a_registry() -> InteropRegistry:
    """Build one operator-owned A2A target for offline tests."""
    target = A2ATarget.model_validate(
        {
            "id": "peer-a2a",
            "kind": "a2a",
            "transport": "a2a",
            "card_base_url": "https://research-peer.example.test/card",
            "allowed_interface_origins": [
                "https://research-peer.example.test"
            ],
            "allowed_interface_bindings": ["JSONRPC"],
            "allowed_skills": [RESEARCH_A2A_CAPABILITY],
        }
    )
    return InteropRegistry(_targets=dict([(target.id, target)]))


def _a2a_capability() -> InteropCapability:
    """Build a metadata-only Research A2A skill."""
    return InteropCapability(
        target_id="peer-a2a",
        kind="a2a",
        remote_name=RESEARCH_A2A_CAPABILITY,
        qualified_name=f"peer-a2a__{RESEARCH_A2A_CAPABILITY}",
        description="research peer",
        input_schema={},
    )


def _a2a_event(
    state: TaskState,
    *,
    text: str,
    task_id: str = "remote-task",
    context_id: str = "remote-context",
) -> ExternalA2AEvent:
    """Return a bounded normalized A2A status event."""
    return ExternalA2AEvent(
        kind="status_update",
        identity=ExternalA2AIdentity("peer-a2a", RESEARCH_A2A_CAPABILITY),
        task_id=task_id,
        context_id=context_id,
        state=TaskState.Name(state),
        parts=(ExternalA2APart("text", text),),
    )


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
    """An auto request falls back when the registry has no matching target."""
    assert (
        await collect_research_evidence(
            _task(),
            mode="auto",
            target_ids=["peer"],
            dependencies=ResearchInteropDependencies(
                registry=InteropRegistry(),
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


@pytest.mark.parametrize("compute_resource", [None, "medium", "large"])
async def test_worker_keeps_local_analyst_dispatch_and_attaches_evidence(
    monkeypatch: pytest.MonkeyPatch,
    compute_resource: ComputeResourceName | None,
) -> None:
    """External evidence augments, but does not replace, local dispatch."""
    analyst_stub = SimpleNamespace(arun=AsyncMock())
    if compute_resource is None:
        config = InSilicoResearchConfig()
    else:
        config = InSilicoResearchConfig(COMPUTE_RESOURCE=compute_resource)
    agent = InSilicoResearchAgents(
        in_silico_config=config,
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
    submit = AsyncMock()
    submit.return_value = {
        "task_id": "local-task",
        "output_dir": "/tmp/research-out",
        "task_status": "SUCCEEDED",
    }
    submit_path = (
        "mcp_server_phytomni.agents.research.agent.submit_remote_analysis"
    )
    monkeypatch.setattr(submit_path, submit)

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
    assert isinstance(request, RemoteAnalysisRequest)
    assert request.analysis_type == "research_goal_0"
    assert request.target_id == "research_goal_0"
    assert request.goal_description == "Characterize PHYB"
    assert request.meta.startswith("local context\n\n")
    assert "[UNTRUSTED EXTERNAL MCP EVIDENCE]" in request.meta
    assert request.data_list == {}
    assert request.output_dir == "/tmp/research-out"
    assert request.compute_resource == (compute_resource or "small")
    assert submit_call.kwargs["is_polling"] is False

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
    assert cast(dict[str, Any], updates)["evidence"] == [evidence]


async def test_required_research_never_pseudo_succeeds_without_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Required mode rejects an empty external-evidence result."""
    analyst_stub = SimpleNamespace(arun=AsyncMock())
    agent = InSilicoResearchAgents(
        in_silico_config=InSilicoResearchConfig(),
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, analyst_stub),
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.research.agent.collect_research_evidence",
        AsyncMock(return_value=None),
    )

    with pytest.raises(
        RuntimeError, match="required Research interop produced no external"
    ):
        await getattr(agent, "_submit_research_task")(
            ResearchTaskContext(
                goal_description="Characterize PHYB",
                context="local context",
                data_list={},
                output_dir="/tmp/research-out",
                task_name="research_goal_0",
                thread_id="thread-0",
                interop=ResearchTaskInterop(
                    mode="required", targets=("peer",)
                ),
            )
        )
    analyst_stub.arun.assert_not_awaited()


async def test_a2a_stream_is_bounded_redacted_and_marked() -> None:
    """A2A text/data events become bounded untrusted Research evidence."""
    calls: list[dict[str, Any]] = []

    async def stream(*args: Any, **kwargs: Any):
        """Record the operator-selected send and emit one terminal event."""
        calls.append({"args": args, "kwargs": kwargs})
        yield _a2a_event(
            TaskState.TASK_STATE_COMPLETED,
            text="token=peer-secret https://peer.example.test/private "
            + "x" * 40_000,
        )

    result = await collect_research_a2a(
        _task(),
        mode="required",
        target_ids=["peer-a2a"],
        dependencies=ResearchInteropDependencies(
            registry=_a2a_registry(),
            discover_a2a=AsyncMock(
                return_value=DiscoveryResult(data=(_a2a_capability(),))
            ),
            stream_a2a=stream,
        ),
    )

    assert result is not None
    assert result["status"] == "completed"
    assert result["target_id"] == "peer-a2a"
    assert result["task_id"] == "remote-task"
    assert result["truncated"] is True
    assert "peer-secret" not in result["content"]
    assert "https://peer.example.test" not in result["content"]
    assert calls[0]["kwargs"]["text"] == "Characterize PHYB"
    assert calls[0]["kwargs"]["data"]["task_name"] == "research_goal_0"


async def test_a2a_input_required_preserves_resume_correlations() -> None:
    """Input-required results preserve task/context ids for continuation."""
    calls: list[dict[str, Any]] = []
    responses = [
        _a2a_event(TaskState.TASK_STATE_INPUT_REQUIRED, text="choose"),
        _a2a_event(TaskState.TASK_STATE_COMPLETED, text="done"),
    ]

    async def stream(*args: Any, **kwargs: Any):
        """Emit input-required first and completion on the resume call."""
        calls.append({"args": args, "kwargs": kwargs})
        yield responses[len(calls) - 1]

    dependencies = ResearchInteropDependencies(
        registry=_a2a_registry(),
        discover_a2a=AsyncMock(
            return_value=DiscoveryResult(data=(_a2a_capability(),))
        ),
        stream_a2a=stream,
    )
    first = await collect_research_a2a(
        _task(),
        mode="required",
        target_ids=["peer-a2a"],
        dependencies=dependencies,
    )
    assert first is not None
    assert first["status"] == "input_required"

    pending = ResearchA2APending(
        task_name="research_goal_0",
        goal_description="Characterize PHYB",
        context="rice drought response",
        data_list={"rice.tsv": "expression"},
        output_dir="/tmp/research-out",
        thread_id="thread-0",
        target_id="peer-a2a",
        capability=RESEARCH_A2A_CAPABILITY,
        task_id=first["task_id"] or "",
        context_id=first["context_id"],
        draft=first["content"],
    )
    second = await collect_research_a2a(
        _task(),
        mode="required",
        target_ids=["peer-a2a"],
        dependencies=dependencies,
        resume={"text": "yes"},
        pending=pending,
    )
    assert second is not None
    assert second["status"] == "completed"
    assert calls[1]["kwargs"]["task_id"] == "remote-task"
    assert calls[1]["kwargs"]["context_id"] == "remote-context"
    assert calls[1]["kwargs"]["text"] == "yes"


@pytest.mark.parametrize("mode", ["auto", "required"])
async def test_a2a_transport_failure_obeys_mode(mode: InteropMode) -> None:
    """A2A transport failure falls back only in auto mode."""
    dependencies = ResearchInteropDependencies(
        registry=_a2a_registry(),
        discover_a2a=AsyncMock(
            return_value=DiscoveryResult(data=(_a2a_capability(),))
        ),
        stream_a2a=AsyncMock(side_effect=RuntimeError("peer unavailable")),
    )
    if mode == "auto":
        assert (
            await collect_research_a2a(
                _task(),
                mode=mode,
                target_ids=["peer-a2a"],
                dependencies=dependencies,
            )
            is None
        )
    else:
        with pytest.raises(InteropA2AClientError, match="external A2A"):
            await collect_research_a2a(
                _task(),
                mode=mode,
                target_ids=["peer-a2a"],
                dependencies=dependencies,
            )


async def test_research_graph_interrupts_and_resumes_a2a_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The graph persists ids before pause and does not resend on replay."""
    calls: list[dict[str, Any]] = []

    async def stream(*args: Any, **kwargs: Any):
        """Return input-required once, then a completed resumed event."""
        calls.append({"args": args, "kwargs": kwargs})
        state = (
            TaskState.TASK_STATE_INPUT_REQUIRED
            if len(calls) == 1
            else TaskState.TASK_STATE_COMPLETED
        )
        yield _a2a_event(state, text="choose" if len(calls) == 1 else "done")

    analyst_stub = SimpleNamespace(arun=AsyncMock())
    agent = InSilicoResearchAgents(
        checkpointer=InMemorySaver(),
        in_silico_config=InSilicoResearchConfig(),
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, analyst_stub),
        interop_dependencies=ResearchInteropDependencies(
            registry=_a2a_registry(),
            discover_a2a=AsyncMock(
                return_value=DiscoveryResult(data=(_a2a_capability(),))
            ),
            stream_a2a=stream,
        ),
    )

    async def extract(
        _query: str,
        _files: list[str],
        _locale: str | None,
    ) -> list[dict[str, str]]:
        """Return one deterministic goal for the graph pause test."""
        del _locale
        return [{"goal": "Characterize PHYB", "context": "drought"}]

    submit = AsyncMock(
        return_value={
            "task_id": "local-task",
            "output_dir": "/tmp/research-out",
            "task_status": "SUCCEEDED",
        }
    )
    monkeypatch.setattr(agent, "_extract_goals", extract)
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.research.agent.submit_remote_analysis",
        submit,
    )

    state: dict[str, Any] = {
        "paper_text": "PHYB",
        "data_list": {},
        "obs_file_list": [],
        "user_id": "test-user",
        "output_dir": "/tmp/research-out",
        "interop_mode": "required",
        "interop_targets": ["peer-a2a"],
        "task_ids": {},
        "completed_count": 0,
        "error": None,
        "goals": [],
        "research_tasks": [],
        "evidence": [],
        "a2a_pending": [],
        "a2a_task_ids": {},
    }
    paused = await agent.app.ainvoke(
        state,
        config={"configurable": {"thread_id": "research-a2a-pause"}},
    )
    info = detect_interrupt(paused, "research-a2a-pause")
    assert info is not None
    assert info["draft"]["task_id"] == "remote-task"
    assert paused["a2a_task_ids"] == {"research_goal_0": "remote-task"}
    assert len(calls) == 1

    final = await aresume_graph(
        agent.app,
        "research-a2a-pause",
        {"text": "yes"},
    )
    assert final["task_ids"] == {"research_goal_0": "local-task"}
    assert final["evidence"][0]["kind"] == "a2a"
    assert len(calls) == 2
    assert calls[1]["kwargs"]["task_id"] == "remote-task"
    submit.assert_awaited_once()
