# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""HTTP-only resolver dispatch shared by the API request surfaces.

The resolver flags are transport concerns: they pre-shape a free-form
``user_query`` before the shared MCP invocation seam sees the agent arguments.
Keeping the dispatch here prevents the FastAPI application factory from also
owning domain-specific resolver policy while preserving the established HTTP
400 messages and metadata keys.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from ..agents.brief_gene.resolve_query import (
    BriefGeneResolveError,
    BriefGeneResolveResult,
    resolve_brief_gene_user_query,
)
from ..agents.deep_genome.resolve_query import (
    DeepGenomeResolveError,
    DeepGenomeResolveResult,
    resolve_deep_genome_user_query,
)
from ..agents.design.resolve_query import (
    DigitalDesignResolveError,
    DigitalDesignResolveResult,
    resolve_design_user_query,
)
from ..agents.network.resolve_query import (
    GeneNetworkResolveError,
    GeneNetworkResolveResult,
    resolve_network_user_query,
)
from ..config.defaults import (
    BriefGeneConfig,
    DeepGenomeConfig,
    DigitalDesignConfig,
    GeneNetworkConfig,
)
from ..config.settings import SensitiveConfig
from .openai_mapping import tool_accepts_resolve_gene_id

__all__ = [
    "ResolverDispatch",
    "apply_runs_resolver",
    "resolve_chat_query",
]

type BriefGeneResolver = Callable[..., Awaitable[BriefGeneResolveResult]]


async def _maybe_resolve_brief_gene_query(
    *,
    raw_query: str,
    resolve_flag: bool,
    tool_name: str | None,
    agent_slug: str | None,
    resolver: BriefGeneResolver | None,
) -> tuple[str, dict[str, Any]]:
    """Resolve BriefGene text for either HTTP request surface."""
    if not resolve_flag:
        return raw_query, {}
    brief_tool = (
        tool_name == "BriefGeneAgent"
        and tool_accepts_resolve_gene_id(tool_name)
    )
    brief_slug = agent_slug == "brief_gene"
    if not (brief_tool or brief_slug):
        raise HTTPException(
            status_code=400,
            detail="resolve_gene_id is only valid for BriefGene calls",
        )
    resolve = resolver or resolve_brief_gene_user_query
    try:
        result = await resolve(
            raw_query,
            brief_config=BriefGeneConfig(),
            sensitive_config=SensitiveConfig.load(),
        )
    except BriefGeneResolveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.gene_id, {
        "original_query": raw_query,
        "resolved_gene_id": result.gene_id,
        "resolved_species_code": result.species_code,
        "resolve_gene_id": True,
    }


type DeepGenomeResolver = Callable[..., Awaitable[DeepGenomeResolveResult]]
type DigitalDesignResolver = Callable[
    ..., Awaitable[DigitalDesignResolveResult]
]
type GeneNetworkResolver = Callable[..., Awaitable[GeneNetworkResolveResult]]


@dataclass(frozen=True, slots=True)
class ResolverDispatch:
    """Optional resolver callables injected by API compatibility seams."""

    brief_gene_resolver: BriefGeneResolver | None = None
    deep_genome_resolver: DeepGenomeResolver | None = None
    design_resolver: DigitalDesignResolver | None = None
    network_resolver: GeneNetworkResolver | None = None


async def resolve_chat_query(
    *,
    raw_query: str,
    resolve_flag: bool,
    tool_name: str | None,
    brief_gene_resolver: BriefGeneResolver | None = None,
) -> tuple[str, dict[str, Any]]:
    """Resolve a chat query when the BriefGene opt-in flag is enabled.

    The chat-completions route is the only HTTP surface that gates this flag
    by MCP tool name.  Native agent runs use :func:`apply_runs_resolver`
    instead, so both surfaces share the same resolver but retain their
    transport-specific validation boundary.
    """
    return await _maybe_resolve_brief_gene_query(
        raw_query=raw_query,
        resolve_flag=resolve_flag,
        tool_name=tool_name,
        agent_slug=None,
        resolver=brief_gene_resolver,
    )


async def _maybe_resolve_deep_genome_query(
    *,
    raw_query: str,
    agent_slug: str | None,
    resolver: DeepGenomeResolver | None,
) -> tuple[DeepGenomeResolveResult | None, dict[str, Any]]:
    """Resolve a DeepGenome query and return its metadata patch."""
    if agent_slug != "deep_genome":
        raise HTTPException(
            status_code=400,
            detail="resolve_gene_id is only valid for DeepGenome calls",
        )
    resolve = resolver or resolve_deep_genome_user_query
    try:
        result = await resolve(
            raw_query,
            deep_genome_config=DeepGenomeConfig(),
            sensitive_config=SensitiveConfig.load(),
        )
    except DeepGenomeResolveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result, {
        "original_query": raw_query,
        "resolved_gene_id": result.gene_id,
        "resolved_species_code": result.species_code,
        "resolve_gene_id": True,
    }


