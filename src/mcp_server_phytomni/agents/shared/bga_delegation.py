# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared BGA resolver delegation helper.

DeepGenome and Design wrappers both project their config into a
``BriefGeneConfig`` view, call ``resolve_brief_gene_user_query``, and
re-wrap BGA's error class into their own per-domain ``ValueError``
subclass. Centralising the projection + delegation + error translation
here keeps the per-wrapper code surface-only (one call + final type
projection) and removes the cross-file duplicate-code cluster.
"""

from __future__ import annotations

from typing import Any, Type

from ...config.defaults import BriefGeneConfig
from ...config.settings import SensitiveConfig
from ..brief_gene.resolve_query import (
    BriefGeneResolveError,
    BriefGeneResolveResult,
    resolve_brief_gene_user_query,
)

__all__ = ["resolve_via_bga"]


async def resolve_via_bga(
    raw_query: str,
    source_config: Any,
    sensitive_config: SensitiveConfig,
    timeout_seconds: float,
    *,
    error_cls: Type[ValueError],
) -> BriefGeneResolveResult:
    """Delegate a gene-id resolve to BGA from a sibling agent wrapper.

    Projects ``source_config`` into a ``BriefGeneConfig`` view so the
    BGA resolver's prompt + JSON-schema + cache fingerprint pull from
    identical fields regardless of which agent owns the call; the
    project's shared ``ServerConfig`` / ``KnowledgeConfig`` /
    ``SensitiveConfig`` roots make this projection a no-op rename in
    practice (every BriefGene field exists on the source). Any
    ``BriefGeneResolveError`` re-raised as ``error_cls`` so the HTTP
    API layer maps it to a 400 with the caller's domain name in the
    error and downstream callers can ``except`` on the per-domain
    type alone.

    Args:
        raw_query: Free-form user query (English or Chinese).
        source_config: Caller's non-secret config; must expose every
            ``BriefGeneConfig`` field by name (the central config
            inheritance chain guarantees this for DeepGenome and
            DigitalDesign).
        sensitive_config: Shared sensitive config; supplies the chat
            api key and base url.
        timeout_seconds: Resolver wall-clock budget.
        error_cls: ``ValueError`` subclass to raise in place of the
            BGA-typed ``BriefGeneResolveError``. The HTTP API layer
            keys per-domain 400 mapping off this class.

    Returns:
        Typed ``BriefGeneResolveResult`` with chosen gene_id +
        species_code + raw_query + sorted candidates. Callers project
        it into their per-domain result type.

    Raises:
        error_cls: Translated from any ``BriefGeneResolveError`` the
            BGA resolver raises (blank input, retry-exhausted LLM,
            empty / unparseable LLM payload, blank species_code, or
            wall-clock timeout).
    """
    brief_field_names = list(BriefGeneConfig.model_fields.keys())
    brief_view = BriefGeneConfig(
        **{
            field: getattr(source_config, field)
            for field in brief_field_names
            if hasattr(source_config, field)
        }
    )
    try:
        return await resolve_brief_gene_user_query(
            raw_query,
            brief_config=brief_view,
            sensitive_config=sensitive_config,
            timeout_seconds=timeout_seconds,
        )
    except BriefGeneResolveError as exc:
        raise error_cls(str(exc)) from exc
