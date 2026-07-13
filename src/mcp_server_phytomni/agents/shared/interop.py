# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared policy helpers for agent-level interoperability adapters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, NamedTuple, TypedDict, cast

from ...config.settings import SensitiveConfig
from ...interop.planner import InteropMode
from ...interop.registry import InteropRegistryError, load_interop_registry


class InteropEvidence(TypedDict):
    """Bounded, sanitized evidence shared by agent interop adapters."""

    target_id: str
    kind: str
    capability: str
    content: str
    truncated: bool


class InteropA2AResult(TypedDict):
    """Bounded A2A result shared by agent interop adapters."""

    target_id: str
    kind: Literal["a2a"]
    capability: str
    status: Literal["completed", "input_required", "failed"]
    task_id: str | None
    context_id: str | None
    content: str
    truncated: bool


class InteropA2APending(TypedDict):
    """Common resumable correlation fields for A2A agent adapters."""

    goal_description: str
    context: str
    data_list: dict[str, str]
    output_dir: str
    thread_id: str
    target_id: str
    capability: str
    task_id: str
    context_id: str | None
    draft: str


class InteropRecord(TypedDict):
    """Safe client-summary record for one external delegation attempt."""

    target_id: str
    kind: Literal["mcp", "a2a"]
    capability: str
    status: Literal["completed", "input_required", "degraded", "failed"]
    latency_ms: float


class InteropAttempt(NamedTuple):
    """Inputs needed to summarize one external route attempt."""

    dependencies: Any
    target_ids: Sequence[str]
    mode: InteropMode
    mcp_capability: str
    a2a_capability: str
    status: Literal["completed", "input_required", "degraded", "failed"]
    latency_seconds: float
    degraded: bool = False


def make_interop_record(
    *,
    target_id: str,
    kind: Literal["mcp", "a2a"],
    capability: str,
    status: Literal["completed", "input_required", "degraded", "failed"],
    latency_seconds: float,
) -> InteropRecord:
    """Create a bounded, credential-free delegation summary."""
    return {
        "target_id": target_id,
        "kind": kind,
        "capability": capability,
        "status": status,
        "latency_ms": round(max(0.0, latency_seconds) * 1000, 3),
    }


def interop_record_from_evidence(
    evidence: Mapping[str, Any],
    *,
    status: Literal["completed", "input_required", "degraded", "failed"],
    latency_seconds: float,
) -> InteropRecord:
    """Create a summary record from a bounded evidence DTO."""
    kind: Literal["mcp", "a2a"] = (
        "a2a" if str(evidence.get("kind")) == "a2a" else "mcp"
    )
    return make_interop_record(
        target_id=str(evidence.get("target_id", "unknown")),
        kind=kind,
        capability=str(evidence.get("capability", "unknown")),
        status=status,
        latency_seconds=latency_seconds,
    )


def interop_state_update(
    record: InteropRecord,
    *,
    degraded: bool = False,
) -> dict[str, Any]:
    """Return a reducer-safe graph update for one interop attempt."""
    return {"interop": [record], "degraded_interop": degraded}


def interop_attempt_update(attempt: InteropAttempt) -> dict[str, Any]:
    """Build a reducer update for an attempted external route."""
    if attempt.mode == "off":
        return {}
    kind, target_id, capability = interop_attempt_descriptor(
        attempt.dependencies,
        attempt.target_ids,
        mcp_capability=attempt.mcp_capability,
        a2a_capability=attempt.a2a_capability,
    )
    return interop_state_update(
        make_interop_record(
            target_id=target_id,
            kind=kind,
            capability=capability,
            status=attempt.status,
            latency_seconds=attempt.latency_seconds,
        ),
        degraded=attempt.degraded,
    )


def interop_evidence_update(
    evidence: Mapping[str, Any],
    *,
    status: Literal["completed", "input_required", "degraded", "failed"],
    latency_seconds: float,
    degraded: bool = False,
) -> dict[str, Any]:
    """Build a reducer update from a bounded evidence DTO."""
    return interop_state_update(
        interop_record_from_evidence(
            evidence,
            status=status,
            latency_seconds=latency_seconds,
        ),
        degraded=degraded,
    )


def interop_target_descriptor(
    dependencies: Any,
    target_ids: Sequence[str],
    *,
    kind: Literal["mcp", "a2a"],
    capability: str,
) -> tuple[str, str]:
    """Return safe target/capability labels for fallback or failure records."""
    registry = getattr(dependencies, "registry", None)
    if registry is not None:
        for target_id in target_ids:
            try:
                target = registry.require_target(target_id)
            except InteropRegistryError:
                continue
            if target.kind == kind:
                return target.id, capability
    return (str(target_ids[0]) if target_ids else "unknown", capability)


