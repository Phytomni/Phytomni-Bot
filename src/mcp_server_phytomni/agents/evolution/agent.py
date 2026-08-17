# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Evolution analysis helpers built on Phytomni analyst workflows.

This module exposes ``evo_test_analysis`` plus the public helpers
(``target_taxids`` / ``find_spa_taxids`` / ``evolution_output_dir``
/ ``evolution_submit_kwargs`` / ``evolution_chat_kwargs``) the
LangGraph nodes in :mod:`.graph` call into. ``evo_test_analysis``
itself is a thin wrapper that delegates to the compiled evolution
subgraph via :func:`ainvoke_graph`.
"""

import importlib
from collections.abc import Mapping
from functools import lru_cache
from json import loads
from typing import Any

from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from ...auth.iam import get_token
from ...common.prompts import get_prompt
from ...common.relay_client import RelayRequestOptions, current_relay_client
from ...config.defaults import DeepGenomeConfig, resolve_compute_resource
from ...config.relay_mode import relay_mode_enabled
from ...config.settings import get_sensitive_config
from ...graphs.chat_adapters import invoke_chat_content
from ...runtime.langgraph_runner import ainvoke_graph
from ...runtime.outbound import (
    OutboundHttpProfile,
    OutboundPoolName,
    current_outbound_runtime,
)
from ...storage.path_policy import RunIdentity
from ..analyst.agent import submit
from ..chat.service import _cached_chat_app
from ..shared.analysis_storage import create_output_dir, get_data_list
from ..shared.options import (
    SubmitKwargsSpec,
    build_chat_kwargs,
    build_submit_kwargs,
    resolve_agent_locale,
)

DEEP_GENOME_CONFIG = DeepGenomeConfig()
_EVOLUTION_LOOKUP_UNAVAILABLE = (
    "Evolution taxonomy lookup temporarily unavailable"
)

__all__ = [
    "DEEP_GENOME_CONFIG",
    "create_output_dir",
    "evo_test_analysis",
    "evolution_chat_kwargs",
    "evolution_output_dir",
    "evolution_submit_kwargs",
    "find_spa_taxids",
    "get_data_list",
    "get_prompt",
    "get_token",
    "submit",
    "target_taxids",
]


def evolution_chat_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return chat kwargs for evolution target-species extraction."""
    return build_chat_kwargs(
        kwargs, DEEP_GENOME_CONFIG, get_sensitive_config()
    )


def evolution_submit_kwargs(
    kwargs: dict[str, Any],
    enable_auto_select: bool,
) -> dict[str, Any]:
    """Return Analyst submit kwargs for the evolution workflow."""
    sensitive = get_sensitive_config()
    return build_submit_kwargs(
        kwargs,
        DEEP_GENOME_CONFIG,
        sensitive,
        sensitive.obs_credentials(),
        SubmitKwargsSpec(
            task_name="evolution-agents-evo-task",
            compute_resource=resolve_compute_resource(
                DEEP_GENOME_CONFIG, "evolution_analysis"
            ),
            is_create_dir=False,
            enable_auto_select=enable_auto_select,
        ),
    )


async def find_spa_taxids(
    spa_names: str,
    request_timeout: float | None = None,
) -> list[str]:
    """Return taxonomy ids for a target species name."""
    timeout = request_timeout
    if timeout is None:
        timeout = DEEP_GENOME_CONFIG.TIMEOUT
    try:
        if relay_mode_enabled():
            # Relay mode injects the operator IAM token and bypasses the proxy.
            response_taxid_data = await current_relay_client().get_json(
                f"spa-faq/{DEEP_GENOME_CONFIG.SPA_REPO_ID}",
                pool=OutboundPoolName.SPA_FAQ,
                options=RelayRequestOptions(
                    message="SPA-FAQ lookup failed",
                    request_timeout=timeout,
                ),
                query={
                    "question": spa_names,
                    "page_size": "10",
                    "page_num": "1",
                },
            )
        else:
            url = DEEP_GENOME_CONFIG.SPA_FAQ_URL.format(
                repo_id=DEEP_GENOME_CONFIG.SPA_REPO_ID
            )
            headers = {
                "X-Auth-Token": await get_token(request_timeout=timeout),
                "Content-Type": "application/json",
            }
            request_params: dict[str, str | int] = {
                "question": spa_names,
                "page_size": 10,
                "page_num": 1,
            }
            # trust_env=False mirrors the previous proxies={'http': None,
            # 'https': None} on the requests call: this endpoint sits on a
            # bare-IP corporate URL, so inheriting HTTP(S)_PROXY from the host
            # env would route it through a proxy that cannot reach it.
            client = current_outbound_runtime().http.for_pool(
                OutboundPoolName.SPA_FAQ,
                profile=OutboundHttpProfile.DIRECT_UPSTREAM,
            )
            response = await client.request(
                "GET",
                url,
                headers=headers,
                params=request_params,
                timeout=timeout,
            )
            response_taxid_data = response.json()
        return _parse_spa_taxids(response_taxid_data)
    except Exception:
        raise _evolution_lookup_unavailable() from None


