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

import logging
import re
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field

from ...common.prompts import get_prompt
from ...config.defaults import GeneNetworkConfig
from ...config.settings import SensitiveConfig
from ..chat.service import phyto_chat
from ..shared.options import build_resolver_chat_kwargs
from ..shared.query_resolution import (
    ResolverInvocation,
    build_candidate_item_schema,
    invoke_chat_resolver,
    normalize_candidate_fields,
    normalize_species_code,
    parse_resolver_payload,
)
from ..shared.species_catalog import warn_if_unsupported_species
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
    "resolve_network_route_hint",
    "resolve_network_user_query",
]

_RESOLVER_SYSTEM_PROMPT_PATH = "system/gene_network_resolve_to_id"
_RESOLVER_USER_PROMPT_PATH = "user/gene_network_resolve_to_id"
_BARE_TO_ID_PATTERN = re.compile(r"TO:\d{7}")
_BARE_TO_ID_DEFAULT_SPECIES = "osa"
_NETWORK_INTENT_PATTERN = re.compile(
    r"\b(?:gene\s+network|regulatory\s+network|network)\b"
    r"|(?:基因网络|网络分析|调控网络|激素(?:调控)?网络)",
    re.IGNORECASE,
)


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
    candidates: list[GeneNetworkToIdCandidate]


def _candidate_item_schema() -> dict[str, Any]:
    """Derive the per-candidate JSON schema from the pydantic model.

    Strips pydantic's ``title`` keys so the shape matches the
    self-hosted OpenAI-compatible endpoint, keeping one source of truth
    for the confidence constraint.
    """
    model = GeneNetworkToIdCandidate.model_json_schema()
    props = model["properties"]
    return build_candidate_item_schema(
        "to_id",
        props["confidence"],
    )


_RESOLVER_JSON_SCHEMA: dict[str, Any] = {
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
                    "items": _candidate_item_schema(),
                },
            },
            "required": ["to_id", "species_code"],
        },
    },
}


def _resolve_bare_to_id(
    raw_query: str,
    valid_to_ids: set[str],
) -> GeneNetworkResolveResult | None:
    """Resolve one exact catalog TO id to the default rice species."""
    normalized_query = raw_query.strip()
    if not _BARE_TO_ID_PATTERN.fullmatch(normalized_query):
        return None
    if normalized_query not in valid_to_ids:
        raise GeneNetworkResolveError(
            f"TO id not in the catalog: '{normalized_query}'"
        )
    _warn_if_deprecated(normalized_query, raw_query)
    return GeneNetworkResolveResult(
        to_id=normalized_query,
        species_code=_BARE_TO_ID_DEFAULT_SPECIES,
        raw_query=raw_query,
        candidates=[
            GeneNetworkToIdCandidate(
                to_id=normalized_query,
                confidence=1.0,
                species_code=_BARE_TO_ID_DEFAULT_SPECIES,
            )
        ],
    )


def resolve_network_route_hint(
    raw_query: str,
) -> GeneNetworkResolveResult | None:
    """Resolve one unambiguous embedded TO id for Expert fallback routing.

    This deliberately narrow, offline hint belongs to the Network domain:
    it requires one catalog-valid TO id and explicit network/regulatory/trait
    intent. It does not replace the LLM resolver for general free-form trait
    queries and returns ``None`` rather than guessing on invalid or multiple
    ids.
    """
    if not raw_query or not raw_query.strip():
        return None
    matches = _BARE_TO_ID_PATTERN.findall(raw_query)
    if len(matches) != 1 or _NETWORK_INTENT_PATTERN.search(raw_query) is None:
        return None
    to_id = matches[0]
    if to_id not in {entry.id for entry in load_to_ontology()}:
        return None
    _warn_if_deprecated(to_id, raw_query)
    return GeneNetworkResolveResult(
        to_id=to_id,
        species_code=_BARE_TO_ID_DEFAULT_SPECIES,
        raw_query=raw_query,
        candidates=[
            GeneNetworkToIdCandidate(
                to_id=to_id,
                confidence=1.0,
                species_code=_BARE_TO_ID_DEFAULT_SPECIES,
            )
        ],
    )


async def resolve_network_user_query(
    raw_query: str,
    *,
    network_config: GeneNetworkConfig,
    sensitive_config: SensitiveConfig,
    timeout_seconds: float | None = None,
) -> GeneNetworkResolveResult:
    """Resolve free-form text into the canonical TO id network expects.

    Args:
        raw_query: Free-form user trait query (English or Chinese).
        network_config: GeneNetwork non-secret config; supplies the
            chat model id, prompt file path, and sampling params.
        sensitive_config: Shared sensitive config; supplies the chat
            api key and base url.
        timeout_seconds: Resolver wall-clock budget. Defaults to
            ``network_config.TIMEOUT`` (``ServerConfig.TIMEOUT``).

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

    if timeout_seconds is None:
        timeout_seconds = network_config.TIMEOUT

    catalog_entries = load_to_ontology()
    catalog_text = format_to_ontology_for_prompt(catalog_entries)
    valid_to_ids = {entry.id for entry in catalog_entries}
    route_hint = _resolve_bare_to_id(raw_query, valid_to_ids)
    if route_hint is None:
        route_hint = resolve_network_route_hint(raw_query)
    if route_hint is not None:
        return route_hint

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

    chat_kwargs = build_resolver_chat_kwargs(
        _RESOLVER_SYSTEM_PROMPT_PATH,
        _RESOLVER_JSON_SCHEMA,
        network_config,
        sensitive_config,
    )
    invocation = ResolverInvocation(
        chat_kwargs=chat_kwargs,
        timeout_seconds=timeout_seconds,
        error_type=GeneNetworkResolveError,
    )

    phyto_response = await invoke_chat_resolver(
        phyto_chat,
        rendered_user_query,
        invocation,
    )

    payload = parse_resolver_payload(phyto_response, GeneNetworkResolveError)

    species_code = normalize_species_code(payload.get("species_code"), "")
    if not species_code:
        raise GeneNetworkResolveError(
            f"species_code could not be determined from query: '{raw_query}'"
        )

    warn_if_unsupported_species(_LOGGER, species_code, raw_query)

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


def _normalize_candidates(
    top_to_id: Any,
    candidates_field: Any,
    valid_to_ids: set,
    top_species_code: str,
) -> list[GeneNetworkToIdCandidate]:
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
    out: list[GeneNetworkToIdCandidate] = []
    if isinstance(candidates_field, list):
        for raw in candidates_field:
            if not isinstance(raw, dict):
                continue
            to_id = str(raw.get("to_id", "")).strip()
            if not to_id or to_id not in valid_to_ids:
                continue
            confidence, species_code = normalize_candidate_fields(
                raw, top_species_code
            )
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