def interop_attempt_descriptor(
    dependencies: Any,
    target_ids: Sequence[str],
    *,
    mcp_capability: str,
    a2a_capability: str,
) -> tuple[Literal["mcp", "a2a"], str, str]:
    """Return ``(kind, target_id, capability)`` for an attempted route."""
    kind: Literal["mcp", "a2a"] = (
        "a2a"
        if has_interop_target_kind(dependencies, target_ids, "a2a")
        else "mcp"
    )
    capability = a2a_capability if kind == "a2a" else mcp_capability
    target_id, capability = interop_target_descriptor(
        dependencies,
        target_ids,
        kind=kind,
        capability=capability,
    )
    return kind, target_id, capability


def resolve_interop_dependencies[T](
    dependencies: T,
    *,
    mode: InteropMode,
    sensitive_config: SensitiveConfig,
) -> T | None:
    """Fill common lazy interop dependencies for an enabled request."""
    if mode == "off":
        return None
    resolved: Any = dependencies
    if resolved.registry is None:
        resolved = resolved._replace(
            registry=load_interop_registry(sensitive_config=sensitive_config)
        )
    resolved = resolved._replace(sensitive_config=sensitive_config)
    if resolved.caches is None:
        resolved = resolved._replace(caches={})
    return cast(T, resolved)


def has_interop_target_kind(
    dependencies: Any,
    target_ids: Sequence[str],
    kind: str,
) -> bool:
    """Return whether an enabled registry contains a named target kind."""
    registry = getattr(dependencies, "registry", None)
    if registry is None or not registry.enabled:
        return False
    for target_id in target_ids:
        try:
            if registry.require_target(target_id).kind == kind:
                return True
        except InteropRegistryError:
            continue
    return False


def project_a2a_evidence(result: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only safe evidence fields, dropping protocol correlations."""
    return {
        "target_id": str(result["target_id"]),
        "kind": str(result["kind"]),
        "capability": str(result["capability"]),
        "content": str(result["content"]),
        "truncated": bool(result["truncated"]),
    }


def build_a2a_resume_draft(
    pending: Mapping[str, Any],
    *,
    kind: str,
    label_key: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the protocol-neutral input-required projection for a resume."""
    draft: dict[str, Any] = {
        "kind": kind,
        "status": "input_required",
        label_key: pending[label_key],
        "target_id": pending["target_id"],
        "capability": pending["capability"],
        "task_id": pending["task_id"],
        "context_id": pending.get("context_id"),
        "message": pending["draft"],
    }
    if extra:
        draft.update(extra)
    return draft


def require_a2a_result[T](result: T | None, label: str) -> T:
    """Reject an impossible local fallback while resuming required A2A."""
    if result is None:
        raise RuntimeError(
            f"external A2A {label} resume unexpectedly fell back"
        )
    return result


def resolve_required_interop_dependencies[T](
    dependencies: T, sensitive_config: SensitiveConfig
) -> T | None:
    """Resolve dependencies for a required resume exchange."""
    return resolve_interop_dependencies(
        dependencies,
        mode="required",
        sensitive_config=sensitive_config,
    )


def update_a2a_pending(
    pending: Mapping[str, Any],
    *,
    content: str,
    task_id: str | None,
    context_id: str | None,
) -> dict[str, Any]:
    """Carry the latest bounded draft and correlation ids across pauses."""
    return {
        **pending,
        "draft": content,
        "task_id": task_id or pending["task_id"],
        "context_id": context_id or pending.get("context_id"),
    }


def update_a2a_pending_from_result(
    pending: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply the common result-to-pending correlation update."""
    return update_a2a_pending(
        pending,
        content=str(result["content"]),
        task_id=result.get("task_id"),
        context_id=result.get("context_id"),
    )


def merge_a2a_pending_fields(
    fields: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Add safe result correlations to a domain-specific pending mapping."""
    return {
        **fields,
        "target_id": str(result["target_id"]),
        "capability": str(result["capability"]),
        "task_id": str(result["task_id"]),
        "context_id": result.get("context_id"),
        "draft": str(result["content"]),
    }


def initial_interop_state(options: Mapping[str, Any]) -> dict[str, Any]:
    """Return shared graph-state fields for an interop-enabled run."""
    return {
        "interop_mode": options.get("interop_mode", "off"),
        "interop_targets": list(options.get("interop_targets", [])),
        "a2a_pending": [],
        "a2a_task_ids": {},
        "interop": [],
        "degraded_interop": False,
    }


__all__ = [
    "build_a2a_resume_draft",
    "has_interop_target_kind",
    "initial_interop_state",
    "InteropA2APending",
    "InteropA2AResult",
    "InteropAttempt",
    "InteropEvidence",
    "interop_attempt_descriptor",
    "interop_attempt_update",
    "interop_evidence_update",
    "InteropRecord",
    "interop_record_from_evidence",
    "interop_target_descriptor",
    "interop_state_update",
    "make_interop_record",
    "merge_a2a_pending_fields",
    "project_a2a_evidence",
    "require_a2a_result",
    "resolve_interop_dependencies",
    "resolve_required_interop_dependencies",
    "update_a2a_pending",
    "update_a2a_pending_from_result",
]
