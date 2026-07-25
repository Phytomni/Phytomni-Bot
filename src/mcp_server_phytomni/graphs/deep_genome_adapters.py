# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pure mapping helpers from DeepGenome state to subgraph input dicts.

Each helper closes over the literal query-construction logic that
``deep_genome/dispatch.py`` currently performs inline before calling
``knowledge_agent.arun(...)``. Lifting the construction here lets a
parent graph mount the knowledge subgraph as a real LangGraph node
through ``adapter_node`` while the test suite pins the mapping
against the dispatch site's expectations.
"""

from __future__ import annotations

from collections.abc import Mapping

from ..agents.deep_genome.formatting import SPECIES_CODE_MAP
from ..agents.knowledge.state import KnowledgeInput
from ..agents.shared.options import resolve_agent_locale
from ..runtime.locale import SupportedLocale


def map_deepgenome_to_knowledge_input(
    gene_symbol: str,
    species_code: str,
    repo_id_dict: Mapping[str, int],
    locale: SupportedLocale | None = None,
) -> KnowledgeInput:
    """Build a ``KnowledgeInput`` for the DeepGenome literature step.

    Mirrors the ``user_query`` construction inside
    :meth:`agents.deep_genome.dispatch.DeepGenomeDispatch._run_knowledge_agent`:
    the literature query joins the resolved gene symbol with the
    human-readable species name (defaulting to the raw species code
    when no display name is registered in ``SPECIES_CODE_MAP``), and
    the retrieve-only / no-follow-up toggles let the DeepGenome
    workflow stay in pure-retrieval mode through the knowledge
    subgraph.

    Args:
        gene_symbol: Resolved primary gene symbol (caller handles
            the gene-id-fallback already; see ``_gene_symbol`` in
            the dispatch module).
        species_code: Internal species code; used to look up the
            human-readable species name.
        repo_id_dict: Per-repository token budget map the
            DeepGenome config carries on ``REPO_ID_DICT``.

    Returns:
        ``KnowledgeInput`` ready to feed the knowledge subgraph
        through ``adapter_node`` or
        ``parent.add_node("knowledge", child_app)``.
    """
    species_name = SPECIES_CODE_MAP.get(species_code, species_code)
    user_query = f"{gene_symbol}\n{species_name}?"
    return KnowledgeInput(
        user_query=user_query,
        repo_id_dict=dict(repo_id_dict),
        is_generate=False,
        is_follow_up=False,
        locale=resolve_agent_locale(locale),
    )
