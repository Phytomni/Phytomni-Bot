# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Formatting helpers for deep genome workflows."""

from dataclasses import dataclass
from typing import Any, Dict

from .func_cache import func_cache

SPECIES_CODE_MAP = {
    "ach": "kiwi (Actinidia chinensis)",
    "aco": "pineapple (Ananas comosus)",
    "aly": "Arabidopsis lyrata",
    "aof": "garden (Asparagus officinalis)",
    "ata": "rough-spike (Aegilops tauschii)",
    "ath": "thale (Arabidopsis thaliana)",
    "atr": "Amborella trichopoda",
    "bdi": "Brachypodium distachyon",
    "bna": "oilseed (Brassica napus)",
    "bol": "Brassica oleracea",
    "bra": "Brassica rapa",
    "bvu": "suger (Beta vulgaris)",
    "can": "pepper (Capsicum annuum)",
    "cav": "Corylus avellana",
    "cbr": "Chara braunii",
    "ccan": "coffee (Coffea canephora)",
    "ccl": "citrus (Citrus clementina)",
    "cla": "watermelon (Citrullus lanatus)",
    "cme": "muskmelon (Cucumis melo)",
    "cqu": "quinoa (Chenopodium quinoa)",
    "cre": "Chlamydomonas reinhardtii",
    "csa": "cucumber (Cucumis sativus)",
    "dca": "carrot (Daucus carota)",
    "dex": "white (Digitaria exilis)",
    "ecu": "weeping (Eragrostis curvula)",
    "egr": "Eucalyptus grandis",
    "esa": "saltwater (Eutrema salsugineum)",
    "ghi": "upland (Gossypium hirsutum)",
    "gma": "soybean (Glycine max)",
    "gra": "cotton (Gossypium raimondii)",
    "han": "sunflower (Helianthus annuus)",
    "hvu": "barley (Hordeum vulgare)",
    "lpe": "Lolium perenne",
    "lsa": "lettuce (Lactuca sativa)",
    "mac": "banana (Musa acuminata)",
    "mes": "cassava (Manihot esculenta)",
    "mpo": "liverwort (Marchantia polymorpha)",
    "mtr": "barrel (Medicago truncatula)",
    "obr": "wild (Oryza brachyantha)",
    "oeu": "common (Olea europaea)",
    "osa": "rice (Oryza sativa)",
    "pha": "Hall's (Panicum hallii)",
    "ppa": "Physcomitrium patens",
    "ppe": "peach (Prunus persica)",
    "psa": "garden (Pisum sativum)",
    "pso": "opium (Papaver somniferum)",
    "ptr": "black (Populus trichocarpa)",
    "pvu": "common (Phaseolus vulgaris)",
    "qlo": "Quercus lobata",
    "rch": "rose (Rosa chinensis)",
    "sbi": "sorghum (Sorghum bicolor)",
    "sce": "rye (Secale cereale)",
    "sit": "foxtail (Setaria italica)",
    "sly": "tomato (Solanum lycopersicum)",
    "smo": "Selaginella moellendorffii",
    "ssp": "sugarcane (Saccharum spontaneum)",
    "stu": "potato (Solanum tuberosum)",
    "svi": "green (Setaria viridis)",
    "tae": "wheat (Triticum aestivum)",
    "tca": "cacao (Theobroma cacao)",
    "tdi": "emmer (Triticum dicoccoides)",
    "tpr": "red (Trifolium pratense)",
    "ttu": "durum (Triticum turgidum)",
    "vvi": "grape (Vitis vinifera)",
    "zma": "maize (Zea mays)",
}

ANNOTATION_TERM_SPECS = (
    ("go", "go_id", "go_name", "go_ids", "go_counts"),
    (
        "interpro",
        "interpro_id",
        "interpro_name",
        "interpro_ids",
        "interpro_counts",
    ),
    ("mapman", "mapman", "mapman_description", "mapman_ids", "mapman_counts"),
)

ENRICHMENT_SUMMARY_SPECS = (
    ("go_ids", "go_counts", "GO"),
    ("interpro_ids", "interpro_counts", "InterPro"),
    ("mapman_ids", "mapman_counts", "MapMan"),
)