def _evolution_lookup_unavailable() -> McpError:
    """Build the fixed public error for a failed taxonomy lookup."""
    return McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message=_EVOLUTION_LOOKUP_UNAVAILABLE,
        )
    )


def _parse_spa_taxids(payload: Any) -> list[str]:
    """Validate one SPA-FAQ payload and return taxids in provider order."""
    if not isinstance(payload, Mapping):
        raise ValueError("invalid taxonomy response")
    total = payload.get("total")
    records = payload.get("records")
    if (
        not isinstance(total, int)
        or isinstance(total, bool)
        or total < 0
        or not isinstance(records, list)
    ):
        raise ValueError("invalid taxonomy response")
    if total == 0:
        if records:
            raise ValueError("invalid taxonomy response")
        return []
    if not records:
        raise ValueError("invalid taxonomy response")

    taxids: list[str] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("invalid taxonomy response")
        answer = record.get("answer")
        if not isinstance(answer, str):
            raise ValueError("invalid taxonomy response")
        taxid = answer.split(".", 1)[0].strip()
        if not taxid.isdigit() or int(taxid) <= 0:
            raise ValueError("invalid taxonomy response")
        taxids.append(taxid)
    return taxids


async def target_taxids(query: str, kwargs: dict[str, Any]) -> str | None:
    """Extract target taxonomy ids for an evolution query."""
    prompt_file = kwargs.get("prompt_file", DEEP_GENOME_CONFIG.PROMPT_FILE)
    prompt = get_prompt(
        prompt_file,
        "user/get_taxid_meta",
        {"user_query": query},
    )
    chat_kwargs_bag = evolution_chat_kwargs(kwargs)
    content = await invoke_chat_content(
        _cached_chat_app(), prompt, chat_kwargs_bag
    )
    if content is None:
        return None
    target_spa_list = loads(content.replace("'", '"'))
    targets = target_spa_list["target_spa_list"]
    if targets[0] == "All":
        return "All"
    taxid_lists = [
        await find_spa_taxids(
            spa,
            request_timeout=kwargs.get("timeout", DEEP_GENOME_CONFIG.TIMEOUT),
        )
        for spa in targets
    ]
    return ",".join(taxid for taxids in taxid_lists for taxid in taxids)


async def evolution_output_dir(
    user_id: str | None, kwargs: dict[str, Any]
) -> str:
    """Create the output directory for a non-batch evolution task."""
    run_identity = RunIdentity.create(
        user_id=user_id,
        scope="evolution_agents_task",
    )
    return await create_output_dir(
        user_id=run_identity.user_id,
        task="evolution_agents_task",
        bucket_name=kwargs.get("bucket_name", DEEP_GENOME_CONFIG.BUCKET_NAME),
        run_identity=run_identity,
    )


@lru_cache(maxsize=1)
def _cached_evolution_app() -> Any:
    """Lazy singleton of the compiled evolution subgraph.

    Resolves ``builder`` dynamically via ``importlib.import_module``
    rather than a top-level ``from .builder import build_evolution_graph``
    because the builder pulls ``graph.py`` which in turn re-imports
    this module for the helper namespace; deferring the resolve to
    first call lets every module finish loading before the compile
    runs. ``lru_cache`` makes the compile happen at most once and
    gives test suites a ``cache_clear()`` hook.
    """
    builder_module = importlib.import_module(".builder", package=__package__)
    return builder_module.build_evolution_graph()


async def evo_test_analysis(
    query: str,
    species_code: str,
    gene_id: str,
    batch: bool = False,
    enable_auto_select: bool = False,
    **kwargs: Any,
) -> dict:
    """Run an evolution analysis workflow for a target gene.

    Delegates to the compiled evolution subgraph: the
    ``resolve_target_taxids_node`` runs the chat-based taxonomy
    extraction and ``submit_evolution_task_node`` issues the
    analyst submission, with the conditional edge short-circuiting
    to END when taxonomy resolution yields no parseable result.

    Args:
        query: Natural-language evolution analysis request.
        species_code: Three-letter species code used to select
            prepared data (e.g., "osa", "ath").
        gene_id: Target gene identifier for the analysis prompt.
        batch: Whether to reuse the provided output directory.
        enable_auto_select: Whether AnalystAgent may auto-select
            tools.
        locale: Optional response locale.
        **kwargs: Keyword-compatible chat, OBS, and submit overrides.

    Returns:
        Dictionary containing the submitted evolution task, or
        ``{"evolution_agents_task": None}`` when taxonomy
        extraction fails. The failure path now returns the same
        key as the happy path; previously the wrapper inconsistently
        returned ``{"evolution_task": None}`` on failure.
    """
    initial_state: dict[str, Any] = {
        "query": query,
        "species_code": species_code,
        "gene_id": gene_id,
        "batch": batch,
        "enable_auto_select": enable_auto_select,
        "locale": resolve_agent_locale(kwargs.get("locale")),
        "kwargs": kwargs,
    }
    final_state = await ainvoke_graph(_cached_evolution_app(), initial_state)
    return {
        "evolution_agents_task": final_state.get("evolution_agents_task"),
    }
