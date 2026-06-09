# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Resolve free-form HTTP user queries into DigitalDesign gene ids.

Public: DigitalDesignResolveError, DigitalDesignIdCandidate,
DigitalDesignResolveResult, resolve_design_user_query.

Thin wrapper around BGA's resolver — shares the gene_id namespace,
prompt, and ~90 d phyto_chat cache. Per-domain error class so the
HTTP layer can disambiguate 400 responses by agent.
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel

from ...config.defaults import DigitalDesignConfig
from ...config.settings import SensitiveConfig
from ..brief_gene.resolve_query import BriefGeneIdCandidate
from ..shared.bga_delegation import resolve_via_bga

__all__ = [
    "DigitalDesignIdCandidate",
    "DigitalDesignResolveError",
    "DigitalDesignResolveResult",
    "resolve_design_user_query",
]


class DigitalDesignResolveError(ValueError):
    """Raised when the LLM cannot resolve a usable gene id for design.

    Per-domain ValueError subclass so the HTTP API layer maps it to a
    400 with the domain name in the error and so callers can ``except``
    on the design type alone without catching every other agent's
    resolver failure.
    """


class DigitalDesignIdCandidate(BaseModel):
    """One LLM-proposed candidate gene id for digital design.

    Per-candidate ``species_code`` mirrors BGA's projection: optional
    at the candidate level so blanks fall through to the top-level
    species code chosen for the resolution.
    """

    gene_id: str
    confidence: float = 0.0
    species_code: str = ""


class DigitalDesignResolveResult(BaseModel):
    """Resolver output: chosen gene id, species, raw query, candidates.

    ``species_code`` is the three-letter code BGA extracted alongside
    ``gene_id``; the HTTP injection site reads it to populate the
    DigitalDesignAgent ``species_code`` argument without a second LLM
    call.
    """

    gene_id: str
    species_code: str
    raw_query: str
    candidates: List[DigitalDesignIdCandidate]


async def resolve_design_user_query(
    raw_query: str,
    *,
    design_config: DigitalDesignConfig,
    sensitive_config: SensitiveConfig,
    timeout_seconds: float = 90.0,
) -> DigitalDesignResolveResult:
    """Resolve free-form text into the canonical gene id design expects.

    Delegates to ``resolve_brief_gene_user_query`` because both agents
    operate over the same canonical gene_id namespace. Wraps the
    result and any error so callers see design-typed objects.

    Args:
        raw_query: Free-form user query (English or Chinese).
        design_config: DigitalDesign non-secret config; projected into
            a BriefGeneConfig view internally so the BGA resolver's
            prompt + json schema + chat dispatch reuse on identical
            kwargs.
        sensitive_config: Shared sensitive config; supplies the chat
            api key and base url.
        timeout_seconds: Resolver wall-clock budget.

    Returns:
        Typed DigitalDesignResolveResult with chosen gene_id + original
        raw_query + full candidate list sorted by confidence desc.

    Raises:
        DigitalDesignResolveError: blank input, retry-exhausted LLM,
            empty / unparseable LLM payload, or wall-clock timeout.
            Other unexpected exceptions propagate so the outer FastAPI
            handler renders them as 500.
    """
    bga_result = await resolve_via_bga(
        raw_query,
        design_config,
        sensitive_config,
        timeout_seconds,
        error_cls=DigitalDesignResolveError,
    )
    return DigitalDesignResolveResult(
        gene_id=bga_result.gene_id,
        species_code=bga_result.species_code,
        raw_query=bga_result.raw_query,
        candidates=[
            _to_design_candidate(candidate)
            for candidate in bga_result.candidates
        ],
    )


def _to_design_candidate(
    candidate: BriefGeneIdCandidate,
) -> DigitalDesignIdCandidate:
    """Project a BGA candidate into the design-typed view."""
    return DigitalDesignIdCandidate(
        gene_id=candidate.gene_id,
        confidence=candidate.confidence,
        species_code=candidate.species_code,
    )
