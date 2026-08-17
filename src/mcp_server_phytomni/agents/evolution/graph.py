# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Node functions and conditional router for the evolution LangGraph.

Two nodes mirror ``evo_test_analysis``:
``resolve_target_taxids_node`` runs the chat completion + per-target
taxonomy lookup, and ``submit_evolution_task_node`` issues the
analyst submission. Nodes route every external call through the
``agent`` module namespace so existing wrapper-level
``monkeypatch.setattr`` still intercepts the dependencies.
"""

from __future__ import annotations

from typing import Any, Literal, NamedTuple

from ...config.settings import get_sensitive_config
from ...graphs.analyst_dispatch_adapters import (
    build_analyst_dispatch_request,
    submit_analyst_via_subgraph,
)
from ..analyst.submission import _build_submit_agent
from . import agent
from .agent import (
    DEEP_GENOME_CONFIG,
    evolution_submit_kwargs,
    target_taxids,
)
from .state import EvolutionState


class _EvolutionSubmitInputs(NamedTuple):
    """Narrative inputs the evolution dispatch helpers consume.

    Packed into a NamedTuple so the four narrative fields stay
    together. The three remaining helper parameters (``gene_id`` /
    ``submit_kwargs`` / ``user_id``) are keyword-only so the call
    site reads as documentation.
    """

    goal_description: str
    data_list: dict[str, str]
    output_dir: str
    meta: str


async def resolve_target_taxids_node(
    state: EvolutionState,
) -> dict[str, Any]:
    """Resolve target taxonomy ids for the evolution query.

    A consumer (e.g. deep_genome) may pre-supply ``target_taxids`` in the
    input to pin the taxonomic scope without an NL query; a truthy
    pre-supplied value passes through unchanged and skips the chat
    extraction. Otherwise this delegates to
    :func:`agents.evolution.agent.target_taxids` so the chat extraction
    and per-species taxonomy lookup stay in one place, returning the
    comma-joined taxids string (or the ``"All"`` sentinel); the
    conditional router downstream branches on whether the value is
    ``None``. Always sets ``evolution_agents_task`` on the failure path
    so the schema-filtered output keeps a consistent shape across both
    branches.
    """
    presupplied = state.get("target_taxids")
    if presupplied:
        return {"target_taxids": presupplied}
    kwargs = {
        **(state.get("kwargs") or {}),
        "locale": state.get("locale"),
    }
    taxids = await target_taxids(state["query"], kwargs)
    if taxids is None:
        return {"target_taxids": None, "evolution_agents_task": None}
    return {"target_taxids": taxids}


async def submit_evolution_task_node(
    state: EvolutionState,
) -> dict[str, Any]:
    """Submit the evolution analysis task for the resolved taxids.

    Mirrors the post-extraction body of
    :func:`agents.evolution.agent.evo_test_analysis`: builds the
    goal prompt + data list and forwards the submission to the
    analyst ``submit``. The output directory is left for the dispatch
    seam to create from the input fingerprint (the tenant-neutral
    shared key), so this node no longer pre-materialises a user-scoped
    dir. Re-routes ``get_prompt`` / ``get_data_list`` / ``submit``
    through the ``agent`` module namespace so the existing wrapper-level
    ``monkeypatch.setattr`` calls still intercept the dependencies.
    """
    kwargs = state.get("kwargs") or {}
    enable_auto_select = state.get("enable_auto_select", False)
    taxids = state.get("target_taxids")
    if taxids is None:
        raise RuntimeError(
            "submit_evolution_task_node reached with target_taxids=None; "
            "route_after_resolve should have short-circuited to END"
        )
    species_code = state["species_code"]
    gene_id = state["gene_id"]
    user_id = kwargs.get("user_id", DEEP_GENOME_CONFIG.USER_ID)
    prompt_file = kwargs.get("prompt_file", DEEP_GENOME_CONFIG.PROMPT_FILE)
    deepgenome_data = kwargs.get(
        "deepgenome_data", DEEP_GENOME_CONFIG.DEEPGENOME_DATA
    )
    output_dir = kwargs.get("output_dir", DEEP_GENOME_CONFIG.OUTPUT_DIR)
    goal_description = agent.get_prompt(
        prompt_file,
        "user/evolution_agents_analysis",
        {"gene_id": gene_id, "target_taxid": taxids},
    )
    data_list = agent.get_data_list(
        deepgenome_data, "evolution_analysis", species_code
    )
    meta = agent.get_prompt(prompt_file, "user/evolution_agents_meta")
    evo_task = await _submit_evolution_via_subgraph(
        _EvolutionSubmitInputs(
            goal_description=goal_description,
            data_list=data_list,
            output_dir=output_dir,
            meta=meta,
        ),
        user_id=user_id,
        gene_id=gene_id,
        submit_kwargs=evolution_submit_kwargs(kwargs, enable_auto_select),
        is_polling=state.get("is_polling", False),
    )
    return {"evolution_agents_task": evo_task}


async def _submit_evolution_via_subgraph(
    inputs: _EvolutionSubmitInputs,
    *,
    user_id: Any,
    gene_id: str,
    submit_kwargs: dict[str, Any],
    is_polling: bool = False,
) -> dict[str, Any]:
    """Dispatch the evolution analysis task through the analyst subgraph.

    Mirrors ``analyst.submit`` (the legacy free-function path) but
    routes through ``submit_analyst_via_subgraph`` so the analyst
    graph runs as a structured subgraph. Builds a per-call
    ``AnalystAgent`` via :func:`_build_submit_agent` so cache and
    sensitive-config behavior matches the legacy path.

    Args:
        inputs: ``_EvolutionSubmitInputs`` carrying the four narrative
            fields (``goal_description`` / ``data_list`` /
            ``output_dir`` / ``meta``); packed into a NamedTuple so no
            field is dropped at the call site.
        user_id: Resolved user identifier; threaded through the
            cached-agent kwargs to match the legacy ``user_id`` arg
            on ``analyst.submit``.
        gene_id: Gene identifier from the evolution state; used as the
            dispatch ``target_id`` so the analyst thread id includes
            the analysed gene.
        submit_kwargs: The same kwargs ``evolution_submit_kwargs``
            forwards into ``analyst.submit`` on the legacy path; the
            cache labels and ``compute_resource`` derive from them.
        is_polling: When ``True`` the analyst submission blocks until the
            task reaches a terminal state; defaults to ``False`` for the
            submit-only external surface.

    Returns:
        Dispatch state dict (``task_id`` / ``output_dir`` / ``plan`` /
        ``tool_usages`` / ``task_status``) from
        :func:`submit_analyst_via_subgraph`.
    """
    submit_kwargs_with_user = {**submit_kwargs, "user_id": user_id}
    analyst_agent, _resolved_output_dir, _compute_resource, _thread_id = (
        _build_submit_agent(
            submit_kwargs_with_user,
            "analyst-evolution",
            "submit_via_subgraph",
            "AnalystAgent.evolution_submit",
        )
    )
    compute_resource = submit_kwargs.get(
        "compute_resource", DEEP_GENOME_CONFIG.COMPUTE_RESOURCE
    )
    request = build_analyst_dispatch_request(
        "evolution_analysis",
        gene_id,
        inputs._asdict(),
        compute_resource,
    )
    return await submit_analyst_via_subgraph(
        analyst_agent,
        DEEP_GENOME_CONFIG,
        get_sensitive_config(),
        request,
        is_polling=is_polling,
    )


def route_after_resolve(
    state: EvolutionState,
) -> Literal["submit_evolution_task_node", "__end__"]:
    """Route to submit when taxids resolved, else short-circuit to END.

    Mirrors the legacy wrapper's early-return on ``target_taxids``
    returning ``None``: the failure path produces no evolution
    task and skips the analyst submission.
    """
    if state.get("target_taxids") is None:
        return "__end__"
    return "submit_evolution_task_node"
