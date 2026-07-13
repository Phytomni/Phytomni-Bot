# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Design adapter for bounded external planning evidence.

The transport and policy implementation lives in the Research adapter until
the common interop surface is extracted.  This module only projects Design's
safe planning payload into that seam; the external peer never receives an
output directory or a local Analyst/OBS dispatch handle.
"""

from __future__ import annotations

from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    MutableMapping,
    Sequence,
)
from typing import Any, NamedTuple, cast

from ...config.settings import SensitiveConfig
from ...interop.a2a_mapping import ExternalA2AEvent
from ...interop.cache import DiscoveryCache
from ...interop.capabilities import DiscoveryResult
from ...interop.planner import InteropMode
from ...interop.registry import InteropRegistry
from ..research.interop import (
    MAX_RESEARCH_EVIDENCE_BYTES,
    ResearchA2APending,
    ResearchInteropDependencies,
    collect_research_a2a,
    collect_research_evidence,
)
from ..shared.interop import (
    InteropA2APending,
    InteropA2AResult,
    InteropEvidence,
)

DESIGN_MCP_CAPABILITY = "design"
DESIGN_A2A_CAPABILITY = "design"
MAX_DESIGN_EVIDENCE_BYTES = MAX_RESEARCH_EVIDENCE_BYTES


DesignEvidence = InteropEvidence
DesignA2AResult = InteropA2AResult


class DesignA2APending(InteropA2APending):
    """Safe state carried from Design A2A pause to its resume node."""

    analysis_type: str
    species_code: str
    gene_id: str
    is_polling: bool


DiscoverDesign = Callable[..., Awaitable[DiscoveryResult]]
InvokeDesign = Callable[..., Awaitable[Any]]
StreamDesignA2A = Callable[..., AsyncIterator[ExternalA2AEvent]]


class DesignInteropDependencies(NamedTuple):
    """Injectable registry, cache, and transport seams for Design."""

    registry: InteropRegistry | None = None
    sensitive_config: SensitiveConfig | None = None
    caches: MutableMapping[str, DiscoveryCache] | None = None
    discover: DiscoverDesign | None = None
    invoke: InvokeDesign | None = None
    capability_name: str = DESIGN_MCP_CAPABILITY
    discover_a2a: DiscoverDesign | None = None
    stream_a2a: StreamDesignA2A | None = None
    a2a_capability_name: str = DESIGN_A2A_CAPABILITY


def _task_for_external(task: Mapping[str, Any]) -> dict[str, Any]:
    """Project Design state into the transport-neutral planning payload."""
    analysis_type = str(
        task.get("analysis_type", task.get("task_name", "design"))
    )
    species_code = str(task.get("species_code", ""))
    gene_id = str(task.get("gene_id", ""))
    context = task.get("context")
    if context is None:
        context = (
            f"species_code={species_code}; gene_id={gene_id}; "
            f"analysis_type={analysis_type}"
        )
    return {
        "goal_description": str(task.get("goal_description", "")),
        "context": str(context),
        "data_list": dict(task.get("data_list") or {}),
        "task_name": analysis_type,
    }


def _research_dependencies(
    dependencies: DesignInteropDependencies | None,
) -> ResearchInteropDependencies:
    """Adapt Design's injected seams to the shared transport adapter."""
    resolved = dependencies or DesignInteropDependencies()
    return ResearchInteropDependencies(
        registry=resolved.registry,
        sensitive_config=resolved.sensitive_config,
        caches=resolved.caches,
        discover=resolved.discover,
        invoke=resolved.invoke,
        capability_name=resolved.capability_name,
        discover_a2a=resolved.discover_a2a,
        stream_a2a=resolved.stream_a2a,
        a2a_capability_name=resolved.a2a_capability_name,
    )


def _map_pending(
    pending: DesignA2APending | None,
) -> ResearchA2APending | None:
    """Convert Design pause state into the shared A2A resume DTO."""
    if pending is None:
        return None
    return ResearchA2APending(
        task_name=pending["analysis_type"],
        goal_description=pending["goal_description"],
        context=pending["context"],
        data_list=dict(pending["data_list"]),
        output_dir=pending["output_dir"],
        thread_id=pending["thread_id"],
        target_id=pending["target_id"],
        capability=pending["capability"],
        task_id=pending["task_id"],
        context_id=pending.get("context_id"),
        draft=pending["draft"],
    )


def _map_evidence(result: Mapping[str, Any]) -> DesignEvidence:
    """Drop protocol correlation fields before local prompt projection."""
    return DesignEvidence(
        target_id=str(result["target_id"]),
        kind=str(result["kind"]),
        capability=str(result["capability"]),
        content=str(result["content"]),
        truncated=bool(result["truncated"]),
    )


def format_design_evidence(evidence: DesignEvidence) -> str:
    """Mark external Design output as untrusted before local planning."""
    kind = "A2A" if evidence.get("kind") == "a2a" else "MCP"
    return (
        f"[UNTRUSTED EXTERNAL {kind} DESIGN EVIDENCE]\n"
        f"target_id={evidence['target_id']}\n"
        f"capability={evidence['capability']}\n"
        f"{evidence['content']}\n"
        f"[/UNTRUSTED EXTERNAL {kind} DESIGN EVIDENCE]"
    )


async def collect_design_evidence(
    task: Mapping[str, Any],
    *,
    mode: InteropMode,
    target_ids: Sequence[str],
    dependencies: DesignInteropDependencies | None = None,
) -> DesignEvidence | None:
    """Collect optional MCP planning evidence without local side effects."""
    result = await collect_research_evidence(
        _task_for_external(task),
        mode=mode,
        target_ids=target_ids,
        dependencies=_research_dependencies(dependencies),
    )
    return None if result is None else _map_evidence(result)


async def collect_design_a2a(
    task: Mapping[str, Any],
    *,
    mode: InteropMode,
    target_ids: Sequence[str],
    dependencies: DesignInteropDependencies | None = None,
    **options: Any,
) -> DesignA2AResult | None:
    """Collect optional A2A planning evidence and preserve correlations."""
    resume = cast(Mapping[str, Any] | None, options.get("resume"))
    pending = cast(DesignA2APending | None, options.get("pending"))
    result = await collect_research_a2a(
        _task_for_external(task),
        mode=mode,
        target_ids=target_ids,
        dependencies=_research_dependencies(dependencies),
        resume=resume,
        pending=_map_pending(pending),
    )
    if result is None:
        return None
    return DesignA2AResult(
        target_id=result["target_id"],
        kind="a2a",
        capability=result["capability"],
        status=result["status"],
        task_id=result.get("task_id"),
        context_id=result.get("context_id"),
        content=result["content"],
        truncated=result["truncated"],
    )


DESIGN_INTEROP_FAILURES: tuple[type[Exception], ...] = (
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)
