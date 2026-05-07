# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for DeepGenome analyst result summary helpers."""

import pytest

from mcp_server_phytomni.deep_genome_summary import build_sub_summary

pytestmark = pytest.mark.unit


def _gene_dir(tmp_path):
    """Create and return a DeepGenome output directory for one gene."""
    out_dir = tmp_path / "deep_out"
    gene_dir = out_dir / "GeneA"
    gene_dir.mkdir(parents=True)
    return out_dir, gene_dir


def test_build_sub_summary_loads_expression_outputs(tmp_path):
    """Verify expression summaries load paths and replace figure labels."""
    out_dir, gene_dir = _gene_dir(tmp_path)
    (gene_dir / "GeneA_tissues.png").write_text("", encoding="utf-8")
    (gene_dir / "GeneA_tissues.summary").write_text(
        "Figure 1 shows tissue expression.",
        encoding="utf-8",
    )
    (gene_dir / "GeneA_tissues.legend").write_text(
        "Figure 1. Tissue legend.",
        encoding="utf-8",
    )

    result = build_sub_summary(
        analysis_type="gene_expression_tissues",
        gene_id="GeneA",
        deepgenome_out=str(out_dir),
        data={"gene_name": "GeneA"},
        figure_index=3,
    )

    assert result.figure_index == 4
    assert result.data["tissue_path"] == "GeneA/GeneA_tissues.png"
    assert result.data["tissue_summary"] == (
        "Figure 3 shows tissue expression."
    )
    assert result.data["tissue_legend"] == "Figure 3. Tissue legend."


def test_build_sub_summary_reads_explicit_results_dir(tmp_path):
    """Verify result summaries can read from an explicit result directory."""
    out_dir, gene_dir = _gene_dir(tmp_path)
    obsfs_dir = tmp_path / "obsfs" / "GeneA-results"
    obsfs_dir.mkdir(parents=True)
    (obsfs_dir / "GeneA_tissues.png").write_text("", encoding="utf-8")
    (obsfs_dir / "GeneA_tissues.summary").write_text(
        "Figure 1 shows mounted tissue expression.",
        encoding="utf-8",
    )
    (obsfs_dir / "GeneA_tissues.legend").write_text(
        "Figure 1. Mounted tissue legend.",
        encoding="utf-8",
    )

    result = build_sub_summary(
        analysis_type="gene_expression_tissues",
        gene_id="GeneA",
        deepgenome_out=str(out_dir),
        data={"gene_name": "GeneA"},
        figure_index=7,
        results_dir=str(obsfs_dir),
    )

    assert result.figure_index == 8
    assert result.data["tissue_path"] == "GeneA/GeneA_tissues.png"
    assert result.data["tissue_summary"] == (
        "Figure 7 shows mounted tissue expression."
    )
    assert result.data["tissue_legend"] == ("Figure 7. Mounted tissue legend.")
    assert not (gene_dir / "GeneA_tissues.summary").exists()


def test_build_sub_summary_keeps_domain_text_unrenumbered(tmp_path):
    """Verify evolution domain text preserves original figure labels."""
    out_dir, gene_dir = _gene_dir(tmp_path)
    (gene_dir / "GeneA_tree.png").write_text("", encoding="utf-8")
    (gene_dir / "GeneA_tree.summary").write_text(
        "Figure 1 shows a tree.",
        encoding="utf-8",
    )
    (gene_dir / "GeneA_tree.legend").write_text(
        "Figure 1. Tree legend.",
        encoding="utf-8",
    )
    (gene_dir / "GeneA_domain.md").write_text("domain table", encoding="utf-8")
    (gene_dir / "GeneA_domain.summary").write_text(
        "Figure 1 domain summary",
        encoding="utf-8",
    )
    (gene_dir / "GeneA_domain.legend").write_text(
        "Figure 1 domain legend",
        encoding="utf-8",
    )

    result = build_sub_summary(
        analysis_type="evolution_analysis",
        gene_id="GeneA",
        deepgenome_out=str(out_dir),
        data={"gene_name": "GeneA"},
        figure_index=2,
    )

    assert result.figure_index == 3
    assert result.data["tree_summary"] == "Figure 2 shows a tree."
    assert result.data["tree_legend"] == "Figure 2. Tree legend."
    assert result.data["domain_table"] == "domain table"
    assert result.data["domain_summary"] == "Figure 1 domain summary"
    assert result.data["domain_legend"] == "Figure 1 domain legend"


def test_build_sub_summary_preserves_smep_empty_default(tmp_path):
    """Verify SMEP missing files keep the historical empty summary default."""
    out_dir, _ = _gene_dir(tmp_path)

    result = build_sub_summary(
        analysis_type="smep_analysis",
        gene_id="GeneA",
        deepgenome_out=str(out_dir),
        data={"gene_name": "GeneA"},
        figure_index=1,
    )

    assert result.figure_index == 1
    assert result.data["smep_path"] == ""
    assert result.data["smep_summary"] == ""
    assert result.data["smep_legend"] == ""


def test_build_sub_summary_loads_protein_structure_blocks(tmp_path):
    """Verify protein structures include CIF references and renumbered text."""
    out_dir, gene_dir = _gene_dir(tmp_path)
    (gene_dir / "GeneA_seed_101_sample_0.cif").write_text(
        "structure",
        encoding="utf-8",
    )
    (gene_dir / "GeneA_seed_101_sample_0.legend").write_text(
        "Table 1 and Figure 1 describe the structure.",
        encoding="utf-8",
    )
    (gene_dir / "GeneA_seed_101_sample_0.summary").write_text(
        "Figure 1 summary.",
        encoding="utf-8",
    )

    result = build_sub_summary(
        analysis_type="protein_structure_analysis",
        gene_id="GeneA",
        deepgenome_out=str(out_dir),
        data={"gene_name": "GeneA"},
        figure_index=5,
    )

    assert result.figure_index == 6
    assert (
        "![3D Structure](GeneA/GeneA_seed_101_sample_0.cif)"
        in result.data["protein_structures"]
    )
    assert (
        "Figure 5 and Figure 5 describe the structure."
        in result.data["protein_structures"]
    )
    assert "Figure 5 summary." in result.data["protein_structures"]
