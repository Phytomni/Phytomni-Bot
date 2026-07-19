# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Route tests for the resolve_gene_id flag on design native runs."""

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
from mcp_server_phytomni.agents.design.resolve_query import (
    DigitalDesignIdCandidate,
    DigitalDesignResolveError,
    DigitalDesignResolveResult,
)

pytestmark = pytest.mark.server


def _resolved(gene_id: str, raw: str) -> DigitalDesignResolveResult:
    """Return a canned design resolver result."""
    return DigitalDesignResolveResult(
        gene_id=gene_id,
        species_code="ath",
        raw_query=raw,
        candidates=[
            DigitalDesignIdCandidate(
                gene_id=gene_id, confidence=1.0, species_code="ath"
            ),
        ],
    )


DESIGN_CASE = NativeResolverCase(
    spec=ResolverCaseSpec(
        slug="design",
        tool_name=server.PhytomniAgents.DIGITAL_DESIGN_AGENT.value,
        answer="design submission",
        resolver_attribute="resolve_design_user_query",
        result_factory=_resolved,
        error_factory=DigitalDesignResolveError,
    ),
    expected=ResolverCaseExpected(
        species_code="ath",
        resolved_gene_id="AT1G01010",
        resolved_raw_query="design AT1G01010 promoter",
        failure_message="no valid candidate",
        blank_message="species_code could not be determined from query: bar",
    ),
    arguments=ResolverCaseArguments(
        resolved_arguments={
            "obs_file_list": [],
            "user_query": "design AT1G01010 promoter",
            "resolve_gene_id": True,
        },
        passthrough_arguments={
            "species_code": "ath",
            "gene_id": "AT1G01010",
            "obs_file_list": [],
            "resolve_gene_id": False,
        },
        missing_arguments={
            "species_code": "ath",
            "obs_file_list": [],
            "resolve_gene_id": True,
        },
        failure_arguments={
            "obs_file_list": [],
            "user_query": "ambiguous query",
            "resolve_gene_id": True,
        },
        blank_arguments={
            "obs_file_list": [],
            "user_query": "bar",
            "resolve_gene_id": True,
        },
    ),
)


install_native_resolver_tests(globals(), DESIGN_CASE)
