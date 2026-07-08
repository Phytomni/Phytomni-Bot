# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: lihu (lihu0628@qq.com)
#         maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Brief gene function summaries from BI annotations and literature RAG.

Thin orchestration wrapper: re-exports BriefGeneAgent and state from
core.py, defines the public brief_gene_function entry point, and
maintains backward-compat pipeline symbol re-exports for existing
importers.
"""

from typing import Any

from ...config.overrides import (
    CHAT_COMPLETION_CONFIG_FIELD_MAP,
    RETRIEVAL_CONFIG_FIELD_MAP,
    RETRY_CONFIG_FIELD_MAP,
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from ...config.settings import get_sensitive_config
from ...mcp.schemas import BriefGeneAgent as BriefGeneAgentSchema
from ...runtime.agent_registry import (
    agent_fingerprint_values,
    get_cached_agent,
)
from ..knowledge.agent import KnowledgeAgent
from .core import (
    BRIEF_CONFIG,
    BriefGeneAgent,
    BriefGeneAgentState,
    initial_brief_gene_state,
)
from .pipeline import (
    GeneRetrieveRequest,
    _attach_metadata,
    _dedupe,
    _first_row,
    _format_docs,
    _generate_follow_up,
    _go_annotation_string,
    _interpro_annotation_string,
    _mapman_annotation_string,
    _response_data,
    _safe_rows,
    _split_symbols,
    clear_gene_retrieve_cache,
    gene_retrieve,
    run_bi_api,
)

__all__ = [
    "BRIEF_CONFIG",
    "BRIEF_GENE_CONFIG_FIELD_MAP",
    "BRIEF_GENE_SECRET_FIELD_MAP",
    "BRIEF_GENE_SENSITIVE_FIELD_MAP",
    "BriefGeneAgent",
    "BriefGeneAgentState",
    "GeneRetrieveRequest",
    "_attach_metadata",
    "_dedupe",
    "_first_row",
    "_format_docs",
    "_generate_follow_up",
    "_go_annotation_string",
    "_interpro_annotation_string",
    "_mapman_annotation_string",
    "_response_data",
    "_safe_rows",
    "_split_symbols",
    "brief_gene_function",
    "brief_gene_stream_seed",
    "clear_gene_retrieve_cache",
    "gene_retrieve",
    "run_bi_api",
]

BRIEF_GENE_CONFIG_FIELD_MAP = {
    **CHAT_COMPLETION_CONFIG_FIELD_MAP,
    **RETRIEVAL_CONFIG_FIELD_MAP,
    **RETRY_CONFIG_FIELD_MAP,
    "max_concurrency": "MAX_CONCURRENCY",
}
BRIEF_GENE_SENSITIVE_FIELD_MAP = {
    "base_url": "BASE_URL",
    "model": "MODEL_ID",
}
BRIEF_GENE_SECRET_FIELD_MAP = {
    "api_key": "API_KEY",
}


async def brief_gene_function(
    user_query: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run the LangGraph brief gene function workflow.

    Args:
        user_query: Gene identifier, symbol, or free-text query.
        **kwargs: Optional chat, retrieval, BI, retry, credential, and
            cache-fingerprint overrides.

    Returns:
        Chat-completions-style final response payload from
        BriefGeneAgent.
    """
    brief_config = copy_config_with_overrides(
        BRIEF_CONFIG,
        kwargs,
        BRIEF_GENE_CONFIG_FIELD_MAP,
    )
    sensitive_config = copy_sensitive_config_with_overrides(
        get_sensitive_config(),
        kwargs,
        field_map=BRIEF_GENE_SENSITIVE_FIELD_MAP,
        secret_field_map=BRIEF_GENE_SECRET_FIELD_MAP,
    )
    agent = get_cached_agent(
        "BriefGeneAgent",
        lambda: BriefGeneAgent(
            brief_config=brief_config,
            sensitive_config=sensitive_config,
            knowledge_agent=KnowledgeAgent(
                knowledge_config=brief_config,
                sensitive_config=sensitive_config,
            ),
        ),
        agent_fingerprint_values(
            brief_config=brief_config,
            sensitive_config=sensitive_config,
        ),
    )
    return await agent.arun(user_query=user_query)


def brief_gene_stream_seed(
    args: BriefGeneAgentSchema,
) -> tuple[Any, BriefGeneAgentState]:
    """Return the cached BriefGeneAgent app + seeded state for progress.

    Acquires the SAME cached agent ``brief_gene_function`` uses with
    DEFAULT config and seeds the 40-key initial state through the
    shared :func:`initial_brief_gene_state` helper so the seed and
    ``arun`` stay byte-identical. There is no SSE ``stream_target``
    for BriefGeneAgent because it carries no public chat-completions
    model alias; this seed feeds only the MCP stdio progress driver.

    Args:
        args: The validated MCP request schema carrying ``user_query``.

    Returns:
        Tuple of the compiled graph app and its initial state dict.
    """
    brief_config = copy_config_with_overrides(
        BRIEF_CONFIG, {}, BRIEF_GENE_CONFIG_FIELD_MAP
    )
    sensitive_config = copy_sensitive_config_with_overrides(
        get_sensitive_config(),
        {},
        field_map=BRIEF_GENE_SENSITIVE_FIELD_MAP,
        secret_field_map=BRIEF_GENE_SECRET_FIELD_MAP,
    )
    agent = get_cached_agent(
        "BriefGeneAgent",
        lambda: BriefGeneAgent(
            brief_config=brief_config,
            sensitive_config=sensitive_config,
            knowledge_agent=KnowledgeAgent(
                knowledge_config=brief_config,
                sensitive_config=sensitive_config,
            ),
        ),
        agent_fingerprint_values(
            brief_config=brief_config,
            sensitive_config=sensitive_config,
        ),
    )
    return agent.app, initial_brief_gene_state(args.user_query)
