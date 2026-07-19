# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Resolve free-form HTTP user queries into BriefGene gene ids.

Public: BriefGeneResolveError, BriefGeneIdCandidate,
BriefGeneResolveResult, resolve_brief_gene_user_query.

One structured LLM call (json_schema) through the shared phyto_chat
chokepoint reuses its ~90d cache. BI validation stays with
BriefGene's query_judge_node downstream of the resolved id.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from ...common.prompts import get_prompt
from ...common.responses import message_content, parse_json_object_fragment
from ...config.defaults import BriefGeneConfig
from ...config.settings import SensitiveConfig
from ..chat.service import phyto_chat
from ..shared.options import build_chat_kwargs
from ..shared.query_resolution import (
    ResolverFailure,
    build_candidate_item_schema,
    invoke_resolver,
    normalize_confidence,
    normalize_species_code,
)
from ..shared.species_catalog import warn_if_unsupported_species

__all__ = [
    "BriefGeneIdCandidate",
    "BriefGeneResolveError",
    "BriefGeneResolveResult",
    "resolve_brief_gene_user_query",
]

_LOGGER = logging.getLogger(__name__)

_RESOLVER_SYSTEM_PROMPT_PATH = "system/brief_gene_resolve_gene_id"
_RESOLVER_USER_PROMPT_PATH = "user/brief_gene_resolve_gene_id"


class BriefGeneResolveError(ValueError):
    """Raised when the LLM cannot resolve a usable gene id.

    Callers in the HTTP API layer map this to a 400 response so
    clients that explicitly opted into ``resolve_gene_id=true`` get
    a definitive error instead of a silent fallback.
    """


class BriefGeneIdCandidate(BaseModel):
    """One LLM-proposed candidate gene id with optional confidence.

    Per-candidate ``species_code`` is optional: when the LLM omits it
    the candidate inherits the top-level species code chosen for the
    resolution.
    """

    gene_id: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    species_code: str = ""


class BriefGeneResolveResult(BaseModel):
    """Resolver output: chosen gene id, species, original query, candidates.

    ``species_code`` is the matching three-letter code from the supported
    catalog (e.g. ``osa``, ``ath``, ``zma``) that callers like the
    DeepGenome and Design HTTP runs paths require alongside ``gene_id``.
    """

    gene_id: str
    species_code: str
    raw_query: str
    candidates: list[BriefGeneIdCandidate]


def _candidate_item_schema() -> dict[str, Any]:
    """Derive the per-candidate JSON schema from the pydantic model.

    Strips pydantic's ``title`` keys so the shape matches what the
    self-hosted OpenAI-compatible endpoint expects (bare type +
    numeric bounds), keeping a single source of truth for the
    confidence constraint.
    """
    model = BriefGeneIdCandidate.model_json_schema()
    props = model["properties"]
    return build_candidate_item_schema(
        "gene_id",
        props["confidence"]["minimum"],
        props["confidence"]["maximum"],
    )


_RESOLVER_JSON_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "BriefGeneResolution",
        "schema": {
            "type": "object",
            "properties": {
                "gene_id": {"type": "string"},
                "species_code": {"type": "string"},
                "candidates": {
                    "type": "array",
                    "items": _candidate_item_schema(),
                },
            },
            "required": ["gene_id", "species_code"],
        },
    },
}


