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

from typing import Any, Dict, Literal, NamedTuple

from ...config.settings import get_sensitive_config
from ...graphs.analyst_dispatch_adapters import submit_analyst_via_subgraph
from ..analyst.submission import _build_submit_agent
from . import agent
from .agent import (
    DEEP_GENOME_CONFIG,
    evolution_output_dir,
    evolution_submit_kwargs,
    target_taxids,
)
from .state import EvolutionState


class _EvolutionSubmitInputs(NamedTuple):
    """Narrative inputs the evolution dispatch helpers consume.

    Packed into a NamedTuple so :func:`_submit_evolution_via_subgraph`
    stays under pylint's ``max-args=5`` ceiling. The three remaining
    helper parameters (``gene_id`` / ``submit_kwargs`` / ``user_id``)
    are keyword-only so the call site reads as documentation.
    """

    goal_description: str
    data_list: Dict[str, str]
    output_dir: str
    meta: str


async def resolve_target_taxids_node(
    state: EvolutionState,
) -> Dict[str, Any]:
    """Resolve target taxonomy ids for the evolution query.

    Delegates to :func:`agents.evolution.agent.target_taxids` so the
    chat extraction and per-species taxonomy lookup stay in one
    place. Returns the comma-joined taxids string (or the ``"All"``
    sentinel); the conditional router downstream branches on
    whether the value is ``None``. Always sets
    ``evolution_agents_task`` on the failure path so the schema-
    filtered output keeps a consistent shape across both branches.
    """
    taxids = await target_taxids(state["query"], state.get("kwargs") or {})
    if taxids is None:
        return {"target_taxids": None, "evolution_agents_task": None}
    return {"target_taxids": taxids}


async def submit_evolution_task_node(
    state: EvolutionState,
) -> Dict[str, Any]:
    """Submit the evolution analysis task for the resolved taxids.

    Mirrors the post-extraction body of
    :func:`agents.evolution.agent.evo_test_analysis`: builds the
    goal prompt + data list, materialises the output directory
    when ``batch=False``, and forwards the submission to the
    analyst ``submit``. Re-routes ``get_prompt`` / ``get_data_list``
    / ``submit`` through the ``agent`` module namespace so the
    existing wrapper-level ``monkeypatch.setattr`` calls still
    intercept the dependencies.
    """
    kwargs = state.get("kwargs") or {}
    batch = state.get("batch", False)
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
    if not batch:
        output_dir = evolution_output_dir(user_id, kwargs)
    meta = agent.get_prompt(prompt_file, "user/evolution_agents_meta")
    if DEEP_GENOME_CONFIG.USE_ANALYST_SUBGRAPH:
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
        )
    else:
        evo_task = await agent.submit(
            goal_description=goal_description,
            data_list=data_list,
            user_id=user_id,
            output_dir=output_dir,
            meta=meta,
            **evolution_submit_kwargs(kwargs, enable_auto_select),
        )
    return {"evolution_agents_task": evo_task}


async def _submit_evolution_via_subgraph(
    inputs: _EvolutionSubmitInputs,
    *,
    user_id: Any,
    gene_id: str,
    submit_kwargs: Dict[str, Any],
) -> Dict[str, Any]:
    """Dispatch the evolution analysis task through the analyst subgraph.

    Mirrors ``analyst.submit`` (the legacy free-function path) but
    routes through ``submit_analyst_via_subgraph`` so the analyst
    graph runs as a structured subgraph. Builds a per-call
    ``AnalystAgent`` via :func:`_build_submit_agent` so cache and
    sensitive-config behavior matches the legacy path.

    Args:
        inputs: ``_EvolutionSubmitInputs`` carrying the four narrative
            fields (``goal_description`` / ``data_list`` /
            ``output_dir`` / ``meta``); packed into a NamedTuple so the
            helper stays under pylint's ``max-args`` ceiling without
            silently dropping any field.
        user_id: Resolved user identifier; threaded through the
            cached-agent kwargs to match the legacy ``user_id`` arg
            on ``analyst.submit``.
        gene_id: Gene identifier from the evolution state; used as the
            dispatch ``target_id`` so the analyst thread id includes
            the analysed gene.
        submit_kwargs: The same kwargs ``evolution_submit_kwargs``
            forwards into ``analyst.submit`` on the legacy path; the
            cache labels and ``compute_resource`` derive from them.

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
    request = {
        "analysis_type": "evolution_analysis",
        "target_id": gene_id,
        "output_dir": inputs.output_dir,
        "prompt_parts": (
            inputs.goal_description,
            inputs.meta,
            inputs.data_list,
        ),
        "compute_resource": submit_kwargs.get(
            "compute_resource", DEEP_GENOME_CONFIG.COMPUTE_RESOURCE
        ),
    }
    return await submit_analyst_via_subgraph(
        analyst_agent,
        DEEP_GENOME_CONFIG,
        get_sensitive_config(),
        request,
        is_polling=False,
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
