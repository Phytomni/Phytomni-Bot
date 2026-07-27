# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Handlers for MCP tool execution.

Public functions: handle_chat_agent, handle_knowledge_agent, handle_data_agent,
    handle_analyst_agent, handle_review_agent, handle_brief_gene_agent,
    handle_deep_genome_agent, handle_in_silico_research_agent,
    handle_digital_design_agent, handle_gene_network_agent,
    handle_get_task_status.
"""

from collections.abc import Mapping, Sequence
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

from ..agents.analyst.agent import retrieve_plan_submit
from ..agents.brief_gene.agent import brief_gene_function
from ..agents.chat.service import phyto_chat_with_follow
from ..agents.data.agent import rewrite_nl2sql
from ..agents.deep_genome.agent import gene_function
from ..agents.design.agent import design_module
from ..agents.knowledge.agent import multi_retrieve_generate
from ..agents.network.agent import network_analysis
from ..agents.research.agent import in_silico_research
from ..agents.review.agent import review_agent_function
from ..config.defaults import (
    AnalystConfig,
    BriefGeneConfig,
    DataConfig,
    DeepGenomeConfig,
    DigitalDesignConfig,
    GeneNetworkConfig,
    InSilicoResearchConfig,
    KnowledgeConfig,
    ReviewConfig,
    ServerConfig,
)
from ..runtime.request_context import current_request_user
from ..runtime.submit_recorder import records_submission
from ..runtime.task_reconcile import reconcile_task
from ..storage.path_policy import RunIdentity
from ..storage.scratch import ScratchTarget, resolve_scratch_dir
from .handler_support import (
    analysis_platform_kwargs,
    chat_call_kwargs,
    chat_kwargs,
    coder_kwargs,
    load_chat_runtime,
    load_handler_runtime,
    obs_kwargs,
    retrieve_kwargs,
)
from .schemas import (
    AnalystAgent,
    BriefGeneAgent,
    ChatAgent,
    DataAgent,
    DeepGenomeAgent,
    DigitalDesignAgent,
    GeneNetworkAgent,
    GetTaskStatus,
    InSilicoResearchAgent,
    KnowledgeAgent,
    ReviewAgent,
)

# Public alias for handler return shape: every ``handle_*_agent`` ends
# up returning the wrapper's dict envelope. The chat-side retry chain
# (``phyto_chat_with_follow`` → ``phyto_chat`` → ``_run_phyto_chat``)
# either returns a Dict or raises McpError on retry exhaustion, so the
# handler surface is unconditionally ``Dict[str, Any]``; no handler
# branches return None.
HandlerResult = dict[str, Any]

PrivateConversationMessages = tuple[dict[str, str], ...]
_private_conversation_messages: ContextVar[PrivateConversationMessages] = (
    ContextVar("private_conversation_messages", default=())
)
PrivateAgentThreadId = str | None
_private_agent_thread_id: ContextVar[PrivateAgentThreadId] = ContextVar(
    "private_agent_thread_id", default=None
)
PrivateAgentState = dict[str, Any]
PrivateAgentStateContext = PrivateAgentState | None
_private_agent_state: ContextVar[PrivateAgentStateContext] = ContextVar(
    "private_agent_state", default=None
)


def set_private_conversation_messages(
    messages: Sequence[Mapping[str, str]],
) -> Token[PrivateConversationMessages]:
    """Set bounded V1 history for one in-process handler dispatch."""
    return _private_conversation_messages.set(
        tuple(dict(message) for message in messages)
    )


def reset_private_conversation_messages(
    token: Token[PrivateConversationMessages],
) -> None:
    """Restore the private handler context after one raw dispatch."""
    _private_conversation_messages.reset(token)


def private_conversation_messages() -> PrivateConversationMessages:
    """Return V1 history available only to the active handler invocation."""
    return _private_conversation_messages.get()


def set_private_agent_thread_id(
    thread_id: str | None,
) -> Token[PrivateAgentThreadId]:
    """Set the stable V1 Chat thread for one in-process dispatch."""
    return _private_agent_thread_id.set(thread_id)


def reset_private_agent_thread_id(
    token: Token[PrivateAgentThreadId],
) -> None:
    """Restore the private thread context after one raw dispatch."""
    _private_agent_thread_id.reset(token)


def private_agent_thread_id() -> str | None:
    """Return the stable V1 Chat thread for the active dispatch."""
    return _private_agent_thread_id.get()


def set_private_agent_state(
    state: Mapping[str, Any] | None,
) -> Token[PrivateAgentStateContext]:
    """Set private per-dispatch state that must stay out of public schemas."""
    return _private_agent_state.set(dict(state or {}))


def reset_private_agent_state(token: Token[PrivateAgentStateContext]) -> None:
    """Restore the previous private agent state after one dispatch."""
    _private_agent_state.reset(token)


def private_agent_state() -> PrivateAgentState:
    """Return the private per-dispatch state for the active handler."""
    return dict(_private_agent_state.get() or {})


def scratch_server_dir(config: ServerConfig, scope: str) -> str:
    """Return an obsfs-or-local scratch dir for handler ``server_dir`` use.

    Builds a fresh RunIdentity scoped to the handler call, then routes
    through resolve_scratch_dir so obsfs-mounted hosts land under
    ``agent_data/user_data/<user>/runs/.../<scope>/tmp`` and other hosts
    use ``<config.TEMP_DIR>/<run_id>/<scope>``.
    """
    run_identity = RunIdentity.create(
        user_id=current_request_user(), scope=scope
    )
    return resolve_scratch_dir(
        "tmp",
        run_identity,
        scope,
        ScratchTarget(
            bucket_name=config.BUCKET_NAME,
            local_fallback=Path(config.TEMP_DIR),
        ),
    )


async def handle_chat_agent(args: ChatAgent) -> HandlerResult:
    """Execute ChatAgent with default runtime configuration.

    Args:
        args: Validated ChatAgent request schema.

    Returns:
        ChatAgent response envelope dict.
    """
    chat_config, runtime = load_chat_runtime()
    thread_kwargs = {}
    if (thread_id := private_agent_thread_id()) is not None:
        thread_kwargs["thread_id"] = thread_id
    return await phyto_chat_with_follow(
        **chat_call_kwargs(
            request=args,
            server_dir=scratch_server_dir(chat_config, "chat"),
            config=chat_config,
            runtime=runtime,
        ),
        conversation_messages=private_conversation_messages(),
        **thread_kwargs,
    )


async def handle_knowledge_agent(args: KnowledgeAgent) -> HandlerResult:
    """Execute KnowledgeAgent with default runtime configuration.

    Args:
        args: Validated KnowledgeAgent request schema.

    Returns:
        KnowledgeAgent response envelope dict.
    """
    knowledge_config = KnowledgeConfig()
    runtime = load_handler_runtime()
    private_state = private_agent_state()
    thread_kwargs = {}
    if (thread_id := private_agent_thread_id()) is not None:
        thread_kwargs["thread_id"] = thread_id
    return await multi_retrieve_generate(
        user_query=args.user_query,
        obs_file_list=args.obs_file_list,
        server_dir=scratch_server_dir(knowledge_config, "knowledge"),
        conversation_messages=private_conversation_messages(),
        retrieval_query=private_state.get("retrieval_query"),
        answer_context=private_state.get("answer_context", ""),
        **chat_kwargs(knowledge_config, runtime.sensitive, locale=args.locale),
        **retrieve_kwargs(knowledge_config),
        **obs_kwargs(knowledge_config, runtime.obs_credentials),
        **thread_kwargs,
    )


async def handle_data_agent(args: DataAgent) -> HandlerResult:
    """Execute DataAgent with default runtime configuration.

    Args:
        args: Validated DataAgent request schema (NL question).

    Returns:
        DataAgent response envelope dict.
    """
    data_config = DataConfig()
    runtime = load_handler_runtime()
    thread_id = private_agent_thread_id()
    dialog_id = (
        f"{thread_id}-nl2sql"
        if thread_id is not None
        else data_config.DIALOG_ID
    )
    result = await rewrite_nl2sql(
        user_query=args.user_query,
        retrieve_url=data_config.RETRIEVE_URL,
        data_repo_id=data_config.DATA_REPO_ID,
        page_num=data_config.PAGE_NUM,
        page_size=data_config.DATA_PAGE_SIZE,
        filter_string=data_config.FILTER_STRING,
        scope=data_config.SCOPE,
        rerank_url=data_config.RERANK_URL,
        rerank_batch_size=data_config.RERANK_BATCH_SIZE,
        score_threshold=data_config.SCORE_THRESHOLD,
        database_url=data_config.DATABASE_URL,
        workspace_id=data_config.WORKSPACE_ID,
        subject_id=data_config.SUBJECT_ID,
        dialog_id=dialog_id,
        thread_id=thread_id,
        need_insight=data_config.NEED_INSIGHT,
        simplify_response=data_config.SIMPLIFY_RESPONSE,
        **chat_kwargs(data_config, runtime.sensitive, locale=args.locale),
    )
    adapter = private_agent_state().get("data_adapter")
    capture_result = getattr(adapter, "capture_result", None)
    if callable(capture_result):
        capture_result(result)
    return result


@records_submission("analyst")
async def handle_analyst_agent(args: AnalystAgent) -> HandlerResult:
    """Execute AnalystAgent with default runtime configuration.

    Args:
        args: Validated AnalystAgent request schema.

    Returns:
        AnalystAgent submission envelope dict (task_id + output_dir +
        optional input_fingerprint for the dedup contract).
    """
    analyst_config = AnalystConfig()
    runtime = load_handler_runtime()
    return await retrieve_plan_submit(
        goal_description=args.goal_description,
        data_list=args.data_list,
        user_id=current_request_user() or analyst_config.USER_ID,
        is_create_dir=analyst_config.CREATE_DIR,
        output_dir=analyst_config.OUTPUT_DIR,
        obs_file_list=args.obs_file_list,
        server_dir=scratch_server_dir(analyst_config, "analyst"),
        execute_code=analyst_config.EXECUTE_CODE,
        task_name=analyst_config.TASK_NAME + "-retrieve-plan",
        compute_resource=analyst_config.COMPUTE_RESOURCE,
        meta_meta=None,
        **chat_kwargs(analyst_config, runtime.sensitive, locale=args.locale),
        **retrieve_kwargs(analyst_config),
        **obs_kwargs(analyst_config, runtime.obs_credentials),
        **coder_kwargs(runtime.sensitive),
        **analysis_platform_kwargs(analyst_config),
    )


async def handle_review_agent(args: ReviewAgent) -> HandlerResult:
    """Execute ReviewAgent with default runtime configuration.

    Args:
        args: Validated ReviewAgent request schema.

    Returns:
        ReviewAgent response envelope dict.
    """
    review_config = ReviewConfig()
    runtime = load_handler_runtime()
    return await review_agent_function(
        user_query=args.user_query,
        obs_file_list=args.obs_file_list,
        server_dir=scratch_server_dir(review_config, "review"),
        **chat_kwargs(review_config, runtime.sensitive, locale=args.locale),
        **retrieve_kwargs(review_config),
        **obs_kwargs(review_config, runtime.obs_credentials),
    )


async def handle_brief_gene_agent(args: BriefGeneAgent) -> HandlerResult:
    """Execute BriefGeneAgent with default runtime configuration.

    Args:
        args: Validated BriefGeneAgent request schema (gene/transcript id).

    Returns:
        BriefGeneAgent response envelope dict.
    """
    brief_config = BriefGeneConfig()
    runtime = load_handler_runtime()
    return await brief_gene_function(
        user_query=args.user_query,
        max_concurrency=brief_config.MAX_CONCURRENCY,
        **chat_kwargs(brief_config, runtime.sensitive, locale=args.locale),
        **retrieve_kwargs(brief_config),
    )


@records_submission("deep_genome")
async def handle_deep_genome_agent(args: DeepGenomeAgent) -> HandlerResult:
    """Execute DeepGenomeAgent with default runtime configuration.

    Args:
        args: Validated DeepGenomeAgent request schema.

    Returns:
        DeepGenomeAgent submission envelope dict (task_id + output_dir).
    """
    deep_genome_config = DeepGenomeConfig()
    runtime = load_handler_runtime()
    return await gene_function(
        species_code=args.species_code,
        gene_id=args.gene_id,
        user_id=current_request_user() or deep_genome_config.USER_ID,
        batch=deep_genome_config.BATCH,
        epic_type=deep_genome_config.EPIC_TYPE,
        database_url=deep_genome_config.DATABASE_URL,
        workspace_id=deep_genome_config.WORKSPACE_ID,
        subject_id=deep_genome_config.SUBJECT_ID,
        dialog_id=deep_genome_config.DIALOG_ID,
        need_insight=deep_genome_config.NEED_INSIGHT,
        deepgenome_data=deep_genome_config.DEEPGENOME_DATA,
        output_dir=deep_genome_config.OUTPUT_DIR,
        obs_server=deep_genome_config.OBS_SERVER,
        bucket_name=deep_genome_config.BUCKET_NAME,
        deepgenome_out=deep_genome_config.DEEPGENOME_OUT,
        download_path=deep_genome_config.DOWNLOAD_PATH,
        marker=deep_genome_config.DOWNLOAD_MARKER,
        max_keys=deep_genome_config.DOWNLOAD_MAX_KEYS,
        max_concurrency=deep_genome_config.MAX_CONCURRENCY,
        max_poll=deep_genome_config.MAX_POLL,
        access_key_id=runtime.obs_credentials[0],
        secret_access_key=runtime.obs_credentials[1],
        **chat_kwargs(
            deep_genome_config, runtime.sensitive, locale=args.locale
        ),
        **retrieve_kwargs(deep_genome_config),
        **coder_kwargs(runtime.sensitive),
        **analysis_platform_kwargs(deep_genome_config),
    )


@records_submission("research")
async def handle_in_silico_research_agent(
    args: InSilicoResearchAgent,
) -> HandlerResult:
    """Execute InSilicoResearchAgent with default runtime configuration.

    Args:
        args: Validated InSilicoResearchAgent request schema.

    Returns:
        InSilicoResearchAgent submission envelope dict (task_ids dict
        keyed by research goal + shared output_dir).
    """
    in_silico_config = InSilicoResearchConfig()
    runtime = load_handler_runtime()
    return await in_silico_research(
        user_query=args.user_query,
        data_list=args.data_list,
        user_id=current_request_user(),
        output_dir=in_silico_config.OUTPUT_DIR,
        obs_file_list=args.obs_file_list,
        interop_mode=args.interop_mode,
        interop_targets=args.interop_targets,
        server_dir=scratch_server_dir(in_silico_config, "research"),
        execute_code=in_silico_config.EXECUTE_CODE,
        **chat_kwargs(in_silico_config, runtime.sensitive, locale=args.locale),
        **retrieve_kwargs(in_silico_config),
        **obs_kwargs(in_silico_config, runtime.obs_credentials),
        **coder_kwargs(runtime.sensitive),
    )


@records_submission("design")
async def handle_digital_design_agent(
    args: DigitalDesignAgent,
) -> HandlerResult:
    """Execute DigitalDesignAgent with default runtime configuration.

    Args:
        args: Validated DigitalDesignAgent request schema.

    Returns:
        DigitalDesignAgent submission envelope dict (design_task_result
        list with one entry per design kind: protein / promoter /
        terminator).
    """
    design_config = DigitalDesignConfig()
    runtime = load_handler_runtime()
    return await design_module(
        species_code=args.species_code,
        gene_id=args.gene_id,
        locale=args.locale,
        interop_mode=args.interop_mode,
        interop_targets=args.interop_targets,
        user_id=current_request_user() or design_config.USER_ID,
        batch=True,
        enable_auto_select=False,
        prompt_file=design_config.PROMPT_FILE,
        deepgenome_data=design_config.DEEPGENOME_DATA,
        output_dir=design_config.OUTPUT_DIR,
        obs_server=design_config.OBS_SERVER,
        bucket_name=design_config.BUCKET_NAME,
        max_poll=design_config.MAX_POLL,
        access_key_id=runtime.obs_credentials[0],
        secret_access_key=runtime.obs_credentials[1],
        timeout=design_config.TIMEOUT,
        retriable_codes=design_config.RETRIABLE_CODES,
        max_retries=design_config.MAX_RETRIES,
        **coder_kwargs(runtime.sensitive),
        **analysis_platform_kwargs(design_config),
    )


@records_submission("network")
async def handle_gene_network_agent(
    args: GeneNetworkAgent,
) -> HandlerResult:
    """Execute GeneNetworkAgent with default runtime configuration.

    Args:
        args: Validated GeneNetworkAgent request schema.

    Returns:
        GeneNetworkAgent submission envelope dict (nested network_task
        with task_id + output_dir).
    """
    network_config = GeneNetworkConfig()
    runtime = load_handler_runtime()
    return await network_analysis(
        species_code=args.species_code,
        to_id=args.to_id,
        locale=args.locale,
        user_id=current_request_user() or network_config.USER_ID,
        batch=False,
        prompt_file=network_config.PROMPT_FILE,
        deepgenome_data=network_config.DEEPGENOME_DATA,
        output_dir=network_config.OUTPUT_DIR,
        obs_server=network_config.OBS_SERVER,
        bucket_name=network_config.BUCKET_NAME,
        max_poll=network_config.MAX_POLL,
        access_key_id=runtime.obs_credentials[0],
        secret_access_key=runtime.obs_credentials[1],
        timeout=network_config.TIMEOUT,
        retriable_codes=network_config.RETRIABLE_CODES,
        max_retries=network_config.MAX_RETRIES,
        **coder_kwargs(runtime.sensitive),
        **analysis_platform_kwargs(network_config),
    )


async def handle_get_task_status(args: GetTaskStatus) -> HandlerResult:
    """Return a submitted task's status without ever blocking.

    Delegates to ``runtime.task_reconcile.reconcile_task`` so the MCP
    tool and the upcoming run-registry status endpoint share one
    non-blocking local+live status implementation (a single SELECT plus
    exactly one remote ``task_status`` lookup, never the
    ``wait_for_completion`` poll loop, so this cannot re-create the C-1
    MCP timeout).

    Args:
        args: Validated GetTaskStatus request schema (task_id).

    Returns:
        ``{task_id, status, output_dir, analysis_id, live_status}`` plus
        the public DeepGenome report/progress snapshot when the id is an
        umbrella task; status is ``"unknown"`` for an unrecorded id.
    """
    return await reconcile_task(args.task_id)
