# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pure DeepGenome routing and analysis-request preparation helpers.

This module owns deterministic branch selection, dynamic ``Send`` payload
construction, prompt/data-list lookup, and output-feature selection. It does
not submit remote work, poll task status, download results, or mutate graph
state. The dispatch mixin keeps thin compatibility methods around these
helpers so existing graph builders and test seams remain stable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, NamedTuple

from langgraph.types import Send

from ...common.prompts import get_prompt
from ..shared.analysis_storage import ANALYSIS_DATA_LIST_MAP, get_data_list
from .coordinator import DeepGenomeWorkflowError, workflow_outcome_for_state

PromptLoader = Callable[
    [str, str, Mapping[str, Any] | None],
    str,
]
DataLoader = Callable[[str, str, str], dict[str, Any]]
RoutingState = Mapping[str, Any]


# Generic types use worker nodes; evolution/design use mounted subgraphs.
# Keep this order aligned with tests/unit/test_deep_genome_work_items.py.
_GENERIC_ANALYSIS_NODE_TYPE_SOURCE = (
    "gene_expression_tissues|gene_expression_cultivars|"
    "gene_expression_treatments|gene_expression_genotypes|"
    "single_cell_analysis|promoter_analysis|smep_analysis|smoc_analysis|"
    "protein_structure_analysis"
)
GENERIC_ANALYSIS_NODE_TYPES: tuple[str, ...] = tuple(
    _GENERIC_ANALYSIS_NODE_TYPE_SOURCE.split("|")
)

ANALYSIS_GOAL_TEMPLATE_MAP = {
    "haplotypes_analysis": "user/haplotypes_analysis",
    "fst_analysis": "user/fst_analysis",
    "enrichment_analysis": "user/enrichment_analysis",
    "gene_expression_tissues": "user/gene_expression_analysis/tissue",
    "gene_expression_cultivars": "user/gene_expression_analysis/cultivar",
    "gene_expression_genotypes": "user/gene_expression_analysis/genotype",
    "gene_expression_treatments": "user/gene_expression_analysis/treatment",
    "single_cell_analysis": "user/single_cell_analysis",
    "smep_analysis": "user/smep_analysis",
    "smoc_analysis": "user/smoc_analysis",
}

ANALYSIS_META_TEMPLATE_MAP = {
    "haplotypes_analysis": "user/haplotypes_analysis_meta",
    "fst_analysis": "user/fst_analysis_meta",
    "enrichment_analysis": "user/enrichment_analysis_meta",
    "gene_expression_tissues": "user/gene_expression_analysis_meta",
    "gene_expression_cultivars": "user/gene_expression_analysis_meta",
    "gene_expression_genotypes": "user/gene_expression_analysis_meta",
    "gene_expression_treatments": "user/gene_expression_analysis_meta",
    "single_cell_analysis": "user/single_cell_analysis_meta",
    "smep_analysis": "user/smep_analysis_meta",
    "smoc_analysis": "user/smoc_analysis_meta",
}

ANALYSIS_TARGET_FILE_FEATURE_MAP = {
    "evolution_analysis": [".md", ".png", ".summary", ".legend"],
    "haplotypes_analysis": [".png", ".summary", ".legend"],
    "fst_analysis": [".png"],
    "enrichment_analysis": [".png", ".summary", ".legend"],
    "protein_structure_analysis": ["sample_0.cif", ".summary", ".legend"],
    "promoter_analysis": ["motif_all_logo.png", ".summary", ".legend"],
    "gene_expression_tissues": [".png", ".summary", ".legend"],
    "gene_expression_cultivars": [".png", ".summary", ".legend"],
    "gene_expression_genotypes": [".png", ".summary", ".legend"],
    "gene_expression_treatments": [".png", ".summary", ".legend"],
    "single_cell_analysis": [".png", ".summary", ".legend"],
    "smep_analysis": [".png", ".summary", ".legend"],
    "smoc_analysis": [".png", ".summary", ".legend"],
}
DEFAULT_TARGET_FILE_FEATURE = [".png", ".summary", ".legend"]


class AnalysisDispatchContext(NamedTuple):
    """Resolved request context for one deep analysis task."""

    analysis_type: str
    species_code: str
    gene_id: str
    output_dir: str


def build_analysis_context(
    analysis_type: str,
    species_code: str,
    gene_id: str,
    output_dir: str,
) -> AnalysisDispatchContext:
    """Construct the immutable context passed to remote adapters."""
    return AnalysisDispatchContext(
        analysis_type=analysis_type,
        species_code=species_code,
        gene_id=gene_id,
        output_dir=output_dir,
    )


def analyst_node_name(analysis_type: str) -> str:
    """Map a generic analysis type to its deterministic worker node name."""
    return analysis_type.removesuffix("_analysis") + "_node"


def node_for_analysis_type(analysis_type: Any) -> str:
    """Return the mounted or generic node for one logical task type."""
    if analysis_type == "evolution_analysis":
        return "evolution_node"
    if analysis_type == "digital_design":
        return "design_node"
    return analyst_node_name(str(analysis_type))


