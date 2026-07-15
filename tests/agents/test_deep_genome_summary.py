# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for DeepGenome analyst result summary loaders.

Covers the §8.2 protein-design loader that maps the digital-design
``psap_scores.png`` + ``summary_all.py`` output into the report's
``protein_path`` / ``protein_summary`` / ``protein_legend`` keys.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mcp_server_phytomni.agents.deep_genome.summary import (
    UnusableAnalysisResult,
    build_design_work_item_summary,
    build_sub_summary,
)

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


def test_promoter_design_summary_reads_sorted_nonblank_summaries(
    tmp_path: Path,
) -> None:
    """Join nonblank promoter summaries in filename order."""
    (tmp_path / "z.summary").write_text("zeta", encoding="utf-8")
    (tmp_path / "a.summary").write_text(" ", encoding="utf-8")
    (tmp_path / "b.summary").write_text("alpha", encoding="utf-8")

    summary = build_design_work_item_summary("promoter_design", tmp_path)

    assert summary.startswith("## Promoter Design")
    assert summary.index("alpha") < summary.index("zeta")
    assert "a.summary" not in summary


def test_promoter_design_summary_falls_back_to_stable_artifact_markdown(
    tmp_path: Path,
) -> None:
    """List promoter artifacts when no summary text is available."""
    (tmp_path / "motif_all_logo.png").write_bytes(b"png")
    (tmp_path / "motifs.legend").write_text("motifs", encoding="utf-8")
    (tmp_path / "z.json").write_text("{}", encoding="utf-8")

    summary = build_design_work_item_summary("promoter_design", tmp_path)

    assert summary.startswith("## Promoter Design")
    assert "motif_all_logo.png" in summary
    assert "motifs.legend" in summary
    assert "z.json" in summary
    assert summary.strip()


def test_promoter_design_summary_rejects_empty_results(
    tmp_path: Path,
) -> None:
    """Reject a promoter result containing neither text nor artifacts."""
    (tmp_path / "empty.summary").write_text("\n", encoding="utf-8")

    with pytest.raises(UnusableAnalysisResult):
        build_design_work_item_summary("promoter_design", tmp_path)


def test_display_order_keeps_concurrent_summary_numbering_stable(
    tmp_path: Path,
) -> None:
    """Use stable work order rather than completion order for figures."""
    results = tmp_path / "results"
    results.mkdir()
    (results / "gene_tissues.png").write_bytes(b"png")
    (results / "gene_tissues.summary").write_text(
        "Figure 1 tissue", encoding="utf-8"
    )
    (results / "gene_tissues.legend").write_text(
        "Figure 1 legend", encoding="utf-8"
    )

    def build_once(_run: int) -> str:
        built = build_sub_summary(
            "gene_expression_tissues",
            "gene",
            str(tmp_path),
            {"gene_name": "gene"},
            99,
            results_dir=str(results),
            display_order=4,
        )
        return built.data["tissue_summary"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(build_once, (1, 2))

    assert first == second == "Figure 5 tissue"
