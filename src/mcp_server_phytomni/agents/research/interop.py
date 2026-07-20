# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Research-only adapter for bounded external MCP evidence."""

from __future__ import annotations

import inspect
import json
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    MutableMapping,
    Sequence,
)
from typing import Any, Literal, NamedTuple, TypedDict, cast

from ...common.redaction import redact_secrets
from ...config.defaults import ApiConfig
from ...config.settings import SensitiveConfig
from ...interop.a2a_client import (
    InteropA2AClientError,
    send_external_a2a_task,
)
from ...interop.a2a_discovery import (
    InteropA2AError,
    discover_external_a2a_capabilities,
)
from ...interop.a2a_mapping import ExternalA2AEvent
from ...interop.cache import (
    DiscoveryCache,
    get_or_create_discovery_cache,
)
from ...interop.capabilities import (
    DiscoveryResult,
    InteropCapability,
    discover_external_mcp_capabilities,
)
from ...interop.mcp_client import InteropMCPError, invoke_external_mcp_tool
from ...interop.models import InteropTarget
from ...interop.planner import (
    InteropMode,
    InteropTaskSpec,
    plan_interop_capabilities,
)
from ...interop.registry import InteropRegistry, InteropRegistryError
from ..shared.interop import (
    InteropA2APending,
    InteropA2AResult,
    InteropEvidence,
)

