# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Module-level design submission entry points.

The functions stay separate from the graph implementation so the design
agent module remains focused on state construction and node execution. Their
runtime imports intentionally read dependencies from ``design.agent`` at call
time; this preserves the established producer-test injection points.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, NamedTuple

from ..shared.remote_analysis import (
    RemoteAnalysisPrompt,
    RemoteAnalysisRequest,
)


class _DesignAnalysisSpec(NamedTuple):
    """Static spec bundle for one design analysis wrapper."""

    analysis_type: str
    goal_path: str
    meta_path: str
    compute_resource: Literal["small", "medium", "large"]


@dataclass(frozen=True)
class DesignEntrypointDependencies:
    """Runtime dependencies supplied by the design agent module."""

    config: Any
    get_sensitive_config: Callable[[], Any]
    get_prompt: Callable[..., str]
    get_data_list: Callable[..., dict[str, str]]
    resolve_data_list_key: Callable[[str], str]
    analyst_factory: Callable[..., Any]
    submit_remote_analysis: Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class DesignAnalysisRequest:
    """Inputs for one module-level design submission."""

    species_code: str
    gene_id: str
    spec: _DesignAnalysisSpec
    output_dir: str | None
    is_polling: bool


async def submit_design_analysis(
    request: DesignAnalysisRequest,
    dependencies: DesignEntrypointDependencies,
) -> dict[str, Any]:
    """Build prompt parts and dispatch one analyst submission."""
    sensitive = dependencies.get_sensitive_config()
    goal_description = dependencies.get_prompt(
        dependencies.config.PROMPT_FILE,
        request.spec.goal_path,
        {"gene_id": request.gene_id},
    )
    meta = dependencies.get_prompt(
        dependencies.config.PROMPT_FILE,
        request.spec.meta_path,
    )
    data_list = dependencies.get_data_list(
        dependencies.config.DEEPGENOME_DATA,
        dependencies.resolve_data_list_key(request.spec.analysis_type),
        request.species_code,
    )
    remote_request = RemoteAnalysisRequest(
        analysis_type=request.spec.analysis_type,
        target_id=request.gene_id,
        output_dir=request.output_dir,
        prompt=RemoteAnalysisPrompt(
            goal_description=goal_description,
            meta=meta,
            data_list=data_list,
        ),
        compute_resource=request.spec.compute_resource,
    )
    return await dependencies.submit_remote_analysis(
        dependencies.analyst_factory(
            analyst_config=dependencies.config,
            sensitive_config=sensitive,
        ),
        dependencies.config,
        sensitive,
        remote_request,
        is_polling=request.is_polling,
    )
