# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Resolve free-form HTTP user queries into GeneNetwork TO ids.

Public: GeneNetworkResolveError, GeneNetworkToIdCandidate,
GeneNetworkResolveResult, resolve_network_user_query. Injects the
``config/to_ontology.json`` catalog into the LLM user message and
validates the returned id against it; fabricated ids drop inside
``_normalize_candidates`` rather than reaching the agent.
"""

from __future__ import annotations

import asyncio
import json
import logging
from functools import lru_cache
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ...common.prompts import get_prompt
from ...config.defaults import GeneNetworkConfig
from ...config.settings import SensitiveConfig
from ..chat.service import phyto_chat
from ..shared.options import build_chat_kwargs
from .to_ontology import (
    DEPRECATED_UPSTREAM_STATUS,
    format_to_ontology_for_prompt,
    load_to_ontology,
)

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "GeneNetworkResolveError",
    "GeneNetworkResolveResult",
    "GeneNetworkToIdCandidate",
    "resolve_network_user_query",
]

_RESOLVER_SYSTEM_PROMPT_PATH = "system/gene_network_resolve_to_id"
_RESOLVER_USER_PROMPT_PATH = "user/gene_network_resolve_to_id"
_RESOLVER_JSON_SCHEMA: Dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "GeneNetworkResolution",
        "schema": {
            "type": "object",
            "properties": {
                "to_id": {"type": "string"},
                "species_code": {"type": "string"},
                "candidates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "to_id": {"type": "string"},
                            "species_code": {"type": "string"},
                            "confidence": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                        },
                        "required": ["to_id"],
                    },
                },
            },
            "required": ["to_id", "species_code"],
        },
    },
}


class GeneNetworkResolveError(ValueError):
    """Raised when the LLM cannot resolve a usable TO id.

    Callers in the HTTP API layer map this to a 400 response so
    clients that explicitly opted into ``resolve_to_id=true`` get a
    definitive error instead of a silent fallback.
    """


class GeneNetworkToIdCandidate(BaseModel):
    """One LLM-proposed candidate TO id with optional confidence.

    Per-candidate ``species_code`` is optional: when the LLM omits it
    the candidate inherits the top-level species code chosen for the
    resolution.
    """

    to_id: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    species_code: str = ""


class GeneNetworkResolveResult(BaseModel):
    """Resolver output: chosen TO id, species, query, all candidates.

    ``species_code`` is the matching three-letter code from the
    supported catalog (e.g. ``osa``, ``ath``, ``zma``) that callers
    like the GeneNetwork HTTP runs path require alongside ``to_id``.
    """

    to_id: str
    species_code: str
    raw_query: str
    candidates: List[GeneNetworkToIdCandidate]


async def resolve_network_user_query(
    raw_query: str,
    *,
    network_config: GeneNetworkConfig,
    sensitive_config: SensitiveConfig,
    timeout_seconds: float = 90.0,
) -> GeneNetworkResolveResult:
    """Resolve free-form text into the canonical TO id network expects.

    Args:
        raw_query: Free-form user trait query (English or Chinese).
        network_config: GeneNetwork non-secret config; supplies the
            chat model id, prompt file path, and sampling params.
        sensitive_config: Shared sensitive config; supplies the chat
            api key and base url.
        timeout_seconds: Resolver wall-clock budget.

    Returns:
        Typed GeneNetworkResolveResult with chosen to_id + matching
        species_code + original raw_query + full candidate list sorted
        by confidence desc.

    Raises:
        GeneNetworkResolveError: blank input, retry-exhausted LLM,
            empty / unparseable LLM payload, blank or missing
            species_code, wall-clock timeout, or LLM-proposed id not
            present in the committed TO catalog.
    """
    if not raw_query or not raw_query.strip():
        raise GeneNetworkResolveError("query is blank")

    catalog_entries = load_to_ontology()
    catalog_text = format_to_ontology_for_prompt(catalog_entries)
    valid_to_ids = {entry.id for entry in catalog_entries}

    rendered_user_query = get_prompt(
        network_config.PROMPT_FILE,
        _RESOLVER_USER_PROMPT_PATH,
        {"user_query": raw_query},
    )
    # Prepend the catalog so the LLM's user-side context carries the
    # closed-set candidates the system prompt instructs it to choose
    # from. System prompts are loaded without param substitution by
    # phyto_chat's _system_message helper, so the catalog has to ride
    # on the user message.
    rendered_user_query = f"Catalog:\n{catalog_text}\n\n{rendered_user_query}"

    chat_kwargs = build_chat_kwargs(
        {"prompt_path": _RESOLVER_SYSTEM_PROMPT_PATH},
        network_config,
        sensitive_config,
    )
    chat_kwargs["response_format"] = _RESOLVER_JSON_SCHEMA

    try:
        phyto_response = await asyncio.wait_for(
            phyto_chat(user_query=rendered_user_query, **chat_kwargs),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise GeneNetworkResolveError(
            f"resolver timeout after {timeout_seconds:.1f} s"
        ) from exc

    if phyto_response is None:
        raise GeneNetworkResolveError("LLM returned no content after retries")

    content = _first_message_content(phyto_response)
    if not content:
        raise GeneNetworkResolveError("LLM returned empty content")

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise GeneNetworkResolveError(
            f"non-parseable LLM output: {exc.msg}"
        ) from exc

    if not isinstance(payload, dict):
        raise GeneNetworkResolveError("LLM output is not a JSON object")

    species_code = _extract_species_code(payload.get("species_code"))
    if not species_code:
        raise GeneNetworkResolveError(
            "species_code could not be determined from query: "
            f"'{raw_query}'"
        )

    candidates = _normalize_candidates(
        payload.get("to_id"),
        payload.get("candidates"),
        valid_to_ids,
        species_code,
    )
    if not candidates:
        raise GeneNetworkResolveError(
            "no valid candidate (LLM proposed ids not in the catalog)"
        )

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    _warn_if_deprecated(candidates[0].to_id, raw_query)
    return GeneNetworkResolveResult(
        to_id=candidates[0].to_id,
        species_code=species_code,
        raw_query=raw_query,
        candidates=candidates,
    )


@lru_cache(maxsize=1)
def _deprecated_to_ids() -> frozenset:
    """Return the frozen set of upstream-deprecated TO ids (process-cached).

    Built once from ``load_to_ontology`` so the resolver's pick-time
    check is O(1) instead of linear over the 573-entry catalog. The
    cache key is the loader singleton itself, which is also cached.
    """
    return frozenset(
        entry.id
        for entry in load_to_ontology()
        if entry.status == DEPRECATED_UPSTREAM_STATUS
    )


def _warn_if_deprecated(chosen_to_id: str, raw_query: str) -> None:
    """Emit a WARNING log when the resolver picks an upstream-deprecated id.

    The catalog still accepts these ids so customer workflows do not
    break, but server logs surface the drift so a future audit can
    decide whether to migrate the trait to a canonical id. The id-set
    lookup is O(1) through ``_deprecated_to_ids`` so the check stays
    cheap even if the catalog grows.
    """
    if chosen_to_id not in _deprecated_to_ids():
        return
    _LOGGER.warning(
        "GeneNetwork resolver picked upstream-deprecated TO id "
        "%s for query %r; upstream PTO marks it obsoleted (or "
        "missing) without a replaced_by hint",
        chosen_to_id,
        raw_query,
    )


def _extract_species_code(raw: Any) -> str:
    """Normalize an LLM-provided species_code field to a stripped string."""
    return raw.strip() if isinstance(raw, str) else ""


def _first_message_content(
    phyto_response: Dict[str, Any],
) -> Optional[str]:
    """Pull the first assistant message content out of an OpenAI dict."""
    choices = phyto_response.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    return None


def _normalize_candidates(
    top_to_id: Any,
    candidates_field: Any,
    valid_to_ids: set,
    top_species_code: str,
) -> List[GeneNetworkToIdCandidate]:
    """Build candidate list, dropping blanks and ids outside the catalog.

    Validation against ``valid_to_ids`` is the resolver's last line
    of defense against LLM hallucination: the system prompt instructs
    the LLM to pick from the supplied catalog, but a non-compliant
    completion still loses its bogus ids here rather than reaching
    the downstream BI / agent layer that has no equivalent check.

    Per-candidate ``species_code`` defaults to ``top_species_code``
    when absent or blank so downstream consumers always see a
    populated three-letter code.
    """
    out: List[GeneNetworkToIdCandidate] = []
    if isinstance(candidates_field, list):
        for raw in candidates_field:
            if not isinstance(raw, dict):
                continue
            to_id = str(raw.get("to_id", "")).strip()
            if not to_id or to_id not in valid_to_ids:
                continue
            confidence_raw = raw.get("confidence", 0.0)
            try:
                confidence = float(confidence_raw)
            except (TypeError, ValueError):
                confidence = 0.0
            confidence = max(0.0, min(1.0, confidence))
            candidate_species_raw = raw.get("species_code")
            candidate_species = (
                candidate_species_raw.strip()
                if isinstance(candidate_species_raw, str)
                else ""
            )
            species_code = candidate_species or top_species_code
            out.append(
                GeneNetworkToIdCandidate(
                    to_id=to_id,
                    confidence=confidence,
                    species_code=species_code,
                )
            )
    if not out and isinstance(top_to_id, str):
        cleaned = top_to_id.strip()
        if cleaned and cleaned in valid_to_ids:
            out.append(
                GeneNetworkToIdCandidate(
                    to_id=cleaned,
                    confidence=1.0,
                    species_code=top_species_code,
                )
            )
    return out