@dataclass(frozen=True)
class EnrichmentSummarySpec:
    """Resolved inputs for one enrichment summary line."""

    ids_key: str
    counts_key: str
    label: str
    network_type: str
    top_n: int


def _new_enrichment_maps() -> Dict[str, Dict[str, Any]]:
    """Create mutable annotation id/count maps for one network summary."""
    return {
        "go_ids": {},
        "interpro_ids": {},
        "mapman_ids": {},
        "go_counts": {},
        "interpro_counts": {},
        "mapman_counts": {},
    }


def _network_symbol_string(
    species_gene: tuple,
    species_gene_symbol_dict: dict,
) -> str:
    """Return a printable symbol string for one species/gene tuple."""
    if species_gene in species_gene_symbol_dict:
        return "|".join(species_gene_symbol_dict[species_gene]).replace(
            "\n",
            "|",
        )
    return species_gene[1]


def _append_annotation_terms(
    gene_anno: dict,
    enrichment_maps: Dict[str, Dict[str, Any]],
) -> None:
    """Accumulate annotation term ids, names, and counts."""
    for (
        source_key,
        id_key,
        name_key,
        ids_key,
        counts_key,
    ) in ANNOTATION_TERM_SPECS:
        for term in gene_anno.get(source_key, []):
            term_id = term[id_key]
            enrichment_maps[ids_key][term_id] = term[name_key]
            counts = enrichment_maps[counts_key]
            counts[term_id] = counts.get(term_id, 0) + 1


def _format_network_gene_line(
    species_gene: tuple,
    species_gene_symbol_dict: dict,
    species_gene_anno_dict: dict,
    enrichment_maps: Dict[str, Dict[str, Any]],
) -> str:
    """Format one network gene line and update enrichment counters."""
    symbol_string = _network_symbol_string(
        species_gene,
        species_gene_symbol_dict,
    )
    gene_anno = species_gene_anno_dict.get(species_gene, {})
    description_string = gene_anno.get("description", "")
    _append_annotation_terms(gene_anno, enrichment_maps)
    if not description_string:
        return ""
    return (
        f"{SPECIES_CODE_MAP[species_gene[0]]}: "
        f"{symbol_string}: {description_string}\n"
    )


def _format_enrichment_summary(
    enrichment_maps: Dict[str, Dict[str, Any]],
    spec: EnrichmentSummarySpec,
) -> str:
    """Format one TOP-N enrichment summary line."""
    sorted_ids = sorted(
        enrichment_maps[spec.counts_key].items(),
        key=lambda item: item[1],
        reverse=True,
    )[: spec.top_n]
    all_terms = "; ".join(
        enrichment_maps[spec.ids_key][term_id]
        for term_id, _count in sorted_ids
        if term_id in enrichment_maps[spec.ids_key]
    )
    return (
        f"{spec.network_type} genes TOP {spec.top_n} "
        f"{spec.label} enrichment results: {all_terms}\n"
    )


@func_cache(
    key_params=[
        "gene_network_list",
        "species_gene_symbol_dict",
        "species_gene_anno_dict",
        "network_type",
        "top_n",
    ],
    ttl=3600,
)
def network_to_string(
    gene_network_list: list,
    species_gene_symbol_dict: dict,
    species_gene_anno_dict: dict,
    network_type: str,
    top_n: int = 10,
):
    """Formats gene network information into a string."""
    if not gene_network_list:
        return f"No {network_type} genes"

    enrichment_maps = _new_enrichment_maps()
    network_parts = []
    for species_gene in gene_network_list:
        gene_line = _format_network_gene_line(
            species_gene,
            species_gene_symbol_dict,
            species_gene_anno_dict,
            enrichment_maps,
        )
        if gene_line:
            network_parts.append(gene_line)
    for ids_key, counts_key, label in ENRICHMENT_SUMMARY_SPECS:
        network_parts.append(
            _format_enrichment_summary(
                enrichment_maps,
                EnrichmentSummarySpec(
                    ids_key=ids_key,
                    counts_key=counts_key,
                    label=label,
                    network_type=network_type,
                    top_n=top_n,
                ),
            )
        )
    return "".join(network_parts)
