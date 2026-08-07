# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Canonical suffix classification for Research dataset references."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "ScientificFormat",
    "advertised_research_formats",
    "classify_scientific_reference",
]


@dataclass(frozen=True, slots=True)
class ScientificFormat:
    """One immutable scientific reference suffix and its safe metadata hint."""

    canonical_suffix: str
    family: str
    media_hint: str
    archive: bool = False


def _formats(
    suffixes: tuple[str, ...],
    family: str,
    media_hint: str,
    *,
    archive: bool = False,
) -> tuple[ScientificFormat, ...]:
    """Build immutable entries for one scientific family."""
    return tuple(
        ScientificFormat(suffix, family, media_hint, archive)
        for suffix in suffixes
    )


_SCIENTIFIC_FORMATS = (
    _formats(
        (".fasta", ".fa", ".fna", ".ffn", ".faa", ".frn"),
        "sequence",
        "text/x-fasta",
    )
    + _formats(
        (
            ".fastq",
            ".fq",
            ".qual",
            ".sff",
            ".fast5",
            ".pod5",
            ".bcl",
            ".cbcl",
        ),
        "reads",
        "application/octet-stream",
    )
    + _formats(
        (
            ".fasta.gz",
            ".fa.gz",
            ".fna.gz",
            ".ffn.gz",
            ".faa.gz",
            ".frn.gz",
        ),
        "sequence",
        "application/gzip",
    )
    + _formats(
        (
            ".fastq.gz",
            ".fq.gz",
            ".qual.gz",
            ".sff.gz",
            ".fast5.gz",
            ".pod5.gz",
            ".bcl.gz",
            ".cbcl.gz",
        ),
        "reads",
        "application/gzip",
    )
    + _formats(
        (".gb", ".gbk", ".genbank", ".embl"),
        "record",
        "text/plain",
    )
    + _formats((".sam", ".bam", ".cram"), "alignment", "text/plain")
    + _formats((".vcf", ".bcf", ".gvcf"), "variant", "text/plain")
    + _formats(
        (
            ".ped",
            ".map",
            ".bim",
            ".fam",
            ".pgen",
            ".pvar",
            ".psam",
            ".bgen",
            ".gen",
            ".haps",
            ".sample",
        ),
        "genotype",
        "text/plain",
    )
    + _formats(
        (
            ".bed",
            ".bedgraph",
            ".broadpeak",
            ".narrowpeak",
            ".gappedpeak",
            ".gff",
            ".gff3",
            ".gtf",
            ".wig",
            ".bw",
            ".bigwig",
            ".bb",
            ".bigbed",
            ".maf",
            ".psl",
            ".chain",
            ".2bit",
        ),
        "annotation",
        "text/plain",
    )
    + _formats(
        (
            ".aln",
            ".clustal",
            ".phy",
            ".phylip",
            ".nex",
            ".nexus",
            ".nwk",
            ".newick",
            ".tree",
            ".sto",
            ".stockholm",
        ),
        "phylogeny",
        "text/plain",
    )
    + _formats(
        (".h5", ".hdf5", ".h5ad", ".loom", ".mtx", ".cool", ".mcool", ".hic"),
        "matrix",
        "application/octet-stream",
    )
    + _formats(
        (".mtx.gz", ".tsv.gz", ".txt.gz"),
        "matrix",
        "application/gzip",
    )
    + _formats(
        (".cel", ".idat", ".fcs", ".biom", ".qza", ".qzv", ".sra", ".ab1"),
        "array_cytometry",
        "application/octet-stream",
    )
    + _formats(
        (".rds", ".rdata", ".mat", ".npy", ".npz"),
        "serialized",
        "application/octet-stream",
    )
    + _formats(
        (".csv", ".tsv", ".xls", ".xlsx", ".parquet", ".feather", ".arrow"),
        "tabular",
        "application/octet-stream",
    )
    + _formats(
        (".json", ".jsonl", ".ndjson", ".xml", ".yaml", ".yml"),
        "structured",
        "application/json",
    )
    + _formats(
        (".mzml", ".mzid", ".pepxml", ".protxml"),
        "proteomics",
        "application/octet-stream",
    )
    + _formats(
        (".mzxml", ".mgf", ".raw"),
        "metabolomics",
        "application/octet-stream",
    )
    + _formats(
        (".pdb", ".cif", ".mmcif", ".sdf", ".mol", ".mol2"),
        "structure",
        "chemical/x-pdb",
    )
    + _formats((".obo", ".owl", ".rdf"), "ontology", "text/plain")
    + _formats(
        (
            ".ome.tiff",
            ".ome.tif",
            ".tiff",
            ".tif",
            ".czi",
            ".nd2",
            ".lif",
            ".svs",
            ".dcm",
        ),
        "scientific_image",
        "application/octet-stream",
    )
    + _formats(
        (
            ".zip",
            ".zipx",
            ".tar.gz",
            ".tar",
            ".tgz",
            ".tbz",
            ".tbz2",
            ".txz",
            ".tlz",
            ".tzst",
            ".gz",
            ".bgz",
            ".bgzf",
            ".bgzip",
            ".bz",
            ".bz2",
            ".xz",
            ".lz",
            ".lzma",
            ".lz4",
            ".lzo",
            ".br",
            ".z",
            ".zst",
            ".7z",
            ".rar",
            ".cab",
            ".ace",
            ".arj",
        ),
        "archive",
        "application/octet-stream",
        archive=True,
    )
)

# Matching must retain compound semantic suffixes before their short archive
# tails.  Tuple construction prevents callers from mutating the registry.
_ORDERED_SCIENTIFIC_FORMATS = tuple(
    sorted(
        _SCIENTIFIC_FORMATS,
        key=lambda format_descriptor: len(format_descriptor.canonical_suffix),
        reverse=True,
    )
)


def classify_scientific_reference(reference: str) -> ScientificFormat | None:
    """Match the longest configured compound suffix without rewriting input."""
    comparison = reference.lower()
    return next(
        (
            format_descriptor
            for format_descriptor in _ORDERED_SCIENTIFIC_FORMATS
            if comparison.endswith(format_descriptor.canonical_suffix)
        ),
        None,
    )


def advertised_research_formats() -> tuple[str, ...]:
    """Return sorted unique canonical suffixes without leading dots."""
    return tuple(
        sorted(
            {
                format_descriptor.canonical_suffix.removeprefix(".")
                for format_descriptor in _ORDERED_SCIENTIFIC_FORMATS
            }
        )
    )
