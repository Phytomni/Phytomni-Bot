# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Select operator-approved external capabilities from discovered metadata.

The planner never loads a registry, performs discovery, opens a transport, or
executes a peer tool; callers decide separately when discovery is allowed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .capabilities import InteropCapability

InteropMode = Literal["off", "auto", "required"]
_VALID_MODES = frozenset(("off", "auto", "required"))


class InteropPlanningError(ValueError):
    """Raised when a required interop request has no eligible capability."""

    def __init__(self, code: str, message: str) -> None:
        """Store a stable code and a caller-safe diagnostic message."""
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class InteropTaskSpec:
    """Describe one local task that may be matched to a peer tool.

    Attributes:
        name: Exact remote capability name requested by the caller.  The
            value is matched against ``InteropCapability.remote_name``.
        input_schema: Local task input schema.  Its property names are used
            to verify that every required peer-tool field can be supplied.
    """

    name: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class InteropPlan:
    """Deterministic planner output without executable peer handles.

    Attributes:
        mode: Requested local/automatic/required policy.
        candidates: Eligible metadata-only capabilities in stable order.
        use_local_fallback: Whether the caller should continue locally.
        reason: Stable reason for an empty candidate set, when applicable.
    """

    mode: InteropMode
    candidates: tuple[InteropCapability, ...] = ()
    use_local_fallback: bool = False
    reason: str | None = None


def _schema_properties(schema: Mapping[str, Any]) -> frozenset[str]:
    """Return property names from a JSON-schema-like mapping."""
    properties = schema.get("properties", schema)
    if not isinstance(properties, Mapping):
        return frozenset()
    return frozenset(str(key) for key in properties)


def _required_fields(schema: Mapping[str, Any]) -> frozenset[str] | None:
    """Return valid required fields, or ``None`` for a malformed schema."""
    required = schema.get("required", ())
    if required is None:
        return frozenset()
    if isinstance(required, str) or not isinstance(required, Sequence):
        return None
    if any(not isinstance(item, str) for item in required):
        return None
    return frozenset(required)


def _schema_compatible(
    task_schema: Mapping[str, Any], peer_schema: Mapping[str, Any]
) -> bool:
    """Check that the local task can provide every peer required field."""
    task_properties = _schema_properties(task_schema)
    peer_required = _required_fields(peer_schema)
    if peer_required is None:
        return False
    return peer_required.issubset(task_properties)


def _eligible_candidates(
    task: InteropTaskSpec,
    target_ids: Sequence[str],
    capabilities: Iterable[InteropCapability],
) -> tuple[InteropCapability, ...]:
    """Filter and order capabilities without consulting external state."""
    target_order = {
        target_id: index for index, target_id in enumerate(target_ids)
    }
    if not target_order:
        return ()
    selected: dict[str, InteropCapability] = {}
    for capability in capabilities:
        if capability.target_id not in target_order:
            continue
        if capability.remote_name != task.name:
            continue
        if not _schema_compatible(task.input_schema, capability.input_schema):
            continue
        selected.setdefault(capability.qualified_name, capability)
    return tuple(
        sorted(
            selected.values(),
            key=lambda item: (
                target_order[item.target_id],
                item.qualified_name,
            ),
        )
    )


def plan_interop_capabilities(
    task: InteropTaskSpec,
    *,
    mode: InteropMode,
    target_ids: Sequence[str],
    capabilities: Iterable[InteropCapability],
) -> InteropPlan:
    """Select eligible peer capabilities under an explicit request policy.

    ``capabilities`` must be supplied by a higher-level discovery coordinator;
    this function never discovers or executes anything.  An empty target list
    is a closed allowlist and therefore yields no candidates.  ``auto`` asks
    the caller to continue locally when selection is empty, while ``required``
    raises a stable error instead of allowing a local pseudo-success.

    Args:
        task: Requested remote capability name and local input schema.
        mode: ``off``, ``auto``, or ``required``.
        target_ids: Caller-selected operator target ids.
        capabilities: Already-discovered metadata-only capabilities.

    Returns:
        A deterministic, executable-handle-free plan.

    Raises:
        InteropPlanningError: If ``mode`` is invalid or required selection
            produces no eligible candidate.
    """
    if mode not in _VALID_MODES:
        raise InteropPlanningError("invalid_mode", "invalid interop mode")
    if mode == "off":
        return InteropPlan(
            mode=mode,
            use_local_fallback=True,
            reason="disabled",
        )

    candidates = _eligible_candidates(task, target_ids, capabilities)
    if candidates:
        return InteropPlan(mode=mode, candidates=candidates)
    if mode == "auto":
        return InteropPlan(
            mode=mode,
            use_local_fallback=True,
            reason="no_eligible_capability",
        )
    raise InteropPlanningError(
        "no_eligible_capability",
        "required interop request has no eligible capability",
    )


__all__ = [
    "InteropMode",
    "InteropPlan",
    "InteropPlanningError",
    "InteropTaskSpec",
    "plan_interop_capabilities",
]
