# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Conditional-edge target completeness across registered subgraphs.

``add_conditional_edges(src, fn)`` without ``Literal[...]`` and
without ``path_map`` leaves LangGraph metadata missing the
conditional edge from ``src``: ``draw_mermaid()`` / ``xray=True``
/ manifest export silently drop the routing. Loop the default
registry, compare ``builder.branches`` (declared sources) against
manifest conditional sources, and fail on any gap.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.graphs.defaults import build_default_registry
from mcp_server_phytomni.graphs.manifest import export_manifest

pytestmark = pytest.mark.agent


def test_registered_subgraphs_surface_declared_conditional_sources() -> None:
    """Every ``add_conditional_edges`` source reaches graph metadata.

    Reflective contract: enumerate the default registry, compare
    each compiled subgraph's ``builder.branches`` declared sources
    against the manifest's exported conditional edge sources, and
    fail when any declared source is missing — regression bait for
    a future router added without ``Literal`` return annotation
    AND without explicit ``path_map``.
    """
    registry = build_default_registry()
    spec_ids = registry.names()
    assert spec_ids, "registry is empty; refusing to vacuously pass"

    missing: list[str] = []
    for spec_id in spec_ids:
        app = registry.get_or_compile(spec_id)
        builder = getattr(app, "builder", None)
        declared_sources = set(getattr(builder, "branches", {}).keys())
        if not declared_sources:
            continue
        manifest = export_manifest(app)
        seen_sources = {
            edge.source for edge in manifest.edges if edge.conditional
        }
        for source in declared_sources - seen_sources:
            missing.append(
                f"{spec_id}: source {source!r} declared via "
                f"add_conditional_edges but no conditional edge "
                f"from it in graph metadata; likely needs "
                f"explicit path_map or Literal return annotation"
            )

    assert not missing, "\n".join(missing)
