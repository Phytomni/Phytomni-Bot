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

from dataclasses import dataclass
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
from ...runtime.conversation_context.models import ContextProjection
from ...runtime.locale import SupportedLocale
from ..knowledge.agent import KnowledgeAgent
from ..shared.options import resolve_agent_locale
from .chat_helpers import invoke_brief_gene_chat
from .conversation import (
    BriefGeneClarificationError,
    BriefGeneConversationAdapter,
    BriefGeneConversationOperation,
    brief_gene_clarification_result,
)
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
    _split_symbols,
    gene_retrieve,
    run_bi_api,
)
from .resolve_query import (
    BriefGeneResolveError,
    resolve_brief_gene_user_query,
)

__all__ = [
    "BRIEF_CONFIG",
    "BRIEF_GENE_CONFIG_FIELD_MAP",
    "BRIEF_GENE_SECRET_FIELD_MAP",
    "BRIEF_GENE_SENSITIVE_FIELD_MAP",
    "BriefGeneAgent",
    "BriefGeneAgentState",
    "BriefGeneConversationAdapter",
    "BriefGeneConversationOperation",
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
    "_split_symbols",
    "brief_gene_function",
    "brief_gene_stream_seed",
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


@dataclass(frozen=True, slots=True)
class _BriefGeneRuntime:
    """Resolved configuration shared by one Brief Gene invocation."""

    effective_locale: SupportedLocale
    brief_config: Any
    sensitive_config: Any


def _build_brief_gene_runtime(
    locale: SupportedLocale | None,
    overrides: dict[str, Any],
) -> _BriefGeneRuntime:
    """Resolve locale and non-secret/secret configuration once."""
    return _BriefGeneRuntime(
        effective_locale=resolve_agent_locale(locale),
        brief_config=copy_config_with_overrides(
            BRIEF_CONFIG,
            overrides,
            BRIEF_GENE_CONFIG_FIELD_MAP,
        ),
        sensitive_config=copy_sensitive_config_with_overrides(
            get_sensitive_config(),
            overrides,
            field_map=BRIEF_GENE_SENSITIVE_FIELD_MAP,
            secret_field_map=BRIEF_GENE_SECRET_FIELD_MAP,
        ),
    )


def _cached_brief_gene_agent(runtime: _BriefGeneRuntime) -> BriefGeneAgent:
    """Return the cached agent for one resolved runtime configuration."""
    return get_cached_agent(
        "BriefGeneAgent",
        lambda: BriefGeneAgent(
            brief_config=runtime.brief_config,
            sensitive_config=runtime.sensitive_config,
            knowledge_agent=KnowledgeAgent(
                knowledge_config=runtime.brief_config,
                sensitive_config=runtime.sensitive_config,
            ),
        ),
        agent_fingerprint_values(
            brief_config=runtime.brief_config,
            sensitive_config=runtime.sensitive_config,
        ),
    )


async def _handle_brief_gene_conversation_prefix(
    adapter: BriefGeneConversationAdapter,
    projection: ContextProjection | None,
    runtime: _BriefGeneRuntime,
) -> dict[str, Any] | None:
    """Handle preparation, clarification, and follow-up-only turns."""
    if adapter.operation is None:
        if projection is None:
            return brief_gene_clarification_result(
                "Brief Gene context is unavailable; please clarify the gene."
            )
        adapter.prepare(projection)
    operation = adapter.operation
    if operation is BriefGeneConversationOperation.CLARIFY:
        adapter.mark_failed()
        return brief_gene_clarification_result(adapter.clarification_message)
    if operation is not BriefGeneConversationOperation.FOLLOW_UP:
        return None
    try:
        return await adapter.follow_up(
            lambda prompt: invoke_brief_gene_chat(
                prompt,
                config=runtime.brief_config,
                sensitive_config=runtime.sensitive_config,
                locale=runtime.effective_locale,
            )
        )
    except BriefGeneClarificationError as exc:
        adapter.mark_failed()
        return brief_gene_clarification_result(str(exc))


async def _run_brief_gene_conversation_report(
    adapter: BriefGeneConversationAdapter,
    runtime: _BriefGeneRuntime,
    thread_id: str | None,
) -> dict[str, Any]:
    """Resolve an active conversation turn and run its report graph."""
    resolver_query = adapter.resolver_query
    if not resolver_query:
        adapter.mark_failed()
        return brief_gene_clarification_result(adapter.clarification_message)
    try:
        resolved = await resolve_brief_gene_user_query(
            resolver_query,
            brief_config=runtime.brief_config,
            sensitive_config=runtime.sensitive_config,
            timeout_seconds=runtime.brief_config.TIMEOUT,
        )
    except BriefGeneResolveError as exc:
        adapter.mark_failed()
        return brief_gene_clarification_result(str(exc))
    agent = _cached_brief_gene_agent(runtime)
    try:
        result = await agent.arun(
            user_query=resolved.gene_id,
            locale=runtime.effective_locale,
            thread_id=adapter.thread_id or thread_id,
        )
    except BaseException:
        adapter.mark_failed()
        raise
    if not adapter.capture_result(result, resolved=resolved):
        return brief_gene_clarification_result(
            "Brief Gene did not return a usable report; please try again."
        )
    return result


async def brief_gene_function(
    user_query: str,
    *,
    locale: SupportedLocale | None = None,
    thread_id: str | None = None,
    conversation_adapter: BriefGeneConversationAdapter | None = None,
    conversation_projection: ContextProjection | None = None,
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
    runtime = _build_brief_gene_runtime(locale, kwargs)
    if conversation_adapter is not None:
        prefix = await _handle_brief_gene_conversation_prefix(
            conversation_adapter, conversation_projection, runtime
        )
        if prefix is not None:
            return prefix
        return await _run_brief_gene_conversation_report(
            conversation_adapter, runtime, thread_id
        )
    agent = _cached_brief_gene_agent(runtime)
    run_kwargs: dict[str, Any] = {
        "user_query": user_query,
        "locale": runtime.effective_locale,
    }
    if thread_id is not None:
        run_kwargs["thread_id"] = thread_id
    return await agent.arun(**run_kwargs)


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
    return agent.app, initial_brief_gene_state(
        args.user_query,
        locale=args.locale,
    )
