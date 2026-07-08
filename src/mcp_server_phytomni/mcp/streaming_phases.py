# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Whitelist mapping graph node names to semantic streaming phases.

Only reduce / post nodes appear so the fan-out worker and dispatch
nodes are folded away; the seam further dedups repeats. Keeping the
table here (not in the agent graphs) decouples the public StepStarted
contract from internal graph topology so a node rename never breaks the
wire contract.
"""

from __future__ import annotations

_PHASE_MAP: dict[str, dict[str, str]] = {
    "KnowledgeAgent": {
        "retrieve_node": "retrieving",
        "generate_post_node": "generating",
    },
    "ReviewAgent": {
        "plan_query_post_node": "planning",
        "retrieve_reduce_node": "retrieving",
        "draft_reduce_node": "drafting",
        "revised_reduce_node": "revising",
        "summary_post_node": "generating",
    },
    "DataAgent": {
        "retrieve_post_node": "retrieving",
        "rewrite_post_node": "rewriting",
        "search_node": "querying",
    },
    "BriefGeneAgent": {
        "fetch_annotation_node": "annotating",
        "retrieve_reduce_node": "retrieving",
        "section_discovery_node": "analyzing",
        "section_cloning_node": "analyzing",
        "section_functional_node": "analyzing",
        "section_application_node": "analyzing",
        "render_node": "generating",
    },
}


def phase_for(agent: str, node: str) -> str | None:
    """Return the semantic phase for one node, or None to drop it."""
    return _PHASE_MAP.get(agent, {}).get(node)
