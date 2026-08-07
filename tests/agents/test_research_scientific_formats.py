# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the pure Research scientific-format registry."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.research.scientific_formats import (
    advertised_research_formats,
    classify_scientific_reference,
)

pytestmark = pytest.mark.agent


@pytest.mark.parametrize(
    ("name", "suffix"),
    [
        ("reads.txt.gz", ".txt.gz"),
        ("matrix.tsv.gz", ".tsv.gz"),
        ("counts.mtx.gz", ".mtx.gz"),
        ("bundle.tar.gz", ".tar.gz"),
    ],
)
def test_longest_compound_suffix_wins(name: str, suffix: str) -> None:
    """Compound scientific suffixes retain their semantic base type."""
    result = classify_scientific_reference(name)

    assert result is not None
    assert result.canonical_suffix == suffix


@pytest.mark.parametrize(
    ("reference", "family"),
    [
        ("reads.fastq.gz", "reads"),
        ("assembly.fasta", "sequence"),
        ("record.genbank", "record"),
        ("alignment.bam", "alignment"),
        ("calls.vcf", "variant"),
        ("cohort.bgen", "genotype"),
        ("features.gff3", "annotation"),
        ("tree.newick", "phylogeny"),
        ("matrix.h5ad", "matrix"),
        ("cytometry.fcs", "array_cytometry"),
        ("table.parquet", "tabular"),
        ("metadata.jsonl", "structured"),
        ("spectra.mzml", "proteomics"),
        ("metabolites.mzxml", "metabolomics"),
        ("model.mmcif", "structure"),
        ("terms.obo", "ontology"),
        ("image.ome.tiff", "scientific_image"),
    ],
)
def test_registry_covers_each_scientific_family(
    reference: str,
    family: str,
) -> None:
    """Every design family has a canonical classification."""
    result = classify_scientific_reference(reference)

    assert result is not None
    assert result.family == family


def test_zip_is_one_archive_reference() -> None:
    """Archives are classified without inspecting or expanding members."""
    result = classify_scientific_reference("study.PDF.ZIP")

    assert result is not None
    assert result.canonical_suffix == ".zip"
    assert result.archive is True


def test_unknown_suffix_is_rejected() -> None:
    """An unregistered suffix cannot enter the Research inventory."""
    assert classify_scientific_reference("opaque.payload") is None


def test_advertised_formats_are_sorted_unique_suffix_tokens() -> None:
    """Capability consumers receive stable, lower-case detached tokens."""
    formats = advertised_research_formats()

    assert formats == tuple(sorted(formats))
    assert len(formats) == len(set(formats))
    assert all(format and not format.startswith(".") for format in formats)
    assert {"fastq.gz", "mtx.gz", "tar.gz", "zip"}.issubset(formats)