RESEARCH_MCP_CAPABILITY = "research"
RESEARCH_A2A_CAPABILITY = "research"
MAX_RESEARCH_EVIDENCE_BYTES = 32 * 1024
RESEARCH_INTEROP_FAILURES: tuple[type[Exception], ...] = (
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


ResearchEvidence = InteropEvidence
ResearchA2AResult = InteropA2AResult


class ResearchA2APending(InteropA2APending):
    """Safe state carried from an A2A pause to the resume node."""

    task_name: str


class _ResearchA2AStreamRequest(TypedDict):
    """Internal A2A stream request bundle kept out of public state."""

    candidate: InteropCapability
    task: Mapping[str, Any]
    registry: InteropRegistry
    dependencies: ResearchInteropDependencies
    resume: Mapping[str, Any] | None
    pending: ResearchA2APending | None


class _ResearchA2ACollectionRequest(TypedDict):
    """Internal policy request for one Research A2A exchange."""

    task: Mapping[str, Any]
    mode: InteropMode
    target_ids: Sequence[str]
    dependencies: ResearchInteropDependencies | None
    resume: Mapping[str, Any] | None
    pending: ResearchA2APending | None


DiscoverMcp = Callable[..., Awaitable[DiscoveryResult]]
InvokeMcp = Callable[..., Awaitable[Any]]
DiscoverA2A = Callable[..., Awaitable[DiscoveryResult]]
StreamA2A = Callable[..., AsyncIterator[ExternalA2AEvent]]


class ResearchInteropDependencies(NamedTuple):
    """Injectable registry, cache, and transport seams for Research."""

    registry: InteropRegistry | None = None
    sensitive_config: SensitiveConfig | None = None
    caches: MutableMapping[str, DiscoveryCache] | None = None
    discover: DiscoverMcp | None = None
    invoke: InvokeMcp | None = None
    capability_name: str = RESEARCH_MCP_CAPABILITY
    discover_a2a: DiscoverA2A | None = None
    stream_a2a: StreamA2A | None = None
    a2a_capability_name: str = RESEARCH_A2A_CAPABILITY


def research_task_spec(
    *,
    capability_name: str = RESEARCH_MCP_CAPABILITY,
) -> InteropTaskSpec:
    """Return the local schema used to select a Research MCP capability."""
    return InteropTaskSpec(
        name=capability_name,
        input_schema={
            "type": "object",
            "properties": {
                "goal_description": {"type": "string"},
                "context": {"type": "string"},
                "data_list": {"type": "object"},
                "task_name": {"type": "string"},
            },
        },
    )


def build_research_mcp_arguments(
    task: Mapping[str, Any],
) -> dict[str, Any]:
    """Project one Research task into an external tool argument object."""
    return {
        "goal_description": str(task.get("goal_description", "")),
        "context": str(task.get("context", "")),
        "data_list": dict(task.get("data_list") or {}),
        "task_name": str(task.get("task_name", "")),
    }


def _result_text(result: Any) -> str:
    """Detach one untrusted tool result into deterministic text."""
    content = getattr(result, "content", result)
    if isinstance(content, bytes):
        text = content.decode("utf-8", errors="replace")
    elif isinstance(content, str):
        text = content
    else:
        try:
            text = json.dumps(
                content,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        except (TypeError, ValueError):
            text = str(content)
    return redact_secrets(text)


def _bound_text(text: str, max_bytes: int) -> tuple[str, bool]:
    """Keep UTF-8 evidence within the configured byte budget."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    marker = "\n<evidence-truncated>"
    marker_bytes = marker.encode("utf-8")
    if len(marker_bytes) >= max_bytes:
        return encoded[:max_bytes].decode("utf-8", errors="ignore"), True
    head = encoded[: max_bytes - len(marker_bytes)].decode(
        "utf-8", errors="ignore"
    )
    return f"{head}{marker}", True


def bound_research_evidence(
    result: Any,
    *,
    target_id: str,
    capability: str,
    kind: str = "mcp",
    max_bytes: int = MAX_RESEARCH_EVIDENCE_BYTES,
) -> ResearchEvidence:
    """Convert an untrusted peer result into a bounded evidence DTO."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    content, truncated = _bound_text(_result_text(result), max_bytes)
    return ResearchEvidence(
        target_id=target_id,
        kind=kind,
        capability=capability,
        content=content,
        truncated=truncated,
    )


def format_research_evidence(evidence: ResearchEvidence) -> str:
    """Mark evidence as untrusted before it enters a model prompt."""
    kind = "A2A" if evidence.get("kind") == "a2a" else "MCP"
    return (
        f"[UNTRUSTED EXTERNAL {kind} EVIDENCE]\n"
        f"target_id={evidence['target_id']}\n"
        f"capability={evidence['capability']}\n"
        f"{evidence['content']}\n"
        f"[/UNTRUSTED EXTERNAL {kind} EVIDENCE]"
    )


def _target_for(
    registry: InteropRegistry,
    target_id: str,
) -> InteropTarget | None:
    """Return a configured target, hiding unknown request ids."""
    try:
        return registry.require_target(target_id)
    except InteropRegistryError:
        return None


async def _discover_target(
    target_id: str,
    *,
    registry: InteropRegistry,
    sensitive_config: SensitiveConfig | None,
    caches: MutableMapping[str, DiscoveryCache],
    discover: DiscoverMcp,
) -> DiscoveryResult:
    """Discover one MCP target through its metadata-only cache seam."""
    target = _target_for(registry, target_id)
    if target is None or target.kind != "mcp":
        return DiscoveryResult()
    cache = get_or_create_discovery_cache(
        caches,
        target,
        max_entries=ApiConfig().INTEROP_CACHE_MAX_ENTRIES,
    )
    return await discover(
        target.id,
        registry=registry,
        sensitive_config=sensitive_config,
        cache=cache,
    )


async def _discover_capabilities(
    target_ids: Sequence[str],
    dependencies: ResearchInteropDependencies,
) -> list[InteropCapability]:
    """Discover MCP capabilities while isolating target-level failures."""
    registry = dependencies.registry
    if registry is None or not registry.enabled:
        return []
    caches = dependencies.caches
    if caches is None:
        caches = {}
    discover = dependencies.discover or discover_external_mcp_capabilities
    capabilities: list[InteropCapability] = []
    for target_id in target_ids:
        try:
            result = await _discover_target(
                target_id,
                registry=registry,
                sensitive_config=dependencies.sensitive_config,
                caches=caches,
                discover=discover,
            )
        except (
            InteropMCPError,
            OSError,
            RuntimeError,
            TimeoutError,
            TypeError,
            ValueError,
        ):
            continue
        capabilities.extend(result.data)
    return capabilities


async def _discover_a2a_capabilities(
    target_ids: Sequence[str],
    dependencies: ResearchInteropDependencies,
) -> list[InteropCapability]:
    """Discover A2A skills while isolating target-level failures."""
    registry = dependencies.registry
    if registry is None or not registry.enabled:
        return []
    caches = dependencies.caches
    if caches is None:
        caches = {}
    discover = dependencies.discover_a2a or discover_external_a2a_capabilities
    capabilities: list[InteropCapability] = []
    for target_id in target_ids:
        target = _target_for(registry, target_id)
        if target is None or target.kind != "a2a":
            continue
        cache = caches.get(target.id)
        if cache is None:
            cache = DiscoveryCache(
                ttl_seconds=target.discovery_ttl_seconds,
                max_entries=ApiConfig().INTEROP_CACHE_MAX_ENTRIES,
            )
            caches[target.id] = cache
        try:
            result = await discover(
                target.id,
                registry=registry,
                sensitive_config=dependencies.sensitive_config,
                cache=cache,
            )
        except (
            InteropA2AClientError,
            InteropA2AError,
            OSError,
            RuntimeError,
            TimeoutError,
            TypeError,
            ValueError,
        ):
            continue
        capabilities.extend(result.data)
    return capabilities


def build_research_a2a_message(
    task: Mapping[str, Any],
) -> dict[str, Any]:
    """Project one Research task into an A2A text/data message."""
    return {
        "text": str(task.get("goal_description", "")),
        "data": {
            "context": str(task.get("context", "")),
            "data_list": dict(task.get("data_list") or {}),
            "task_name": str(task.get("task_name", "")),
        },
    }


def _a2a_event_content(event: ExternalA2AEvent) -> list[str]:
    """Detach text/data parts from one untrusted A2A event."""
    values: list[str] = list(event.texts)
    values.extend(_result_text(value) for value in event.data_values)
    return values


def _a2a_state_status(event: ExternalA2AEvent) -> str | None:
    """Map one normalized A2A event to the Research exchange status."""
    state = event.state or ""
    if state.endswith("INPUT_REQUIRED"):
        return "input_required"
    if state.endswith(("FAILED", "CANCELED", "REJECTED", "AUTH_REQUIRED")):
        return "failed"
    if event.terminal:
        return "completed"
    return None


def _resume_message(
    resume: Mapping[str, Any] | None,
) -> tuple[str | None, Mapping[str, Any] | None]:
    """Extract a bounded text/data answer from a resume payload."""
    if resume is None:
        return None, None
    text = resume.get("text")
    if text is None:
        text = resume.get("answer", resume.get("input"))
    if text is not None and not isinstance(text, str):
        text = str(text)
    data = resume.get("data")
    return text, data if isinstance(data, Mapping) else None


async def _stream_research_a2a(
    request: _ResearchA2AStreamRequest,
) -> ResearchA2AResult:
    """Consume one external A2A stream into a bounded Research result."""
    stream, task_id, context_id = await _open_research_a2a_stream(request)
    content, truncated, status, task_id, context_id = (
        await _consume_research_a2a_stream(stream, task_id, context_id)
    )
    candidate = request["candidate"]
    return ResearchA2AResult(
        target_id=candidate.target_id,
        kind="a2a",
        capability=candidate.remote_name,
        status=status,
        task_id=task_id,
        context_id=context_id,
        content=content,
        truncated=truncated,
    )


async def _open_research_a2a_stream(
    request: _ResearchA2AStreamRequest,
) -> tuple[AsyncIterator[ExternalA2AEvent], str | None, str | None]:
    """Open one A2A stream and return its resumable correlation ids."""
    candidate = request["candidate"]
    dependencies = request["dependencies"]
    pending = request["pending"]
    message = build_research_a2a_message(request["task"])
    task_id = pending.get("task_id") if pending else None
    context_id = pending.get("context_id") if pending else None
    if request["resume"] is not None:
        text, data = _resume_message(request["resume"])
        message = {"text": text or "", "data": data}
    stream_fn = dependencies.stream_a2a or send_external_a2a_task
    stream = stream_fn(
        candidate.target_id,
        candidate.remote_name,
        registry=request["registry"],
        text=message["text"],
        data=message["data"],
        task_id=task_id,
        context_id=context_id,
        sensitive_config=dependencies.sensitive_config,
    )
    if inspect.isawaitable(stream):
        stream = await cast(Awaitable[AsyncIterator[ExternalA2AEvent]], stream)
    return stream, task_id, context_id


async def _consume_research_a2a_stream(
    stream: AsyncIterator[ExternalA2AEvent],
    task_id: str | None,
    context_id: str | None,
) -> tuple[
    str,
    bool,
    Literal["completed", "input_required", "failed"],
    str | None,
    str | None,
]:
    """Collect and bound one normalized A2A event stream."""
    values: list[str] = []
    status: Literal["completed", "input_required", "failed"] = "completed"
    async for event in stream:
        task_id = event.task_id or task_id
        context_id = event.context_id or context_id
        values.extend(_a2a_event_content(event))
        event_status = _a2a_state_status(event)
        if event_status is not None:
            status = cast(
                Literal["completed", "input_required", "failed"],
                event_status,
            )
        if event.terminal:
            break
    content, truncated = _bound_text(
        redact_secrets("\n".join(values)),
        MAX_RESEARCH_EVIDENCE_BYTES,
    )
    return content, truncated, status, task_id, context_id


async def collect_research_a2a(
    task: Mapping[str, Any],
    *,
    mode: InteropMode,
    target_ids: Sequence[str],
    dependencies: ResearchInteropDependencies | None = None,
    **options: Any,
) -> ResearchA2AResult | None:
    """Select and stream one external A2A Research capability.

    ``input_required`` is returned as a typed result so the graph worker can
    persist the remote task/context ids before entering its interrupt node.
    ``auto`` falls back to local execution on discovery, transport, timeout,
    or remote failure; ``required`` propagates a sanitized A2A error.
    """
    request = _ResearchA2ACollectionRequest(
        task=task,
        mode=mode,
        target_ids=target_ids,
        dependencies=dependencies,
        resume=cast(Mapping[str, Any] | None, options.get("resume")),
        pending=cast(ResearchA2APending | None, options.get("pending")),
    )
    return await _collect_research_a2a_impl(request)


async def _collect_research_a2a_impl(
    request: _ResearchA2ACollectionRequest,
) -> ResearchA2AResult | None:
    """Apply policy and invoke the selected A2A capability."""
    task = request["task"]
    mode = request["mode"]
    resolved = request["dependencies"] or ResearchInteropDependencies()
    pending = request["pending"]
    capability_name = (
        pending["capability"]
        if pending is not None
        else resolved.a2a_capability_name
    )
    task_spec = research_task_spec(capability_name=capability_name)
    if mode == "off":
        plan_interop_capabilities(
            task_spec,
            mode=mode,
            target_ids=request["target_ids"],
            capabilities=(),
        )
        return None
    registry = resolved.registry or InteropRegistry.disabled()
    if not registry.enabled:
        plan_interop_capabilities(
            task_spec,
            mode=mode,
            target_ids=request["target_ids"],
            capabilities=(),
        )
        return None
    discover_deps = resolved._replace(registry=registry)
    capabilities = await _discover_a2a_capabilities(
        request["target_ids"], discover_deps
    )
    plan = plan_interop_capabilities(
        task_spec,
        mode=mode,
        target_ids=request["target_ids"],
        capabilities=(item for item in capabilities if item.kind == "a2a"),
    )
    if plan.use_local_fallback:
        return None
    last_error: InteropA2AClientError | None = None
    for candidate in plan.candidates:
        try:
            result = await _stream_research_a2a(
                {
                    "candidate": candidate,
                    "task": task,
                    "registry": registry,
                    "dependencies": resolved,
                    "resume": request["resume"],
                    "pending": pending,
                }
            )
            if result["status"] == "failed":
                raise InteropA2AClientError(
                    "remote_failed", candidate.target_id
                )
            return result
        except InteropA2AClientError as exc:
            last_error = exc
        except RESEARCH_INTEROP_FAILURES:
            last_error = InteropA2AClientError(
                "invoke_failed",
                candidate.target_id,
            )
    if mode == "auto":
        return None
    if last_error is not None:
        raise last_error
    return None


async def collect_research_evidence(
    task: Mapping[str, Any],
    *,
    mode: InteropMode,
    target_ids: Sequence[str],
    dependencies: ResearchInteropDependencies | None = None,
) -> ResearchEvidence | None:
    """Select and invoke one external MCP evidence capability.

    The function consumes only sanitized discovery DTOs.  It returns ``None``
    for the local fallback path and never executes a peer in ``off`` mode.
    ``required`` propagates a stable planning or MCP boundary error instead of
    pretending that a local task satisfied the external request.
    """
    resolved_dependencies = dependencies or ResearchInteropDependencies()
    task_spec = research_task_spec(
        capability_name=resolved_dependencies.capability_name
    )
    if mode == "off":
        plan_interop_capabilities(
            task_spec,
            mode=mode,
            target_ids=target_ids,
            capabilities=(),
        )
        return None

    resolved_registry = (
        resolved_dependencies.registry or InteropRegistry.disabled()
    )
    if not resolved_registry.enabled:
        plan_interop_capabilities(
            task_spec,
            mode=mode,
            target_ids=target_ids,
            capabilities=(),
        )
        return None

    capabilities = await _discover_capabilities(
        target_ids,
        ResearchInteropDependencies(
            registry=resolved_registry,
            sensitive_config=resolved_dependencies.sensitive_config,
            caches=resolved_dependencies.caches,
            discover=resolved_dependencies.discover,
            capability_name=resolved_dependencies.capability_name,
        ),
    )

    plan = plan_interop_capabilities(
        task_spec,
        mode=mode,
        target_ids=target_ids,
        capabilities=(item for item in capabilities if item.kind == "mcp"),
    )
    if plan.use_local_fallback:
        return None

    invoke_fn = resolved_dependencies.invoke or invoke_external_mcp_tool
    last_error: InteropMCPError | None = None
    arguments = build_research_mcp_arguments(task)
    for candidate in plan.candidates:
        try:
            result = await invoke_fn(
                candidate.target_id,
                candidate.remote_name,
                arguments,
                registry=resolved_registry,
                sensitive_config=resolved_dependencies.sensitive_config,
            )
            return bound_research_evidence(
                result,
                target_id=candidate.target_id,
                capability=candidate.remote_name,
            )
        except InteropMCPError as exc:
            last_error = exc
        except (
            OSError,
            RuntimeError,
            TimeoutError,
            TypeError,
            ValueError,
        ):
            last_error = InteropMCPError(
                "external MCP invocation failed",
                code="invoke_failed",
                target_id=candidate.target_id,
                tool_name=candidate.remote_name,
            )

    if mode == "auto":
        return None
    if last_error is not None:
        raise last_error
    return None


__all__ = [
    "MAX_RESEARCH_EVIDENCE_BYTES",
    "RESEARCH_A2A_CAPABILITY",
    "RESEARCH_INTEROP_FAILURES",
    "RESEARCH_MCP_CAPABILITY",
    "ResearchA2APending",
    "ResearchA2AResult",
    "ResearchInteropDependencies",
    "ResearchEvidence",
    "bound_research_evidence",
    "build_research_a2a_message",
    "build_research_mcp_arguments",
    "collect_research_a2a",
    "collect_research_evidence",
    "format_research_evidence",
    "research_task_spec",
]
