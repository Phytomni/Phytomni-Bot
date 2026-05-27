# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Helper-method tests for agents/design/agent.

Pin the compute-resource tier mapping (``protein_design_analysis``
gets ``"medium"``; everything else gets ``"small"``) and the
``_analysis_prompt_parts`` guard that raises ``ValueError`` on an
unknown analysis type before any prompt lookup.
"""

# pylint: disable=protected-access
# Test file exercises the design agent's internal helpers directly
# (``_get_compute_resource``, ``_analysis_prompt_parts``); pylint W0212
# is suppressed at file scope because pytest's "test the smallest unit
# the bug can hide in" convention requires reaching into private
# methods. See ``docs/lint-exemptions.md`` for the rationale.

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from mcp_server_phytomni.agents.analyst.agent import AnalystAgent
from mcp_server_phytomni.agents.design.agent import (
    DigitalDesignAgents,
    DigitalDesignConfig,
)
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent


def _build_agent() -> DigitalDesignAgents:
    """Construct a design agent wired to a stub analyst.

    Uses ``SimpleNamespace`` for the analyst stand-in (same pattern as
    ``_analyst_fakes.py``) so pylint's R0903 too-few-public-methods
    rule does not trip on a single-method stub class.
    """
    analyst_stub = SimpleNamespace(identifier=lambda: "stub-analyst")
    return DigitalDesignAgents(
        digital_design_config=DigitalDesignConfig(),
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, analyst_stub),
    )


def test_get_compute_resource_protein_design_returns_medium() -> None:
    """Protein design is the only documented medium-tier analysis."""
    agent = _build_agent()

    assert agent._get_compute_resource("protein_design_analysis") == "medium"


def test_get_compute_resource_unknown_falls_back_to_small() -> None:
    """Every other analysis type stays on the small tier by default."""
    agent = _build_agent()

    assert agent._get_compute_resource("promoter_design_analysis") == "small"
    assert agent._get_compute_resource("does-not-exist") == "small"


def test_analysis_prompt_parts_rejects_unknown_type() -> None:
    """Unknown analysis types fail loud before any prompt lookup.

    The guard sits ahead of ``get_prompt`` and ``get_data_list``
    so a misconfigured caller never reaches the species metadata
    layer with a typo in the analysis-type slug.
    """
    agent = _build_agent()

    with pytest.raises(ValueError, match="does-not-exist"):
        agent._analysis_prompt_parts(
            analysis_type="does-not-exist",
            species="arabidopsis thaliana",
            gene_id="AT1G01010",
        )