def route_start(_state: RoutingState) -> list[str]:
    """Return the required BriefGene mount and launch barrier."""
    return ["brief_gene_node"]


def route_after_brief_gene(state: RoutingState) -> list[str] | str:
    """Gate optional task preparation on successful BriefGene completion."""
    use_analyst = state.get("config_params", {}).get("use_analyst_agent", True)
    if use_analyst:
        return ["prepare_tasks_node", "experiment_node"]
    return "experiment_node"


def route_synthesize_barrier(state: RoutingState) -> str:
    """Route only when every concrete task is terminal and usable."""
    if state.get("skip_synthesize"):
        if not state.get("synthesize_report"):
            raise DeepGenomeWorkflowError("final synthesis unavailable")
        return "experiment_node"
    outcome = workflow_outcome_for_state(state)
    if not outcome.all_terminal:
        return "synthesize_node"
    if not outcome.may_synthesize:
        raise DeepGenomeWorkflowError("no usable analysis result")
    if not state.get("synthesize_report"):
        raise DeepGenomeWorkflowError("final synthesis unavailable")
    return "experiment_node"


def route_experiment_barrier(state: RoutingState) -> str:
    """Route only after the required profile and synthesis are available."""
    use_analyst = state.get("config_params", {}).get("use_analyst_agent", True)
    if not use_analyst:
        return (
            "discussion_node"
            if state.get("report_triggered")
            else "experiment_node"
        )
    if not state.get("preamble") or not state.get("synthesize_report"):
        return "experiment_node"
    if state.get("report_triggered"):
        return "protocol_node"
    return "experiment_node"


def build_analyst_sends(state: RoutingState) -> list[Send]:
    """Build ordered Send payloads without mutating the parent state."""
    sleep_time = state.get("task_submit_sleep", 10)
    sends: list[Send] = []
    for index, task in enumerate(state.get("analysis_tasks", [])):
        analysis_type = task.get("analysis_type")
        send_payload: dict[str, Any] = {
            "task_index": index,
            "task_submit_sleep": index * sleep_time,
            "locale": state.get("locale"),
            **task,
        }
        for identity_key in ("task_id", "run_id", "owner", "output_dir"):
            send_payload[identity_key] = state.get(identity_key)
        work_item = next(
            (
                item
                for item in state.get("work_items", [])
                if item.get("section_key") == analysis_type
                or item.get("work_item_key") == analysis_type
            ),
            None,
        )
        if work_item is not None:
            send_payload.update(
                {
                    "work_item_key": work_item.get("work_item_key"),
                    "display_order": work_item.get("display_order"),
                }
            )
        sends.append(
            Send(
                node_for_analysis_type(analysis_type),
                send_payload,
            )
        )
    return sends


def build_analysis_prompt_parts(
    context: AnalysisDispatchContext,
    *,
    prompt_file: str,
    data_file: str,
    prompt_loader: PromptLoader = get_prompt,
    data_loader: DataLoader = get_data_list,
) -> tuple[str, dict[str, Any], str, str]:
    """Resolve goal, data-list, meta prompt, and compute-resource parts."""
    analysis_type = context.analysis_type
    goal_path = ANALYSIS_GOAL_TEMPLATE_MAP.get(analysis_type)
    meta_path = ANALYSIS_META_TEMPLATE_MAP.get(analysis_type)
    if not goal_path or not meta_path:
        raise ValueError(f"Unknown analysis type: {analysis_type}")

    goal_description = prompt_loader(
        prompt_file,
        goal_path,
        {"gene_id": context.gene_id},
    )
    meta = prompt_loader(prompt_file, meta_path, None)
    data_json_path = ANALYSIS_DATA_LIST_MAP.get(analysis_type, "")
    if "/" in data_json_path:
        data_json_path, sub_title = data_json_path.split("/", maxsplit=1)
    else:
        sub_title = None
    data_list = data_loader(data_file, data_json_path, context.species_code)
    if sub_title:
        data_list = data_list[sub_title]
    return goal_description, data_list, meta, "small"


def target_file_features(analysis_type: str) -> list[str]:
    """Return a copy of the stable output filter for one analysis type."""
    return list(
        ANALYSIS_TARGET_FILE_FEATURE_MAP.get(
            analysis_type, DEFAULT_TARGET_FILE_FEATURE
        )
    )


__all__ = [
    "ANALYSIS_DATA_LIST_MAP",
    "ANALYSIS_GOAL_TEMPLATE_MAP",
    "ANALYSIS_META_TEMPLATE_MAP",
    "ANALYSIS_TARGET_FILE_FEATURE_MAP",
    "AnalysisDispatchContext",
    "DEFAULT_TARGET_FILE_FEATURE",
    "GENERIC_ANALYSIS_NODE_TYPES",
    "build_analysis_context",
    "build_analysis_prompt_parts",
    "build_analyst_sends",
    "node_for_analysis_type",
    "route_after_brief_gene",
    "route_experiment_barrier",
    "route_start",
    "route_synthesize_barrier",
    "target_file_features",
]
