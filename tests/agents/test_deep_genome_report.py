# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for DeepGenome report-synthesis pure helpers.

Pins the prompt-feeding helpers downstream LLM nodes depend on:
_state_gene_string at module scope, and _part12_profile /
_experiment_prompt / _summary_source_content on DeepGenomeReportMixin.
A test-only subclass exposes the protected helpers under public names
so the assertions stay inside the class hierarchy.
"""

from __future__ import annotations

from typing import Any, Dict, cast

import pytest

from mcp_server_phytomni.agents.deep_genome import report as report_module
from mcp_server_phytomni.agents.deep_genome.agent import DeepGenomeState
from mcp_server_phytomni.agents.deep_genome.report import (
    DeepGenomeReportMixin,
    _state_gene_string,
)
from mcp_server_phytomni.config.defaults import DeepGenomeConfig

pytestmark = pytest.mark.unit


class _ReportProbe(DeepGenomeReportMixin):
    """Test-only mixin host exposing protected helpers via public names.

    The mixin's ``_part12_profile`` / ``_experiment_prompt`` /
    ``_summary_source_content`` helpers are protected because production
    code only calls them from sibling node methods on the same class.
    Tests exercise them through this subclass so the calls stay inside
    the class hierarchy (no pylint W0212 protected-access escape).
    """

    def __init__(self) -> None:
        """Wire the single config attribute the protected helpers read."""
        self.deep_genome_config = DeepGenomeConfig()

    def part12_profile(self, state: DeepGenomeState) -> str:
        """Public proxy for ``_part12_profile``."""
        return self._part12_profile(state)

    def experiment_prompt(self, state: DeepGenomeState, content: str) -> str:
        """Public proxy for ``_experiment_prompt``."""
        return self._experiment_prompt(state, content)

    def summary_source_content(self, state: DeepGenomeState) -> str:
        """Public proxy for ``_summary_source_content``."""
        return self._summary_source_content(state)


def _state(**overrides: Any) -> DeepGenomeState:
    """Build a DeepGenomeState-shaped mapping with overridable keys."""
    base: Dict[str, Any] = {
        "gene_id": "Os01g0177400",
        "species_code": "osa",
        "gene_annotation": {"gene_string": "Os01g0177400 (display)"},
        "part1_report": "part1-body",
        "synthesize_report": "part2-body",
        "introduction_report": "intro",
        "discussion_report": "disc",
        "experiment_report": "exp",
        "protocol_report": "proto",
        "part12_combined": "combined",
        "config_params": {"use_analyst_agent": True},
    }
    base.update(overrides)
    return cast(DeepGenomeState, base)


def test_state_gene_string_returns_annotation_value() -> None:
    """The helper returns the nested gene_string when present."""
    assert _state_gene_string(_state()) == "Os01g0177400 (display)"


def test_state_gene_string_defaults_to_empty_on_missing_annotation() -> None:
    """A missing gene_annotation block yields an empty string, not KeyError.

    Pins the .get(..., {}).get(..., "") chain that lets early-stage state
    flow through report nodes before annotation has populated.
    """
    assert _state_gene_string(_state(gene_annotation={})) == ""


def test_part12_profile_composes_part1_and_synthesize() -> None:
    """``_part12_profile`` concatenates the part1 + part2 report bodies."""
    assert _ReportProbe().part12_profile(_state()) == (
        "## Gene Profiles\n\npart1-body\n\npart2-body\n\n"
    )


def test_part12_profile_coerces_none_synthesize_to_empty() -> None:
    """A None synthesize_report still produces a well-formed profile.

    The dispatch barrier returns {} when analysis branches have not
    completed, which leaves synthesize_report empty/None. The report
    must keep generating instead of templating a literal "None" string.
    """
    state = _state(synthesize_report=None)

    assert _ReportProbe().part12_profile(state) == (
        "## Gene Profiles\n\npart1-body\n\n\n\n"
    )


def test_experiment_prompt_threads_state_and_content_to_get_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_experiment_prompt`` forwards gene/species/content into get_prompt.

    Captures the args dict get_prompt sees so a future template rename
    or a dropped key surfaces as an assertion failure rather than a
    silent prompt-shape change.
    """
    captured: Dict[str, Any] = {}

    def fake_prompt(_file: str, prompt_path: str, args: Dict[str, Any]) -> str:
        captured["path"] = prompt_path
        captured["args"] = args
        return "PROMPT"

    monkeypatch.setattr(report_module, "get_prompt", fake_prompt)

    result = _ReportProbe().experiment_prompt(_state(), content="findings")

    assert result == "PROMPT"
    assert captured["path"] == "user/gene_function_experiment"
    assert captured["args"] == {
        "gene_string": "Os01g0177400 (display)",
        "species_string": "rice (Oryza sativa)",
        "content": "findings",
    }


def test_summary_source_content_uses_analyst_layout_by_default() -> None:
    """When use_analyst_agent stays True, all six sections render in order."""
    assert _ReportProbe().summary_source_content(_state()) == (
        "# Deep Genome Analysis of Os01g0177400\n\n"
        "intro\n\ncombined\n\n"
        "## Recommended experiments\n\n"
        "proto\n\nexp\n\n"
        "## Discussion\n\ndisc\n\n"
    )


def test_summary_source_content_skips_analyst_sections_when_disabled() -> None:
    """``use_analyst_agent=False`` uses the short part12-only layout.

    Pins the deep_genome non-analyst pathway: the gene-function report
    still has to include part12 plus discussion, just without the
    analyst-driven introduction / protocol / experiment sections.
    """
    state = _state(config_params={"use_analyst_agent": False})

    assert (
        _ReportProbe().summary_source_content(state)
        == "combined\n\n## Discussion\n\ndisc\n\n"
    )


def test_summary_source_content_defaults_to_analyst_layout_when_unset() -> (
    None
):
    """Missing ``config_params`` is treated as use_analyst_agent=True.

    Belt-and-braces guard for callers that build state without seeding
    config_params yet — the analyst-aware layout is the safer default
    because it includes more sections.
    """
    result = _ReportProbe().summary_source_content(_state(config_params={}))

    assert "## Recommended experiments" in result
    assert "## Discussion" in result
