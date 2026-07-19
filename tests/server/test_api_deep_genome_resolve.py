# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Route tests for the resolve_gene_id flag on DeepGenome native runs."""

from __future__ import annotations

import pytest
from tests.support.resolver_fakes import (
    NativeResolverCase,
    ResolverCaseArguments,
    ResolverCaseExpected,
    ResolverCaseSpec,
    install_native_resolver_tests,
)

from mcp_server_phytomni import server
from mcp_server_phytomni.agents.deep_genome.resolve_query import (
    DeepGenomeIdCandidate,
    DeepGenomeResolveError,
    DeepGenomeResolveResult,
)

pytestmark = pytest.mark.server


def _resolved(gene_id: str, raw: str) -> DeepGenomeResolveResult:
    """Return a canned deep_genome resolver result."""
    return DeepGenomeResolveResult(
        gene_id=gene_id,
        species_code="osa",
        raw_query=raw,
        candidates=[
            DeepGenomeIdCandidate(
                gene_id=gene_id, confidence=1.0, species_code="osa"
            )
        ],
    )


DEEP_GENOME_CASE = NativeResolverCase(
    spec=ResolverCaseSpec(
        slug="deep_genome",
        tool_name=server.PhytomniAgents.DEEP_GENOME_AGENT.value,
        answer="deep genome submission",
        resolver_attribute="resolve_deep_genome_user_query",
        result_factory=_resolved,
        error_factory=DeepGenomeResolveError,
    ),
    expected=ResolverCaseExpected(
        species_code="osa",
        resolved_gene_id="Os01g0177400",
        resolved_raw_query="tell me about CAB1 in rice",
        failure_message="no valid candidate",
        blank_message="species_code could not be determined from query: foo",
    ),
    arguments=ResolverCaseArguments(
        resolved_arguments={
            "user_query": "tell me about CAB1 in rice",
            "resolve_gene_id": True,
        },
        passthrough_arguments={
            "species_code": "osa",
            "gene_id": "Os01g0177400",
            "resolve_gene_id": False,
        },
        missing_arguments={"species_code": "osa", "resolve_gene_id": True},
        failure_arguments={
            "user_query": "ambiguous query",
            "resolve_gene_id": True,
        },
        blank_arguments={"user_query": "foo", "resolve_gene_id": True},
    ),
)


install_native_resolver_tests(globals(), DEEP_GENOME_CASE)
