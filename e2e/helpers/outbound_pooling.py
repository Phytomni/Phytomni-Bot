# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Safety gates for live outbound-pooling acceptance."""

from __future__ import annotations

from collections.abc import Mapping

LIVE_GATE_NAMES: tuple[str, ...] = (
    "PHYTOMNI_RUN_INTEGRATION",
    "PHYTOMNI_ALLOW_NETWORK",
    "PHYTOMNI_RUN_OUTBOUND_POOL_E2E",
    "PHYTOMNI_CONFIRM_NON_PRODUCTION",
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


__all__ = [
    "LIVE_GATE_NAMES",
    "MISSING_LIVE_GATE_REASON",
    "MissingOutboundLiveGateError",
    "require_live_gates",
]
