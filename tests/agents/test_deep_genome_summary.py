# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for DeepGenome analyst result summary loaders.

Covers the §8.2 protein-design loader that maps the digital-design
``psap_scores.png`` + ``summary_all.py`` output into the report's
``protein_path`` / ``protein_summary`` / ``protein_legend`` keys.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_server_phytomni.agents.deep_genome.summary import build_sub_summary

pytestmark = pytest.mark.agent


def test_protein_design_loader_populates_section_keys(
    tmp_path: Path,
) -> None:
    """build_sub_summary for protein_design fills the §8.2 protein keys."""
    results = tmp_path / "g1"
    results.mkdir()
    (results / "psap_scores.png").write_bytes(b"x")
    (results / "g1_protein_design.summary").write_text(
        "design summary Figure 1", encoding="utf-8"
    )
    (results / "g1_protein_design.legend").write_text(
        "design legend Figure 1", encoding="utf-8"
    )

    out = build_sub_summary(
        analysis_type="protein_design_analysis",
        gene_id="g1",
        deepgenome_out=str(tmp_path),
        data={"gene_name": "g1"},
        figure_index=1,
        results_dir=str(results),
    )

    assert out.data["protein_path"].endswith("psap_scores.png")
    assert "design summary" in out.data["protein_summary"]
    assert out.data["protein_legend"]
