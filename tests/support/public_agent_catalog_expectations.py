# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Independent expected values shared by public Agent contract tests."""

from __future__ import annotations

from collections.abc import Iterable
from itertools import chain


def _words(value: str) -> tuple[str, ...]:
    """Keep long immutable expectation sequences compact and reviewable."""
    return tuple(value.split())


EXPECTED_TRACE_OPERATIONS = {
    "chat": _words("model.generate tool.chat"),
    "knowledge": _words("knowledge.search model.generate tool.knowledge"),
    "data": _words("data.query tool.data"),
    "analyst": _words(
        "analyst.prepare_analysis analyst.run_workflow "
        "analyst.collect_outputs artifact.package remote.analysis "
        "remote.reconcile tool.analyst"
    ),
    "review": _words(
        "model.generate review.citation_check review.draft_dimension "
        "review.final_synthesis review.retrieve_dimension tool.review"
    ),
    "brief_gene": _words("model.generate tool.brief_gene"),
    "deep_genome": _words(
        "deep_genome.prepare_plan deep_genome.gather_context "
        "deep_genome.run_analysis_branches deep_genome.experiment_protocol "
        "deep_genome.synthesize_results artifact.package "
        "deep_genome.workflow model.generate remote.analysis "
        "remote.reconcile tool.deep_genome"
    ),
    "research": _words(
        "research.decompose_objectives research.dispatch_work "
        "research.collect_evidence research.synthesize_results "
        "research.package_outputs artifact.package model.generate "
        "remote.analysis remote.reconcile tool.research"
    ),
    "design": _words(
        "design.validate_target design.run_branches "
        "design.consolidate_candidates design.package_outputs "
        "artifact.package remote.analysis remote.reconcile tool.design"
    ),
    "network": _words(
        "artifact.package gene_network.infer_network "
        "gene_network.prepare_inputs gene_network.rank_regulators "
        "gene_network.synthesize_results gene_network.validate_target "
        "remote.analysis remote.reconcile tool.network"
    ),
}

EXPECTED_TRACE_TARGET_AGENT_SLUGS = tuple(
    slug
    for slug, operations in EXPECTED_TRACE_OPERATIONS.items()
    if "remote.analysis" in operations
)

EXPECTED_EXECUTION_DRIVERS = _words(
    "local_graph remote_task remote_fanout resumable_graph hybrid"
)
EXPECTED_EXECUTION_TARGET_KINDS = _words(
    "event artifact report todo preview download trace"
)
EXPECTED_EXECUTION_RUNTIME_FEATURES: dict[str, int | bool] = {
    key: True
    for key in _words(
        "stable_execution_identity async_message_admission content_resume "
        "actions cancellation"
    )
}
EXPECTED_EXECUTION_RUNTIME_FEATURES.update(
    execution_runtime_major=1,
    execution_journal_major=2,
)


def _flatten(values: Iterable[tuple[str, ...]]) -> frozenset[str]:
    """Return the distinct operations present across expected Agent traces."""
    return frozenset(chain.from_iterable(values))


EXPECTED_PRESENTER_OPERATIONS = _flatten(
    EXPECTED_TRACE_OPERATIONS.values()
) | {"remote.submit"}
