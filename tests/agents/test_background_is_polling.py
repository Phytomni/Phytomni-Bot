# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Default ``is_polling`` pins for subgraph and Design MCP helpers."""

from inspect import signature

import pytest

from mcp_server_phytomni.agents.design.agent import (
    promoter_design_for_gene,
    protein_structure_for_gene,
)
from mcp_server_phytomni.graphs.analyst_dispatch_adapters import (
    submit_analyst_via_subgraph,
)

pytestmark = pytest.mark.agent


def test_subgraph_and_design_helpers_default_is_polling_false() -> None:
    """Forgotten kwargs must not reintroduce in-graph EI waits."""
    assert (
        signature(submit_analyst_via_subgraph)
        .parameters["is_polling"]
        .default
        is False
    )
    assert (
        signature(protein_structure_for_gene)
        .parameters["is_polling"]
        .default
        is False
    )
    assert (
        signature(promoter_design_for_gene)
        .parameters["is_polling"]
        .default
        is False
    )
