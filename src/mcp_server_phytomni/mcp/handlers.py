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

import functools
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

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
    ChatConfig,
    DataConfig,
    DeepGenomeConfig,
    DigitalDesignConfig,
    GeneNetworkConfig,
    InSilicoResearchConfig,
    KnowledgeConfig,
    ReviewConfig,
    ServerConfig,
)
from ..runtime.request_context import bind_run_id, current_request_user
from ..runtime.run_registry import RunRegistry, RunSpec
from ..runtime.task_manager import (
    RunContext,
    Submission,
    TaskManager,
    resolve_tasks_db_path,
)
from ..runtime.task_reconcile import reconcile_task
from ..storage.path_policy import IdFactory, RunIdentity
from ..storage.scratch import ScratchTarget, resolve_scratch_dir
from .handler_support import (
    analysis_platform_kwargs,
    chat_kwargs,
    coder_kwargs,
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
# up returning the wrapper's dict envelope. ``Optional`` honours the
# upstream chat/service.py signature (``phyto_chat_with_follow`` is
# typed ``Optional[Dict[str, Any]]`` because its retry helpers carry a
# stale dead-code ``return None`` path — same pattern Phase 13.2
# tightened on ``request_response_with_retries`` and that deserves its
# own follow-up audit step for ``agents/chat/service.py``).
HandlerResult = Optional[Dict[str, Any]]


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


def _extract_task_submissions(
    result: Mapping[str, Any], agent: str
) -> Tuple[Tuple[str, str, Optional[str]], ...]:
    """Extract per-task identity triples by per-agent wrapper shape.

    Each public submit wrapper returns task identity in its own shape,
    so the chokepoint dispatches by agent slug rather than guessing:

    - ``analyst`` / ``deep_genome``: ``task_id`` at the top level (with
      ``output_dir`` alongside). Analyst additionally carries
      ``input_fingerprint`` for the duplicate-submission dedup contract;
      other agents leave the slot ``None``.
    - ``research``: a ``task_ids`` dict mapping research-goal names to
      task ids; the top-level ``output_dir`` is shared across children.
    - ``network``: nested under ``network_task`` (``task_id`` +
      ``output_dir`` inside).
    - ``design``: a ``design_task_result`` list of AnalystAgent
      submission dicts (one per design kind: protein / promoter /
      terminator), each with its own ``task_id`` and ``output_dir``.

    Args:
        result: Raw wrapper result dict (pre-formatter).
        agent: Public agent alias (e.g. ``"analyst"``).

    Returns:
        Tuple of ``(task_id, output_dir, input_fingerprint)`` triples;
        empty when nothing recognizable is present so the caller skips
        writing. ``input_fingerprint`` is ``None`` for agents that do
        not participate in the dedup contract.
    """
    pairs: list[tuple[str, str, Optional[str]]] = []
    if agent in ("analyst", "deep_genome"):
        task_id = result.get("task_id")
        if isinstance(task_id, str) and task_id:
            fingerprint = result.get("input_fingerprint")
            pairs.append(
                (
                    task_id,
                    str(result.get("output_dir") or ""),
                    fingerprint if isinstance(fingerprint, str) else None,
                )
            )
    elif agent == "research":
        mapping = result.get("task_ids")
        if isinstance(mapping, Mapping):
            shared_output = str(result.get("output_dir") or "")
            pairs.extend(
                (str(value), shared_output, None)
                for value in mapping.values()
                if isinstance(value, str) and value
            )
    elif agent == "network":
        nested = result.get("network_task")
        if isinstance(nested, Mapping):
            task_id = nested.get("task_id")
            if isinstance(task_id, str) and task_id:
                pairs.append(
                    (
                        task_id,
                        str(nested.get("output_dir") or ""),
                        None,
                    )
                )
    elif agent == "design":
        design_results = result.get("design_task_result")
        if isinstance(design_results, list):
            for nested in design_results:
                if not isinstance(nested, Mapping):
                    continue
                task_id = nested.get("task_id")
                if isinstance(task_id, str) and task_id:
                    pairs.append(
                        (
                            task_id,
                            str(nested.get("output_dir") or ""),
                            None,
                        )
                    )
    return tuple(pairs)


def _record_submitted_task(result: Any, *, agent: str) -> None:
    """Persist submitted tasks plus their owning run row.

    Mints a fresh ``run_id`` via ``IdFactory().new_id("run", agent)``,
    writes one ``runs`` row (``origin="remote"``, ``status="running"``)
    via ``RunRegistry.create_run``, then writes one child task row per
    extracted task id — all sharing the same ``run_id`` so
    ``RunRegistry.reconcile`` can join them by ``tasks.run_id``.
    Best-effort: a registry / SQLite / OS error must never break an
    already-successful submission, so failures are swallowed.

    The chokepoint binds the freshly-minted ``run_id`` to the request
    contextvar **only after** every child task row has been written,
    so a half-failed record never surfaces a run id without its task
    ids — the HTTP layer then sees ``current_run_id() is None`` and
    returns ``(None, [])`` as a clean silent failure.

    A wrapper return carrying ``dedup_hit=True`` is a transparent
    passthrough for a duplicate submission: the prior caller already
    owns the task row through their own run, so minting a fresh run
    and ``INSERT OR REPLACE`` of the task row here would overwrite the
    prior ``run_id`` and orphan the original aggregate
    (``RunRegistry.list_runs`` would return the prior run with empty
    ``task_ids`` and the HTTP ``GET /v1/runs/{prior}`` aggregate would
    stay pinned at ``running``). The chokepoint therefore bails out
    before any registry mutation when it sees the sentinel; the second
    caller still receives the prior ``task_id`` and reads status
    through it directly.

    The MCP tool's return dict is *not* mutated (no ``run_id`` is
    surfaced to the client) so the existing stdio MCP contract stays
    byte-equivalent; the HTTP API path reads ``tasks.run_id`` back
    when it needs the run identity.

    Args:
        result: The wrapper result returned by a submit-style handler.
        agent: Public agent alias (e.g. ``"analyst"``) recorded on the
            run and task rows.
    """
    if not isinstance(result, dict):
        return
    if result.get("dedup_hit") is True:
        return
    submissions = _extract_task_submissions(result, agent)
    if not submissions:
        return
    user_id = current_request_user() or "anonymous"
    run_id = IdFactory().new_id("run", agent)
    now = datetime.now(timezone.utc).isoformat()
    db_path = resolve_tasks_db_path()
    # Seed the run row with the same envelope shape ``_terminal_payload``
    # writes later so a client polling ``GET /v1/runs/{id}`` while the
    # run is still in flight sees ``task_results`` / ``live_status`` /
    # ``artifacts`` keyed exactly as on the terminal branch, just with
    # placeholder ``submitted`` rows and an empty artifacts list.
    initial_task_rows = [
        {
            "task_id": task_id,
            "status": "submitted",
            "output_dir": output_dir,
        }
        for task_id, output_dir, _input_fingerprint in submissions
    ]
    initial_result: Dict[str, Any] = {
        "task_results": initial_task_rows,
        "live_status": initial_task_rows,
        "artifacts": [],
    }
    try:
        RunRegistry(db_path).create_run(
            RunSpec(
                run_id=run_id,
                user_id=user_id,
                agent=agent,
                origin="remote",
            ),
            result=initial_result,
        )
        manager = TaskManager(db_path)
        for task_id, output_dir, input_fingerprint in submissions:
            manager.record(
                Submission(
                    task_id=task_id,
                    status="submitted",
                    output_dir=output_dir,
                    run_context=RunContext(
                        run_id=run_id,
                        user_id=user_id,
                        agent=agent,
                        origin="remote",
                        created_at=now,
                        updated_at=now,
                    ),
                    input_fingerprint=input_fingerprint,
                )
            )
        bind_run_id(run_id)
    except (sqlite3.Error, OSError):
        return


def _records_submission(
    agent: str,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator factory: log a submit handler's run + task on return.

    The handler runs unchanged; its result is forwarded verbatim and
    also recorded in the unified run+task registry. ``functools.wraps``
    preserves the handler name so the ``TOOL_HANDLERS`` mapping in
    ``mcp/app.py`` is unaffected, and the static ``agent`` slug avoids
    name-introspection at call time.

    Args:
        agent: Public agent alias (e.g. ``"analyst"``) recorded on the
            run and task rows.

    Returns:
        Decorator that wraps an async submit handler.
    """

    def decorator(handler: Callable[..., Any]) -> Callable[..., Any]:
        """Wrap one async submit handler with the recorder hook."""

        @functools.wraps(handler)
        async def _wrapper(args: Any) -> Any:
            """Await the handler, record the run + task, return the result.

            Args:
                args: The validated tool-argument model.

            Returns:
                The handler's result, unchanged.
            """
            result = await handler(args)
            _record_submitted_task(result, agent=agent)
            return result

        return _wrapper

    return decorator


async def handle_chat_agent(args: ChatAgent) -> HandlerResult:
    """Execute ChatAgent with default runtime configuration.

    Args:
        args: Validated ChatAgent request schema.

    Returns:
        ChatAgent response envelope dict.
    """
    chat_config = ChatConfig()
    runtime = load_handler_runtime()
    return await phyto_chat_with_follow(
        user_query=args.user_query,
        obs_file_list=args.obs_file_list,
        server_dir=scratch_server_dir(chat_config, "chat"),
        **chat_kwargs(chat_config, runtime.sensitive),
        **obs_kwargs(chat_config, runtime.obs_credentials),
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
    return await multi_retrieve_generate(
        user_query=args.user_query,
        obs_file_list=args.obs_file_list,
        server_dir=scratch_server_dir(knowledge_config, "knowledge"),
        **chat_kwargs(knowledge_config, runtime.sensitive),
        **retrieve_kwargs(knowledge_config),
        **obs_kwargs(knowledge_config, runtime.obs_credentials),
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
    return await rewrite_nl2sql(
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
        dialog_id=data_config.DIALOG_ID,
        need_insight=data_config.NEED_INSIGHT,
        simplify_response=data_config.SIMPLIFY_RESPONSE,
        **chat_kwargs(data_config, runtime.sensitive),
    )


@_records_submission("analyst")
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
        **chat_kwargs(analyst_config, runtime.sensitive),
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
        **chat_kwargs(review_config, runtime.sensitive),
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
        bi_url=brief_config.BI_URL,
        bi_token=runtime.sensitive.BI_TOKEN.get_secret_value(),
        max_concurrency=brief_config.MAX_CONCURRENCY,
        **chat_kwargs(brief_config, runtime.sensitive),
        **retrieve_kwargs(brief_config),
    )


@_records_submission("deep_genome")
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
        create_task_url=deep_genome_config.CREATE_TASK_URL,
        update_task_url=deep_genome_config.UPDATE_TASK_URL,
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
        **chat_kwargs(deep_genome_config, runtime.sensitive),
        **retrieve_kwargs(deep_genome_config),
        **coder_kwargs(runtime.sensitive),
        **analysis_platform_kwargs(deep_genome_config),
    )


@_records_submission("research")
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
        server_dir=scratch_server_dir(in_silico_config, "research"),
        execute_code=in_silico_config.EXECUTE_CODE,
        **chat_kwargs(in_silico_config, runtime.sensitive),
        **retrieve_kwargs(in_silico_config),
        **obs_kwargs(in_silico_config, runtime.obs_credentials),
        **coder_kwargs(runtime.sensitive),
    )


@_records_submission("design")
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
        species=args.species,
        gene_id=args.gene_id,
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


@_records_submission("network")
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
        species=args.species,
        to_id=args.to_id,
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
        ``{task_id, status, output_dir, analysis_id, live_status}``;
        status is ``"unknown"`` for an unrecorded id.
    """
    return await reconcile_task(args.task_id)