async def resolve_brief_gene_user_query(
    raw_query: str,
    *,
    brief_config: BriefGeneConfig,
    sensitive_config: SensitiveConfig,
    timeout_seconds: float | None = None,
) -> BriefGeneResolveResult:
    """Resolve free-form text into the canonical gene id BriefGene expects.

    Args:
        raw_query: Free-form user query (English or Chinese).
        brief_config: BriefGene non-secret config; supplies the chat
            model id, prompt file path, and sampling params.
        sensitive_config: Shared sensitive config; supplies the chat
            api key and base url.
        timeout_seconds: Resolver wall-clock budget. Defaults to
            ``brief_config.TIMEOUT`` (``ServerConfig.TIMEOUT``). An
            inflight LLM call exceeding this raises
            BriefGeneResolveError.

    Returns:
        Typed result with the chosen gene_id, the matching species_code
        (three-letter code from the supported catalog), the original
        raw_query, and the full candidate list sorted by confidence
        descending.

    Raises:
        BriefGeneResolveError: blank input, retry-exhausted LLM,
            empty / unparseable LLM payload, blank or missing
            species_code, or wall-clock timeout. Other unexpected
            exceptions propagate so the outer FastAPI handler renders
            them as 500.
    """
    if not raw_query or not raw_query.strip():
        raise BriefGeneResolveError("query is blank")

    if timeout_seconds is None:
        timeout_seconds = brief_config.TIMEOUT

    rendered_user_query = get_prompt(
        brief_config.PROMPT_FILE,
        _RESOLVER_USER_PROMPT_PATH,
        {"user_query": raw_query},
    )

    chat_kwargs = build_chat_kwargs(
        {"prompt_path": _RESOLVER_SYSTEM_PROMPT_PATH},
        brief_config,
        sensitive_config,
    )
    chat_kwargs["response_format"] = _RESOLVER_JSON_SCHEMA

    try:
        phyto_response = await invoke_resolver(
            lambda: phyto_chat(
                user_query=rendered_user_query,
                **chat_kwargs,
            ),
            timeout_seconds,
        )
    except ResolverFailure as exc:
        if str(exc).startswith("resolver timeout after"):
            raise BriefGeneResolveError(
                f"resolver timeout after {timeout_seconds:.1f} s"
            ) from exc
        if str(exc) == "resolver returned no result":
            raise BriefGeneResolveError(
                "LLM returned no content after retries"
            ) from exc
        raise BriefGeneResolveError(str(exc)) from exc

    content = message_content(phyto_response)
    if not content:
        raise BriefGeneResolveError("LLM returned empty content")

    payload = parse_json_object_fragment(content)
    if not payload:
        raise BriefGeneResolveError("non-parseable LLM output")

    species_raw = payload.get("species_code")
    species_code = species_raw.strip() if isinstance(species_raw, str) else ""
    if not species_code:
        raise BriefGeneResolveError(
            "species_code could not be determined from query: "
            f"'{raw_query}'"
        )

    warn_if_unsupported_species(_LOGGER, species_code, raw_query)

    candidates = _normalize_candidates(
        payload.get("gene_id"),
        payload.get("candidates"),
        species_code,
    )
    if not candidates:
        raise BriefGeneResolveError("no valid candidate")

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return BriefGeneResolveResult(
        gene_id=candidates[0].gene_id,
        species_code=species_code,
        raw_query=raw_query,
        candidates=candidates,
    )


def _normalize_candidates(
    top_gene_id: Any,
    candidates_field: Any,
    top_species_code: str,
) -> list[BriefGeneIdCandidate]:
    """Build the candidate list, dropping blanks and clamping confidence.

    Prefer an explicit ``candidates`` list when present; fall back to
    the top-level ``gene_id`` only when no usable candidate survives
    the cleaning pass. Per-candidate ``species_code`` defaults to
    ``top_species_code`` when absent or blank so downstream consumers
    always see a populated three-letter code.
    """
    out: list[BriefGeneIdCandidate] = []
    if isinstance(candidates_field, list):
        for raw in candidates_field:
            if not isinstance(raw, dict):
                continue
            gene_id = str(raw.get("gene_id", "")).strip()
            if not gene_id:
                continue
            confidence_raw = raw.get("confidence", 0.0)
            try:
                confidence = normalize_confidence(confidence_raw)
            except ResolverFailure:
                confidence = 0.0
            species_code = normalize_species_code(
                raw.get("species_code"), top_species_code
            )
            out.append(
                BriefGeneIdCandidate(
                    gene_id=gene_id,
                    confidence=confidence,
                    species_code=species_code,
                )
            )
    if not out and isinstance(top_gene_id, str):
        cleaned = top_gene_id.strip()
        if cleaned:
            out.append(
                BriefGeneIdCandidate(
                    gene_id=cleaned,
                    confidence=1.0,
                    species_code=top_species_code,
                )
            )
    return out
