# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Research-only adapter for bounded external MCP evidence."""

from __future__ import annotations

import json
from collections.abc import (
    Awaitable,
    Callable,
    Mapping,
    MutableMapping,
    Sequence,
)
from typing import Any, NamedTuple, TypedDict

from ...common.redaction import redact_secrets
from ...config.settings import SensitiveConfig
from ...interop.cache import DiscoveryCache
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

RESEARCH_MCP_CAPABILITY = "research"
MAX_RESEARCH_EVIDENCE_BYTES = 32 * 1024


class ResearchEvidence(TypedDict):
    """Bounded, sanitized evidence returned by one external MCP tool."""

    target_id: str
    kind: str
    capability: str
    content: str
    truncated: bool


DiscoverMcp = Callable[..., Awaitable[DiscoveryResult]]
InvokeMcp = Callable[..., Awaitable[Any]]


class ResearchInteropDependencies(NamedTuple):
    """Injectable registry, cache, and transport seams for Research."""

    registry: InteropRegistry | None = None
    sensitive_config: SensitiveConfig | None = None
    caches: MutableMapping[str, DiscoveryCache] | None = None
    discover: DiscoverMcp | None = None
    invoke: InvokeMcp | None = None
    capability_name: str = RESEARCH_MCP_CAPABILITY


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
    return (
        "[UNTRUSTED EXTERNAL MCP EVIDENCE]\n"
        f"target_id={evidence['target_id']}\n"
        f"capability={evidence['capability']}\n"
        f"{evidence['content']}\n"
        "[/UNTRUSTED EXTERNAL MCP EVIDENCE]"
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
    cache = caches.get(target.id)
    if cache is None:
        cache = DiscoveryCache(ttl_seconds=target.discovery_ttl_seconds)
        caches[target.id] = cache
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
    "RESEARCH_MCP_CAPABILITY",
    "ResearchInteropDependencies",
    "ResearchEvidence",
    "bound_research_evidence",
    "build_research_mcp_arguments",
    "collect_research_evidence",
    "format_research_evidence",
    "research_task_spec",
]
