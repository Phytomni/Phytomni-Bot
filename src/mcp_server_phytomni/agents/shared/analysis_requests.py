# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Typed construction for the common Analyst dispatch request shape.

Environment and Evolution choose their prompts, targets, data, and resource
tiers independently. Once those domain values are resolved, both hand them to
the Analyst subgraph through the same five-field payload. This module owns only
that immutable value and its adapter-facing projection; it does not select
domains, submit tasks, poll remote work, or read configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "AnalystAnalysisRequest",
    "build_analyst_prompt_parts",
    "build_analyst_analysis_request",
]


@dataclass(frozen=True, slots=True)
class AnalystAnalysisRequest:
    """Immutable values shared by Analyst-backed domain dispatchers.

    Attributes:
        analysis_type: Domain-owned Analyst analysis discriminator.
        target_id: Domain-owned target identifier used for dispatch identity.
        output_dir: Optional caller-selected output directory.
        prompt_parts: Adapter-facing tuple of goal, metadata, and data.
        compute_resource: Domain-selected Analyst resource tier.
    """

    analysis_type: str
    target_id: str
    output_dir: str | None
    prompt_parts: tuple[str, str, Any]
    compute_resource: str

    def to_payload(self) -> dict[str, Any]:
        """Project the value into the existing subgraph payload shape.

        The adapter still owns validation and submission. Returning a fresh
        dictionary keeps the immutable request safe from downstream mutation
        while preserving the established ``prompt_parts`` tuple contract.
        """
        return {
            "analysis_type": self.analysis_type,
            "target_id": self.target_id,
            "output_dir": self.output_dir,
            "prompt_parts": self.prompt_parts,
            "compute_resource": self.compute_resource,
        }


def build_analyst_prompt_parts(
    goal_description: str,
    meta: str,
    data_list: Any,
) -> tuple[str, str, Any]:
    """Build the existing goal/meta/data tuple without domain branching."""
    return goal_description, meta, data_list


def build_analyst_analysis_request(
    analysis_type: str,
    target_id: str,
    output_dir: str | None,
    prompt_parts: tuple[str, str, Any],
    compute_resource: str,
) -> AnalystAnalysisRequest:
    """Build one domain-neutral Analyst dispatch request.

    Prompt rendering, data lookup, target selection, resource selection, and
    output-directory policy remain with the caller. This pure builder only
    records their resolved values in the shared typed shape.
    """
    return AnalystAnalysisRequest(
        analysis_type=analysis_type,
        target_id=target_id,
        output_dir=output_dir,
        prompt_parts=prompt_parts,
        compute_resource=compute_resource,
    )
