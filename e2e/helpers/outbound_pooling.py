# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Safety gates for live outbound-pooling acceptance."""

from __future__ import annotations

from collections.abc import Callable, Mapping

LIVE_GATE_NAMES: tuple[str, ...] = (
    "PHYTOMNI_RUN_INTEGRATION",
    "PHYTOMNI_ALLOW_NETWORK",
    "PHYTOMNI_RUN_OUTBOUND_POOL_E2E",
    "PHYTOMNI_CONFIRM_NON_PRODUCTION",
)
LIVE_ACCEPTANCE_SCENARIOS: tuple[str, ...] = (
    "llm_stream_and_completion",
    "retrieval_and_rerank",
    "nl2sql",
    "analysis_lifecycle",
    "relay_paths",
    "obs_lifecycle",
    "interop_targets",
    "cancellation",
    "resource_reuse",
    "bounded_capacity",
    "clean_shutdown",
)
MISSING_LIVE_GATE_REASON = (
    "outbound pooling live acceptance requires all four non-production "
    "gates: " + ", ".join(LIVE_GATE_NAMES)
)


class MissingOutboundLiveGateError(Exception):
    """Raised before live acceptance imports secrets or starts services."""


def require_live_gates(environ: Mapping[str, str]) -> None:
    """Require every explicit non-production live-acceptance gate.

    The guard reads only the supplied environment mapping. Callers must run
    it before loading sensitive configuration, starting a subprocess, or
    constructing a network client.

    Args:
        environ: Environment-like mapping containing gate values.

    Raises:
        MissingOutboundLiveGateError: If any gate is not exactly ``"1"``.
    """
    missing = tuple(
        name for name in LIVE_GATE_NAMES if environ.get(name) != "1"
    )
    if missing:
        raise MissingOutboundLiveGateError(
            f"{MISSING_LIVE_GATE_REASON}; missing: {', '.join(missing)}"
        )


def require_live_scenario[ResultT](
    environ: Mapping[str, str],
    scenario: str,
    live_accessor: Callable[[], ResultT],
) -> ResultT:
    """Run one known live setup callback only after all safety gates pass."""
    require_live_gates(environ)
    if scenario not in LIVE_ACCEPTANCE_SCENARIOS:
        raise ValueError(f"unknown outbound live scenario: {scenario}")
    return live_accessor()


__all__ = [
    "LIVE_ACCEPTANCE_SCENARIOS",
    "LIVE_GATE_NAMES",
    "MISSING_LIVE_GATE_REASON",
    "MissingOutboundLiveGateError",
    "require_live_gates",
    "require_live_scenario",
]
