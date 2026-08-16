# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Declarative graph manifest loader (Pydantic + allowlist).

``load_graph_manifest(path)`` reads a JSON manifest, validates it
against the ``GraphManifest`` shape, and rejects any subgraph node
whose id falls outside ``default_subgraph_allowlist()``.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from .allowlist import default_subgraph_allowlist
from .manifest import GraphManifest


class GraphLoaderValidationError(ValueError):
    """Raised when a manifest fails schema or allowlist validation."""


def load_graph_manifest(
    manifest_path: Path | str,
    *,
    allowlist: frozenset[str] | None = None,
) -> GraphManifest:
    """Return a validated ``GraphManifest`` for one on-disk manifest.

    Args:
        manifest_path: Path to a JSON manifest matching the
            ``graph_manifest.schema.json`` schema.
        allowlist: Optional override frozen set of subgraph ids.
            Defaults to ``default_subgraph_allowlist()``.

    Returns:
        The validated ``GraphManifest`` instance.

    Raises:
        FileNotFoundError: When ``manifest_path`` does not exist.
        GraphLoaderValidationError: When the JSON fails Pydantic
            validation or references a subgraph id outside ``allowlist``.
    """
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
    "GraphLoaderValidationError",
    "load_graph_manifest",
]