async def _maybe_resolve_design_query(
    *,
    raw_query: str,
    agent_slug: str | None,
    resolver: DigitalDesignResolver | None,
) -> tuple[DigitalDesignResolveResult | None, dict[str, Any]]:
    """Resolve a DigitalDesign query and return its metadata patch."""
    if agent_slug != "design":
        raise HTTPException(
            status_code=400,
            detail="resolve_gene_id is only valid for DigitalDesign calls",
        )
    resolve = resolver or resolve_design_user_query
    try:
        result = await resolve(
            raw_query,
            design_config=DigitalDesignConfig(),
            sensitive_config=SensitiveConfig.load(),
        )
    except DigitalDesignResolveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result, {
        "original_query": raw_query,
        "resolved_gene_id": result.gene_id,
        "resolved_species_code": result.species_code,
        "resolve_gene_id": True,
    }


async def _maybe_resolve_network_query(
    *,
    raw_query: str,
    agent_slug: str | None,
    resolver: GeneNetworkResolver | None,
) -> tuple[GeneNetworkResolveResult | None, dict[str, Any]]:
    """Resolve a GeneNetwork trait query and return its metadata patch."""
    if agent_slug != "network":
        raise HTTPException(
            status_code=400,
            detail="resolve_to_id is only valid for GeneNetwork calls",
        )
    resolve = resolver or resolve_network_user_query
    try:
        result = await resolve(
            raw_query,
            network_config=GeneNetworkConfig(),
            sensitive_config=SensitiveConfig.load(),
        )
    except GeneNetworkResolveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result, {
        "original_query": raw_query,
        "resolved_to_id": result.to_id,
        "resolved_species_code": result.species_code,
        "resolve_to_id": True,
    }


async def apply_runs_resolver(
    agent: str,
    arguments: dict[str, Any],
    *,
    dispatch: ResolverDispatch | None = None,
) -> dict[str, Any]:
    """Apply the resolver flags for a native agent-run request.

    ``arguments`` is intentionally mutated in place: the transient resolver
    flags and free-form ``user_query`` must be removed before the per-agent
    Pydantic schema validates the final tool arguments.  ``dispatch`` keeps
    the API app's existing test seams injectable without importing
    ``api.app`` into this module.
    """
    configured = dispatch or ResolverDispatch()
    flag_gene_id = bool(arguments.pop("resolve_gene_id", False))
    flag_to_id = bool(arguments.pop("resolve_to_id", False))
    if not (flag_gene_id or flag_to_id):
        return {}
    raw_query = arguments.pop("user_query", None)
    if not isinstance(raw_query, str) or not raw_query.strip():
        flag_label = "resolve_to_id" if flag_to_id else "resolve_gene_id"
        raise HTTPException(
            status_code=400,
            detail=f"user_query is required when {flag_label} is true",
        )
    if flag_to_id and agent != "network":
        raise HTTPException(
            status_code=400,
            detail="resolve_to_id is only valid for GeneNetwork calls",
        )
    if flag_to_id:
        network_result, metadata = await _maybe_resolve_network_query(
            raw_query=raw_query,
            agent_slug=agent,
            resolver=configured.network_resolver,
        )
        assert network_result is not None
        arguments["to_id"] = network_result.to_id
        arguments["species_code"] = network_result.species_code
        return metadata
    if agent == "brief_gene":
        resolved, metadata = await _maybe_resolve_brief_gene_query(
            raw_query=raw_query,
            resolve_flag=True,
            tool_name=None,
            agent_slug=agent,
            resolver=configured.brief_gene_resolver,
        )
        arguments["user_query"] = resolved
        return metadata
    if agent == "deep_genome":
        deep_genome_result, metadata = await _maybe_resolve_deep_genome_query(
            raw_query=raw_query,
            agent_slug=agent,
            resolver=configured.deep_genome_resolver,
        )
        assert deep_genome_result is not None
        arguments["gene_id"] = deep_genome_result.gene_id
        arguments["species_code"] = deep_genome_result.species_code
        return metadata
    if agent == "design":
        design_result, metadata = await _maybe_resolve_design_query(
            raw_query=raw_query,
            agent_slug=agent,
            resolver=configured.design_resolver,
        )
        assert design_result is not None
        arguments["gene_id"] = design_result.gene_id
        arguments["species_code"] = design_result.species_code
        return metadata
    raise HTTPException(
        status_code=400,
        detail=(
            "resolve_gene_id is only valid for BriefGene / DeepGenome / "
            f"DigitalDesign calls (received agent {agent!r})"
        ),
    )
