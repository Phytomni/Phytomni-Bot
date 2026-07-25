# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Typed state and IO contracts for the evolution LangGraph.

``EvolutionInput`` / ``EvolutionOutput`` / ``EvolutionState`` split
parent-input, parent-output, and internal-state shapes. The module
omits ``from __future__ import annotations`` because
``TypedDict.__required_keys__`` is computed at class-definition
time; lazy annotations would collapse ``Required[]`` markers into
``total=False`` keys.
"""

from typing import Any, Required, TypedDict

from ...runtime.locale import SupportedLocale


class EvolutionInput(TypedDict, total=False):
    """Public input contract for the evolution subgraph.

    Mirrors the kwargs ``evo_test_analysis`` accepts: a required
    natural-language query, the source species code, the target
    gene id, a batch flag controlling output-directory reuse, the
    analyst auto-select toggle, and a type-erased ``kwargs``
    service bag that carries chat / submit / OBS overrides flat
    through to the underlying helpers.

    Attributes:
        query: Natural-language evolution analysis request.
        species_code: Three-letter species code used to select
            prepared data (e.g., "osa", "ath").
        gene_id: Target gene identifier for the analysis prompt.
        batch: When ``True`` the caller-provided ``output_dir`` in
            ``kwargs`` is reused instead of materialising a fresh
            run-scoped directory.
        enable_auto_select: When ``True`` the AnalystAgent may
            auto-select tools at submission time.
        is_polling: When ``True`` the analyst submission blocks until the
            task reaches a terminal state (deep_genome's mount sets this);
            defaults to ``False`` for the submit-only external surface.
        target_taxids: Pre-resolved taxonomy scope. When set (truthy) the
            chat extraction is skipped and this value passes straight to
            the submit node; deep_genome's mount pins ``"All"``.
        kwargs: Flat dict of chat / submit / OBS overrides forwarded
            to the chat extraction and analyst submission helpers.
    """

    query: Required[str]
    species_code: Required[str]
    gene_id: Required[str]
    batch: bool
    enable_auto_select: bool
    is_polling: bool
    locale: SupportedLocale
    target_taxids: str
    kwargs: dict[str, Any]


class EvolutionOutput(TypedDict):
    """Public output contract for the evolution subgraph.

    ``evolution_agents_task`` is the submitted analyst task payload
    (``None`` when taxonomy extraction failed). The key itself is
    always present in the final state so consumers can do a single
    membership check.
    """

    evolution_agents_task: dict[str, Any] | None


class EvolutionState(TypedDict, total=False):
    """Internal state spanning every evolution workflow node.

    Carries every :class:`EvolutionInput` field plus the
    intermediate ``target_taxids`` string ``resolve_taxids_node``
    materialises (comma-joined taxonomy ids or ``"All"`` sentinel
    or ``None`` on extraction failure) and the final
    ``evolution_agents_task`` matching :class:`EvolutionOutput`.
    """

    query: Required[str]
    species_code: Required[str]
    gene_id: Required[str]
    batch: bool
    enable_auto_select: bool
    is_polling: bool
    locale: SupportedLocale
    kwargs: dict[str, Any]
    target_taxids: str | None
    evolution_agents_task: dict[str, Any] | None
