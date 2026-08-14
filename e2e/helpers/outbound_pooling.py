# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Safety gates for live outbound-pooling acceptance."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

LIVE_GATE_NAMES: tuple[str, ...] = (
    "PHYTOMNI_RUN_INTEGRATION",
    "PHYTOMNI_ALLOW_NETWORK",
    "PHYTOMNI_RUN_OUTBOUND_POOL_E2E",
    "PHYTOMNI_CONFIRM_NON_PRODUCTION",
)
LIVE_EXECUTABLE_SCENARIOS: tuple[str, ...] = (
    "llm_stream_and_completion",
    "retrieval_and_rerank",
    "nl2sql",
)
LIVE_EXTERNAL_PENDING_SCENARIOS: Mapping[str, str] = MappingProxyType(
    {
        "analysis_lifecycle": (
            "no confirmed disposable analysis provider with delete authority"
        ),
        "relay_paths": (
            "active child/operator role is not confirmed non-production"
        ),
        "obs_lifecycle": (
            "no public delete route or confirmed disposable cleanup authority"
        ),
        "interop_targets": "no confirmed non-production MCP or A2A peer",
        "cancellation": "no public pool-state observation endpoint by design",
        "resource_reuse": "no public resource-identity endpoint by design",
        "bounded_capacity": (
            "no public pool-state observation endpoint by design"
        ),
        "clean_shutdown": (
            "resource close counters are process-private by design"
        ),
    }
)
LIVE_ACCEPTANCE_SCENARIOS: tuple[str, ...] = (
    *LIVE_EXECUTABLE_SCENARIOS,
    *LIVE_EXTERNAL_PENDING_SCENARIOS,
)
MISSING_LIVE_GATE_REASON = (
    "outbound pooling live acceptance requires all four non-production "
    "gates: " + ", ".join(LIVE_GATE_NAMES)
)
_LIVE_POOL_SUFFIXES = (
    "LLM",
    "RETRIEVAL",
    "RERANK",
    "NL2SQL",
    "ANALYSIS_CONTROL",
    "ANALYSIS_STATUS",
    "IAM",
    "SPA_FAQ",
    "BI",
    "OBS",
    "RELAY_CONTROL",
    "INTEROP",
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


def live_server_environment() -> dict[str, str]:
    """Return deliberately small capacities for a real non-production run.

    The four-gate live suite must exercise the configured provider paths, so
    this mapping intentionally never enables ``PHYTOMNI_TESTING``.
    """
    environment = {
        f"PHYTOMNI_OUTBOUND_{suffix}_CONCURRENCY": "1"
        for suffix in _LIVE_POOL_SUFFIXES
    }
    environment["PHYTOMNI_OUTBOUND_POOL_WAIT_WARN_SECONDS"] = "0.5"
    return environment


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
    "LIVE_EXECUTABLE_SCENARIOS",
    "LIVE_EXTERNAL_PENDING_SCENARIOS",
    "LIVE_GATE_NAMES",
    "MISSING_LIVE_GATE_REASON",
    "MissingOutboundLiveGateError",
    "live_server_environment",
    "require_live_gates",
    "require_live_scenario",
]
