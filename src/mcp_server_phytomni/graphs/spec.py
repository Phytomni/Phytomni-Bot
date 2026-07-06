# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Spec describing one cacheable subgraph for ``SubgraphRegistry``.

A spec carries the subgraph id, the factory that builds the compiled
LangGraph app, and the optional informational schemas describing its
public input/output contract. The schemas are not enforced by the
registry; they document the contract for tooling such as manifest
exports and future declarative graph loaders.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SubgraphSpec:
    """Declarative description of one cacheable subgraph.

    Attributes:
        id: Stable identifier used as the primary key in
            :class:`mcp_server_phytomni.graphs.registry.SubgraphRegistry`.
        factory: Callable returning a compiled LangGraph application
            (the ``workflow.compile()`` result). Called once per
            distinct ``(id, fingerprint)`` tuple.
        state_schema: Optional class describing the subgraph's full
            internal state. Informational only; the factory must wire
            it in itself.
        input_schema: Optional class describing the subgraph's public
            input fields visible to parent graphs.
        output_schema: Optional class describing the subgraph's public
            output fields visible to parent graphs.
        fingerprint_fields: Optional non-secret config values
            participating in the cache key. When ``None``, the spec
            compiles once and shares one instance across all callers.
            When set, callers may further override per call by passing
            ``fingerprint_values`` to ``get_or_compile``.
    """

    id: str
    factory: Callable[[], Any]
    state_schema: type | None = field(default=None)
    input_schema: type | None = field(default=None)
    output_schema: type | None = field(default=None)
    fingerprint_fields: Mapping[str, Any] | None = field(default=None)
