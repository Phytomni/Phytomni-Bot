# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""SubgraphRegistry caches compiled subgraphs by (id, fingerprint).

Role split: ``runtime/langgraph_runner.py:GraphRegistry`` caches one
compiled top-level agent graph per agent; this registry caches the
cross-agent reusable building block — one compiled sub-piece per
(subgraph id, narrow fingerprint). Parent graphs load entries via
``parent.add_node("name", child_app)`` or via ``graphs/adapters.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

from ..runtime.langgraph_runner import config_fingerprint
from .spec import SubgraphSpec


class SubgraphRegistry:
    """In-memory cache of compiled subgraphs keyed on (id, fingerprint).

    Attributes:
        _specs: Mapping of subgraph id to its registered spec.
        _cache: Mapping of (id, fingerprint) to its compiled subgraph.
    """

    def __init__(self) -> None:
        """Initialize an empty subgraph registry."""
        self._specs: dict[str, SubgraphSpec] = {}
        self._cache: dict[tuple[str, str], Any] = {}

    def register(self, spec: SubgraphSpec) -> None:
        """Register one subgraph spec.

        Args:
            spec: Subgraph specification to register.

        Raises:
            ValueError: If a spec with the same id is already registered.
        """
        if spec.id in self._specs:
            raise ValueError(f"subgraph already registered: {spec.id}")
        self._specs[spec.id] = spec

    def get_or_compile(
        self,
        sub_id: str,
        fingerprint_values: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        """Return a compiled subgraph, caching by (id, fingerprint).

        Args:
            sub_id: Registered subgraph id.
            fingerprint_values: Optional non-secret config values
                overriding the spec's ``fingerprint_fields`` for this
                call. When ``None``, the spec's own
                ``fingerprint_fields`` are used.

        Returns:
            The compiled subgraph (same instance for the same key).

        Raises:
            KeyError: If ``sub_id`` has not been registered.
        """
        try:
            spec = self._specs[sub_id]
        except KeyError as exc:
            raise KeyError(f"unknown subgraph: {sub_id}") from exc

        values = (
            fingerprint_values
            if fingerprint_values is not None
            else spec.fingerprint_fields
        )
        fingerprint = config_fingerprint(values)
        key = (sub_id, fingerprint)
        if key not in self._cache:
            self._cache[key] = spec.factory()
        return self._cache[key]

    def clear(self, sub_id: Optional[str] = None) -> None:
        """Drop compiled cache, optionally one id only.

        Args:
            sub_id: Optional subgraph id to clear. When ``None``,
                clears every cached compiled subgraph (registered
                specs remain).
        """
        if sub_id is None:
            self._cache.clear()
            return
        for key in list(self._cache):
            if key[0] == sub_id:
                del self._cache[key]

    def names(self) -> tuple[str, ...]:
        """Return registered subgraph ids in deterministic order.

        Returns:
            Tuple of registered ids sorted alphabetically.
        """
        return tuple(sorted(self._specs))

    def get_spec(self, sub_id: str) -> SubgraphSpec:
        """Return the registered spec for one id.

        Args:
            sub_id: Registered subgraph id.

        Returns:
            The :class:`SubgraphSpec` instance registered under
            ``sub_id``.

        Raises:
            KeyError: If ``sub_id`` has not been registered.
        """
        try:
            return self._specs[sub_id]
        except KeyError as exc:
            raise KeyError(f"unknown subgraph: {sub_id}") from exc
