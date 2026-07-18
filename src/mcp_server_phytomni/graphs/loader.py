# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Declarative graph manifest loader (Pydantic + allowlist).

``load_graph_manifest(path)`` reads a JSON manifest, validates it
against the ``GraphManifest`` shape, and rejects any subgraph node
whose id falls outside ``default_subgraph_allowlist()``. Gated
behind ``ServerConfig.GRAPH_LOADER_ENABLED`` (default ``False``):
the function raises ``GraphLoaderDisabledError`` when the flag is
off so the loader surface stays inert outside opt-in callers.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from ..config.defaults import ServerConfig
from .allowlist import default_subgraph_allowlist
from .manifest import GraphManifest


class GraphLoaderDisabledError(RuntimeError):
    """Raised when manifest loading is requested with the flag off."""


class GraphLoaderValidationError(ValueError):
    """Raised when a manifest fails schema or allowlist validation."""


def load_graph_manifest(
    manifest_path: Path | str,
    *,
    allowlist: frozenset[str] | None = None,
    config: ServerConfig | None = None,
) -> GraphManifest:
    """Return a validated ``GraphManifest`` for one on-disk manifest.

    Args:
        manifest_path: Path to a JSON manifest matching the
            ``graph_manifest.schema.json`` schema.
        allowlist: Optional override frozen set of subgraph ids.
            Defaults to ``default_subgraph_allowlist()``.
        config: Optional ``ServerConfig`` instance for flag check;
            tests pass a config with ``GRAPH_LOADER_ENABLED=True``
            rather than the default-off production resolution.

    Returns:
        The validated ``GraphManifest`` instance.

    Raises:
        FileNotFoundError: When ``manifest_path`` does not exist.
        GraphLoaderDisabledError: When the resolved config has
            ``GRAPH_LOADER_ENABLED=False``.
        GraphLoaderValidationError: When the JSON fails Pydantic
            validation or references a subgraph id outside ``allowlist``.
    """
    resolved_config = config if config is not None else ServerConfig()
    if not resolved_config.GRAPH_LOADER_ENABLED:
        raise GraphLoaderDisabledError(
            "GraphLoader is disabled; set "
            "PHYTOMNI_GRAPH_LOADER=true to enable."
        )
    resolved_allowlist = (
        allowlist if allowlist is not None else default_subgraph_allowlist()
    )
    path = Path(manifest_path)
    payload = path.read_text(encoding="utf-8")
    try:
        manifest = GraphManifest.model_validate_json(payload)
    except ValidationError as exc:
        raise GraphLoaderValidationError(
            f"{path}: manifest failed schema validation: {exc}"
        ) from exc
    for sub_id in manifest.subgraph_node_names:
        if sub_id not in resolved_allowlist:
            raise GraphLoaderValidationError(
                f"{path}: subgraph node {sub_id!r} is not in the "
                f"loader allowlist"
            )
    return manifest


__all__ = [
    "GraphLoaderDisabledError",
    "GraphLoaderValidationError",
    "load_graph_manifest",
]
