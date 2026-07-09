# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Resolve free-form HTTP user queries into DeepGenome gene ids.

Public: DeepGenomeResolveError, DeepGenomeIdCandidate,
DeepGenomeResolveResult, resolve_deep_genome_user_query.

Thin wrapper around BGA's resolver — shares the gene_id namespace,
prompt, and ~90 d phyto_chat cache. Per-domain error class so the
HTTP layer can disambiguate 400 responses by agent.
"""

from __future__ import annotations

from pydantic import BaseModel

from ...config.defaults import DeepGenomeConfig
from ...config.settings import SensitiveConfig
from ..brief_gene.resolve_query import BriefGeneIdCandidate
from ..shared.bga_delegation import resolve_via_bga

__all__ = [
    "DeepGenomeIdCandidate",
    "DeepGenomeResolveError",
    "DeepGenomeResolveResult",
    "resolve_deep_genome_user_query",
]


class DeepGenomeResolveError(ValueError):
    """Raised when the LLM cannot resolve a usable gene id for deep_genome.

    Per-domain ValueError subclass so the HTTP API layer maps it to a
    400 with the domain name in the error and so callers can ``except``
    on the deep_genome type alone without catching every other agent's
    resolver failure.
    """


class DeepGenomeIdCandidate(BaseModel):
    """One LLM-proposed candidate gene id for deep_genome.

    Per-candidate ``species_code`` mirrors BGA's projection: optional
    at the candidate level so blanks fall through to the top-level
    species code chosen for the resolution.
    """

    gene_id: str
    confidence: float = 0.0
    species_code: str = ""


class DeepGenomeResolveResult(BaseModel):
    """Resolver output: chosen gene id, species, raw query, candidates.

    ``species_code`` is the three-letter code BGA extracted alongside
    ``gene_id``; the HTTP injection site reads it to populate the
    DeepGenomeAgent ``species_code`` argument without a second LLM call.
    """

    gene_id: str
    species_code: str
    raw_query: str
    candidates: list[DeepGenomeIdCandidate]


async def resolve_deep_genome_user_query(
    raw_query: str,
    *,
    deep_genome_config: DeepGenomeConfig,
    sensitive_config: SensitiveConfig,
    timeout_seconds: float | None = None,
) -> DeepGenomeResolveResult:
    """Resolve free-form text into the canonical gene id deep_genome expects.

    Delegates to ``resolve_brief_gene_user_query`` because both agents
    operate over the same gene_id namespace (the customer's BI catalog
    of canonical gene ids per species); the BGA prompt + JSON schema
    + ~90 d cache hit on the same gene-id resolution there. Wraps the
    result and any error so callers see deep_genome-typed objects.

    Args:
        raw_query: Free-form user query (English or Chinese).
        deep_genome_config: deep_genome non-secret config; supplies the
            chat model id, prompt file path, and sampling params via
            its inherited BriefGene-shaped fields. Built into a
            BriefGeneConfig view internally because the BGA resolver
            owns the prompt + json-schema; deep_genome and BGA share
            ServerConfig / KnowledgeConfig / SensitiveConfig roots so
            the chat dispatch keys come out identical.
        sensitive_config: Shared sensitive config; supplies the chat
            api key and base url.
        timeout_seconds: Resolver wall-clock budget. Defaults to
            ``deep_genome_config.TIMEOUT`` (``ServerConfig.TIMEOUT``).

    Returns:
        Typed DeepGenomeResolveResult with chosen gene_id + original
        raw_query + full candidate list sorted by confidence desc.

    Raises:
        DeepGenomeResolveError: blank input, retry-exhausted LLM,
            empty / unparseable LLM payload, or wall-clock timeout.
            Other unexpected exceptions propagate so the outer FastAPI
            handler renders them as 500.
    """
    if timeout_seconds is None:
        timeout_seconds = deep_genome_config.TIMEOUT
    bga_result = await resolve_via_bga(
        raw_query,
        deep_genome_config,
        sensitive_config,
        timeout_seconds,
        error_cls=DeepGenomeResolveError,
    )
    return DeepGenomeResolveResult(
        gene_id=bga_result.gene_id,
        species_code=bga_result.species_code,
        raw_query=bga_result.raw_query,
        candidates=[
            _to_deep_genome_candidate(candidate)
            for candidate in bga_result.candidates
        ],
    )


def _to_deep_genome_candidate(
    candidate: BriefGeneIdCandidate,
) -> DeepGenomeIdCandidate:
    """Project a BGA candidate into the deep_genome-typed view."""
    return DeepGenomeIdCandidate(
        gene_id=candidate.gene_id,
        confidence=candidate.confidence,
        species_code=candidate.species_code,
    )
