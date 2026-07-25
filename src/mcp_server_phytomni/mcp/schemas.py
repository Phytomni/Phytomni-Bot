# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""MCP public tool schemas for Phytomni agents.

This module defines request models for each public MCP tool and
`PhytomniAgents`, the enum that stores stable tool names and descriptions.
These schemas are the public JSON-schema surface for MCP clients.
"""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from ..runtime.locale import SupportedLocale

_LOCALE_DESCRIPTION = (
    "Optional natural-language response locale. Allowed values: en-US, "
    "zh-CN."
)


class ChatAgent(BaseModel):
    """Input parameters for general ChatAgent Q&A and file summarization.

    Attributes:
        user_query: User question or instruction for the chat model.
        obs_file_list: Optional OBS paths for uploaded context files.
    """

    user_query: Annotated[
        str,
        Field(
            description="Full user question or instruction for a general "
            "plant-science answer, explanation, or uploaded-file summary. "
            "Preserve the user's original intent and include any requested "
            "output format.",
        ),
    ]
    obs_file_list: Annotated[
        list[str],
        Field(
            description="OBS paths for optional user-uploaded context files "
            "to summarize or use while answering. Use complete OBS paths "
            "exactly as provided, pass [] when no files are supplied, and do "
            "not invent file paths. Supported file types: PPTX, DOCX, XLSX, "
            "XLS, PDF, Outlook.",
            json_schema_extra={
                "example": [
                    "/obs/phytomni/path/to/document.pdf",
                    "/obs/phytomni/path/to/document.docx",
                ],
                "x-java-default": "new ArrayList<>()",
                "x-csharp-default": "new List<string>()",
            },
        ),
    ]
    locale: Annotated[
        SupportedLocale | None, Field(description=_LOCALE_DESCRIPTION)
    ] = None


class KnowledgeAgent(BaseModel):
    """Input parameters for retrieval-backed KnowledgeAgent answers.

    Attributes:
        user_query: Focused plant-science question requiring evidence.
        obs_file_list: Optional uploaded documents to combine with retrieval.
    """

    user_query: Annotated[
        str,
        Field(
            description="Focused plant-science question to answer with "
            "retrieved literature, patent, or book evidence. Include key "
            "species, genes, traits, methods, or constraints from the user."
        ),
    ]
    obs_file_list: Annotated[
        list[str],
        Field(
            description="OBS paths for optional user-uploaded documents to "
            "combine with retrieved evidence. Use complete OBS paths exactly "
            "as provided, pass [] when no files are supplied, and do not "
            "invent file paths. Supported file types: PPTX, DOCX, XLSX, XLS, "
            "PDF, Outlook.",
            json_schema_extra={
                "example": [
                    "/obs/phytomni/path/to/document.pdf",
                    "/obs/phytomni/path/to/document.docx",
                ],
                "x-java-default": "new ArrayList<>()",
                "x-csharp-default": "new List<string>()",
            },
        ),
    ]
    locale: Annotated[
        SupportedLocale | None, Field(description=_LOCALE_DESCRIPTION)
    ] = None


class DataAgent(BaseModel):
    """Input parameters for natural-language database querying.

    Attributes:
        user_query: Natural-language database question to convert to SQL.
    """

    user_query: Annotated[
        str,
        Field(
            description="Natural-language database question that needs "
            "structured SQL-backed results, such as counts, tables, "
            "statistics, rankings, or filtered records. Keep it as a user "
            "question rather than raw SQL, and include species, trait, gene, "
            "or filter constraints when available.",
        ),
    ]
    locale: Annotated[
        SupportedLocale | None, Field(description=_LOCALE_DESCRIPTION)
    ] = None


class AnalystAgent(BaseModel):
    """Input parameters for bioinformatics workflow planning and submission.

    Attributes:
        goal_description: Bioinformatics analysis objective.
        data_list: OBS dataset paths mapped to role descriptions.
        obs_file_list: Optional supporting documents for workflow planning.
    """

    goal_description: Annotated[
        str,
        Field(
            description=(
                "Bioinformatics analysis objective to plan and submit. "
                "Describe the biological question, desired analysis type, "
                "target organism, comparisons, and expected outputs; put "
                "dataset paths in data_list instead of this text."
            ),
        ),
    ]
    data_list: Annotated[
        dict[str, str],
        Field(
            description="Dictionary of input analysis datasets. Each key must "
            "be a complete OBS path to a data file, and each value must "
            "describe that file's role, format, sequencing or omics type, "
            "organism, sample group or condition, quality notes, and intended "
            "analysis use. Use {} only when the workflow truly has no input "
            "datasets.",
            json_schema_extra={
                "example": {
                    "/obs/phytomni/path/to/reference.fasta": "Reference "
                    "genome assembly for target organism.",
                    "/obs/phytomni/path/to/sequence.fastq": "Illumina WGS "
                    "data, 150bp read, for SNP/InDel detection.",
                },
                "x-java-default": "new HashMap<>()",
                "x-csharp-default": "new Dictionary<string, string>()",
            },
        ),
    ]
    obs_file_list: Annotated[
        list[str],
        Field(
            description="OBS paths for optional supporting documents, such as "
            "protocols, papers, or requirement files, that provide context "
            "for workflow planning. Do not duplicate raw analysis datasets "
            "already listed in data_list. Pass [] when no files are supplied.",
            json_schema_extra={
                "example": [
                    "/obs/phytomni/path/to/document.pdf",
                    "/obs/phytomni/path/to/document.docx",
                ],
                "x-java-default": "new ArrayList<>()",
                "x-csharp-default": "new List<string>()",
            },
        ),
    ]
    locale: Annotated[
        SupportedLocale | None, Field(description=_LOCALE_DESCRIPTION)
    ] = None


class DeepGenomeAgent(BaseModel):
    """Input parameters for comprehensive deep genome gene analysis.

    Attributes:
        species_code: Supported three-letter species code.
        gene_id: Single target gene identifier in the selected species.
    """

    species_code: Annotated[
        str,
        Field(
            description=(
                """Three-letter species_code for the target gene. Fill this
                with one supported code, not a Latin name or common name.
                Supported species_code values are the keys of this dict: {
                'ach': 'kiwi (Actinidia chinensis)',
                'aco': 'pineapple (Ananas comosus)',
                'aly': 'Arabidopsis lyrata',
                'aof': 'garden (Asparagus officinalis)',
                'ata': 'rough-spike (Aegilops tauschii)',
                'ath': 'thale (Arabidopsis thaliana)',
                'atr': 'Amborella trichopoda',
                'bdi': 'Brachypodium distachyon',
                'bna': 'oilseed (Brassica napus)',
                'bol': 'Brassica oleracea',
                'bra': 'Brassica rapa',
                'bvu': 'suger (Beta vulgaris)',
                'can': 'pepper (Capsicum annuum)',
                'cav': 'Corylus avellana',
                'cbr': 'Chara braunii',
                'ccan': 'coffee (Coffea canephora)',
                'ccl': 'citrus (Citrus clementina)',
                'cla': 'watermelon (Citrullus lanatus)',
                'cme': 'muskmelon (Cucumis melo)',
                'cqu': 'quinoa (Chenopodium quinoa)',
                'cre': 'Chlamydomonas reinhardtii',
                'csa': 'cucumber (Cucumis sativus)',
                'dca': 'carrot (Daucus carota)',
                'dex': 'white (Digitaria exilis)',
                'ecu': 'weeping (Eragrostis curvula)',
                'egr': 'Eucalyptus grandis',
                'esa': 'saltwater (Eutrema salsugineum)',
                'ghi': 'upland (Gossypium hirsutum)',
                'gma': 'soybean (Glycine max)',
                'gra': 'cotton (Gossypium raimondii)',
                'han': 'sunflower (Helianthus annuus)',
                'hvu': 'barley (Hordeum vulgare)',
                'lpe': 'Lolium perenne',
                'lsa': 'lettuce (Lactuca sativa)',
                'mac': 'banana (Musa acuminata)',
                'mes': 'cassava (Manihot esculenta)',
                'mpo': 'liverwort (Marchantia polymorpha)',
                'mtr': 'barrel (Medicago truncatula)',
                'obr': 'wild (Oryza brachyantha)',
                'oeu': 'common (Olea europaea)',
                'osa': 'rice (Oryza sativa)',
                'pha': "Hall's (Panicum hallii)",
                'ppa': 'Physcomitrium patens',
                'ppe': 'peach (Prunus persica)',
                'psa': 'garden (Pisum sativum)',
                'pso': 'opium (Papaver somniferum)',
                'ptr': 'black (Populus trichocarpa)',
                'pvu': 'common (Phaseolus vulgaris)',
                'qlo': 'Quercus lobata',
                'rch': 'rose (Rosa chinensis)',
                'sbi': 'sorghum (Sorghum bicolor)',
                'sce': 'rye (Secale cereale)',
                'sit': 'foxtail (Setaria italica)',
                'sly': 'tomato (Solanum lycopersicum)',
                'smo': 'Selaginella moellendorffii',
                'ssp': 'sugarcane (Saccharum spontaneum)',
                'stu': 'potato (Solanum tuberosum)',
                'svi': 'green (Setaria viridis)',
                'tae': 'wheat (Triticum aestivum)',
                'tca': 'cacao (Theobroma cacao)',
                'tdi': 'emmer (Triticum dicoccoides)',
                'tpr': 'red (Trifolium pratense)',
                'ttu': 'durum (Triticum turgidum)',
                'vvi': 'grape (Vitis vinifera)',
                'zma': 'maize (Zea mays)'}"""
            ),
        ),
    ]
    gene_id: Annotated[
        str,
        Field(
            description="Single target gene identifier in the selected "
            "species_code, such as a locus ID or accepted database gene "
            "ID. Do not include multiple genes, trait IDs, or free-form "
            "questions.",
        ),
    ]
    locale: Annotated[
        SupportedLocale | None, Field(description=_LOCALE_DESCRIPTION)
    ] = None


class ReviewAgent(BaseModel):
    """Input parameters for broad literature review and report generation.

    Attributes:
        user_query: Broad review topic and desired report scope.
        obs_file_list: Optional source documents to consider in the review.
    """

    user_query: Annotated[
        str,
        Field(
            description="Broad review or report topic. Include the crop or "
            "species, biological process, trait, method, scope, comparison, "
            "and desired report angle when provided by the user."
        ),
    ]
    obs_file_list: Annotated[
        list[str],
        Field(
            description="OBS paths for optional user-uploaded papers, notes, "
            "or source documents to consider during review generation. Use "
            "complete OBS paths exactly as provided, pass [] when no files "
            "are supplied, and do not invent file paths. Supported file "
            "types: PPTX, DOCX, XLSX, XLS, PDF, Outlook.",
            json_schema_extra={
                "example": [
                    "/obs/phytomni/path/to/document.pdf",
                    "/obs/phytomni/path/to/document.docx",
                ],
                "x-java-default": "new ArrayList<>()",
                "x-csharp-default": "new List<string>()",
            },
        ),
    ]
    locale: Annotated[
        SupportedLocale | None, Field(description=_LOCALE_DESCRIPTION)
    ] = None


class BriefGeneAgent(BaseModel):
    """Input parameters for a concise single-gene function report.

    Attributes:
        user_query: One plant gene or transcript identifier to summarize.
    """

    user_query: Annotated[
        str,
        Field(
            description="One plant gene ID or transcript ID to summarize. "
            "Prefer the exact identifier supplied by the user and do not use "
            "gene symbols, species names, multiple genes, or a full research "
            "question as this value.",
        ),
    ]
    locale: Annotated[
        SupportedLocale | None, Field(description=_LOCALE_DESCRIPTION)
    ] = None


class InSilicoResearchAgent(BaseModel):
    """Input parameters for paper-driven in silico research task submission.

    Attributes:
        user_query: Paper text or study context to decompose into tasks.
        data_list: OBS dataset paths mapped to role descriptions.
        obs_file_list: Optional uploaded papers or context files.
        interop_mode: External delegation policy. Defaults to local-only.
        interop_targets: Operator-registered target ids eligible for
            delegation.
    """

    user_query: Annotated[
        str,
        Field(
            description="Scientific paper text, abstract, methods/results "
            "context, or study description to decompose into computational "
            "research objectives. If the paper is uploaded as a file, include "
            "the user's instruction or brief context here."
        ),
    ]
    data_list: Annotated[
        dict[str, str],
        Field(
            description="Dictionary of datasets available for the extracted "
            "in silico tasks. Each key must be a complete OBS path to a data "
            "file, and each value must describe the file's biological source, "
            "format, assay or sequencing type, sample condition, quality "
            "notes, and intended use in reproducibility analysis. Use {} "
            "only when no datasets are supplied.",
            json_schema_extra={
                "example": {
                    "/obs/phytomni/path/to/reference.fasta": "Reference "
                    "genome assembly for target organism.",
                    "/obs/phytomni/path/to/sequence.fastq": "Illumina WGS "
                    "data, 150bp read, for SNP/InDel detection.",
                },
                "x-java-default": "new HashMap<>()",
                "x-csharp-default": "new Dictionary<string, string>()",
            },
        ),
    ]
    obs_file_list: Annotated[
        list[str],
        Field(
            description="OBS paths for optional uploaded paper or context "
            "files used to extract research objectives. Use complete OBS "
            "paths exactly as provided, pass [] when no files are supplied, "
            "and do not invent file paths. Supported file types: PPTX, "
            "DOCX, XLSX, XLS, PDF, Outlook.",
            json_schema_extra={
                "example": [
                    "/obs/phytomni/path/to/document.pdf",
                    "/obs/phytomni/path/to/document.docx",
                ],
                "x-java-default": "new ArrayList<>()",
                "x-csharp-default": "new List<string>()",
            },
        ),
    ]
    interop_mode: Literal["off", "auto", "required"] = Field(
        default="off",
        description=(
            "External capability delegation policy: 'off' keeps execution "
            "local and never discovers or invokes a peer; 'auto' may use "
            "an eligible registered MCP/A2A target and falls back locally "
            "when it cannot obtain evidence; 'required' fails the request "
            "unless an eligible target returns external evidence."
        ),
    )
    interop_targets: list[str] = Field(
        default_factory=list,
        description=(
            "Operator-registered external target ids eligible for this "
            "request. Never provide a URL, command, credential, or token."
        ),
    )
    locale: Annotated[
        SupportedLocale | None, Field(description=_LOCALE_DESCRIPTION)
    ] = None


class DigitalDesignAgent(BaseModel):
    """Input parameters for protein and promoter design task submission.

    Attributes:
        species_code: Three-letter species code for design analysis.
        gene_id: Single target gene identifier.
        obs_file_list: Optional uploaded context files.
        interop_mode: External delegation policy. Defaults to local-only.
        interop_targets: Operator-registered target ids eligible for
            delegation.
    """

    species_code: Annotated[
        str,
        Field(
            description=(
                """Three-letter species_code for the target gene. Fill this
                with one supported code, not a Latin name or common name.
                Supported species_code values are the keys of this dict: {
                'ach': 'kiwi (Actinidia chinensis)',
                'aco': 'pineapple (Ananas comosus)',
                'aly': 'Arabidopsis lyrata',
                'aof': 'garden (Asparagus officinalis)',
                'ata': 'rough-spike (Aegilops tauschii)',
                'ath': 'thale (Arabidopsis thaliana)',
                'atr': 'Amborella trichopoda',
                'bdi': 'Brachypodium distachyon',
                'bna': 'oilseed (Brassica napus)',
                'bol': 'Brassica oleracea',
                'bra': 'Brassica rapa',
                'bvu': 'suger (Beta vulgaris)',
                'can': 'pepper (Capsicum annuum)',
                'cav': 'Corylus avellana',
                'cbr': 'Chara braunii',
                'ccan': 'coffee (Coffea canephora)',
                'ccl': 'citrus (Citrus clementina)',
                'cla': 'watermelon (Citrullus lanatus)',
                'cme': 'muskmelon (Cucumis melo)',
                'cqu': 'quinoa (Chenopodium quinoa)',
                'cre': 'Chlamydomonas reinhardtii',
                'csa': 'cucumber (Cucumis sativus)',
                'dca': 'carrot (Daucus carota)',
                'dex': 'white (Digitaria exilis)',
                'ecu': 'weeping (Eragrostis curvula)',
                'egr': 'Eucalyptus grandis',
                'esa': 'saltwater (Eutrema salsugineum)',
                'ghi': 'upland (Gossypium hirsutum)',
                'gma': 'soybean (Glycine max)',
                'gra': 'cotton (Gossypium raimondii)',
                'han': 'sunflower (Helianthus annuus)',
                'hvu': 'barley (Hordeum vulgare)',
                'lpe': 'Lolium perenne',
                'lsa': 'lettuce (Lactuca sativa)',
                'mac': 'banana (Musa acuminata)',
                'mes': 'cassava (Manihot esculenta)',
                'mpo': 'liverwort (Marchantia polymorpha)',
                'mtr': 'barrel (Medicago truncatula)',
                'obr': 'wild (Oryza brachyantha)',
                'oeu': 'common (Olea europaea)',
                'osa': 'rice (Oryza sativa)',
                'pha': "Hall's (Panicum hallii)",
                'ppa': 'Physcomitrium patens',
                'ppe': 'peach (Prunus persica)',
                'psa': 'garden (Pisum sativum)',
                'pso': 'opium (Papaver somniferum)',
                'ptr': 'black (Populus trichocarpa)',
                'pvu': 'common (Phaseolus vulgaris)',
                'qlo': 'Quercus lobata',
                'rch': 'rose (Rosa chinensis)',
                'sbi': 'sorghum (Sorghum bicolor)',
                'sce': 'rye (Secale cereale)',
                'sit': 'foxtail (Setaria italica)',
                'sly': 'tomato (Solanum lycopersicum)',
                'smo': 'Selaginella moellendorffii',
                'ssp': 'sugarcane (Saccharum spontaneum)',
                'stu': 'potato (Solanum tuberosum)',
                'svi': 'green (Setaria viridis)',
                'tae': 'wheat (Triticum aestivum)',
                'tca': 'cacao (Theobroma cacao)',
                'tdi': 'emmer (Triticum dicoccoides)',
                'tpr': 'red (Trifolium pratense)',
                'ttu': 'durum (Triticum turgidum)',
                'vvi': 'grape (Vitis vinifera)',
                'zma': 'maize (Zea mays)'}"""
            ),
        ),
    ]
    gene_id: Annotated[
        str,
        Field(
            description="Single target gene identifier for protein and "
            "promoter design in the selected species_code. Do not include "
            "multiple genes, trait IDs, or a general function-analysis "
            "question.",
        ),
    ]
    obs_file_list: Annotated[
        list[str],
        Field(
            description="OBS paths for optional uploaded context files. Use "
            "complete OBS paths exactly as provided, pass [] when no files "
            "are supplied, and do not invent file paths. Supported file "
            "types: PPTX, DOCX, XLSX, XLS, PDF, Outlook.",
            json_schema_extra={
                "example": [
                    "/obs/phytomni/path/to/document.pdf",
                    "/obs/phytomni/path/to/document.docx",
                ],
                "x-java-default": "new ArrayList<>()",
                "x-csharp-default": "new List<string>()",
            },
        ),
    ]
    interop_mode: Literal["off", "auto", "required"] = Field(
        default="off",
        description=(
            "External capability delegation policy: 'off' keeps execution "
            "local and never discovers or invokes a peer; 'auto' may use "
            "an eligible registered MCP/A2A target and falls back locally "
            "when it cannot obtain evidence; 'required' fails the request "
            "unless an eligible target returns external evidence."
        ),
    )
    interop_targets: list[str] = Field(
        default_factory=list,
        description=(
            "Operator-registered external target ids eligible for this "
            "request. Never provide a URL, command, credential, or token."
        ),
    )
    locale: Annotated[
        SupportedLocale | None, Field(description=_LOCALE_DESCRIPTION)
    ] = None


class GeneNetworkAgent(BaseModel):
    """Input parameters for trait-associated gene network task submission.

    Attributes:
        species_code: Three-letter species code for network analysis.
        to_id: Trait Ontology identifier for the target phenotype.
        obs_file_list: Optional uploaded context files.
    """

    species_code: Annotated[
        str,
        Field(
            description=("""Three-letter species_code for trait-associated gene
                network analysis. Fill this with one supported code, not a
                Latin name or common name. Supported species_code values
                are the keys of this dict: {
                'ach': 'kiwi (Actinidia chinensis)',
                'aco': 'pineapple (Ananas comosus)',
                'aly': 'Arabidopsis lyrata',
                'aof': 'garden (Asparagus officinalis)',
                'ata': 'rough-spike (Aegilops tauschii)',
                'ath': 'thale (Arabidopsis thaliana)',
                'atr': 'Amborella trichopoda',
                'bdi': 'Brachypodium distachyon',
                'bna': 'oilseed (Brassica napus)',
                'bol': 'Brassica oleracea',
                'bra': 'Brassica rapa',
                'bvu': 'suger (Beta vulgaris)',
                'can': 'pepper (Capsicum annuum)',
                'cav': 'Corylus avellana',
                'cbr': 'Chara braunii',
                'ccan': 'coffee (Coffea canephora)',
                'ccl': 'citrus (Citrus clementina)',
                'cla': 'watermelon (Citrullus lanatus)',
                'cme': 'muskmelon (Cucumis melo)',
                'cqu': 'quinoa (Chenopodium quinoa)',
                'cre': 'Chlamydomonas reinhardtii',
                'csa': 'cucumber (Cucumis sativus)',
                'dca': 'carrot (Daucus carota)',
                'dex': 'white (Digitaria exilis)',
                'ecu': 'weeping (Eragrostis curvula)',
                'egr': 'Eucalyptus grandis',
                'esa': 'saltwater (Eutrema salsugineum)',
                'ghi': 'upland (Gossypium hirsutum)',
                'gma': 'soybean (Glycine max)',
                'gra': 'cotton (Gossypium raimondii)',
                'han': 'sunflower (Helianthus annuus)',
                'hvu': 'barley (Hordeum vulgare)',
                'lpe': 'Lolium perenne',
                'lsa': 'lettuce (Lactuca sativa)',
                'mac': 'banana (Musa acuminata)',
                'mes': 'cassava (Manihot esculenta)',
                'mpo': 'liverwort (Marchantia polymorpha)',
                'mtr': 'barrel (Medicago truncatula)',
                'obr': 'wild (Oryza brachyantha)',
                'oeu': 'common (Olea europaea)',
                'osa': 'rice (Oryza sativa)',
                'pha': "Hall's (Panicum hallii)",
                'ppa': 'Physcomitrium patens',
                'ppe': 'peach (Prunus persica)',
                'psa': 'garden (Pisum sativum)',
                'pso': 'opium (Papaver somniferum)',
                'ptr': 'black (Populus trichocarpa)',
                'pvu': 'common (Phaseolus vulgaris)',
                'qlo': 'Quercus lobata',
                'rch': 'rose (Rosa chinensis)',
                'sbi': 'sorghum (Sorghum bicolor)',
                'sce': 'rye (Secale cereale)',
                'sit': 'foxtail (Setaria italica)',
                'sly': 'tomato (Solanum lycopersicum)',
                'smo': 'Selaginella moellendorffii',
                'ssp': 'sugarcane (Saccharum spontaneum)',
                'stu': 'potato (Solanum tuberosum)',
                'svi': 'green (Setaria viridis)',
                'tae': 'wheat (Triticum aestivum)',
                'tca': 'cacao (Theobroma cacao)',
                'tdi': 'emmer (Triticum dicoccoides)',
                'tpr': 'red (Trifolium pratense)',
                'ttu': 'durum (Triticum turgidum)',
                'vvi': 'grape (Vitis vinifera)',
                'zma': 'maize (Zea mays)'}"""),
        ),
    ]
    to_id: Annotated[
        str,
        Field(
            description="Trait Ontology identifier for the target phenotype, "
            "formatted like 'TO:0000207'. Fill this with a TO ID, not a gene "
            "ID or free-text trait name. If the user did not provide a "
            "reliable TO ID, ask for clarification instead of guessing.",
        ),
    ]
    obs_file_list: Annotated[
        list[str],
        Field(
            description="OBS paths for optional uploaded context files. Use "
            "complete OBS paths exactly as provided, pass [] when no files "
            "are supplied, and do not invent file paths. Supported file "
            "types: PPTX, DOCX, XLSX, XLS, PDF, Outlook.",
            json_schema_extra={
                "example": [
                    "/obs/phytomni/path/to/document.pdf",
                    "/obs/phytomni/path/to/document.docx",
                ],
                "x-java-default": "new ArrayList<>()",
                "x-csharp-default": "new List<string>()",
            },
        ),
    ]
    locale: Annotated[
        SupportedLocale | None, Field(description=_LOCALE_DESCRIPTION)
    ] = None


class GetTaskStatus(BaseModel):
    """Input parameters for a non-blocking task-status lookup.

    Attributes:
        task_id: The task id returned by a prior async submission.
    """

    task_id: Annotated[
        str,
        Field(
            description="The task id returned by a prior asynchronous "
            "submission (AnalystAgent, DeepGenomeAgent, DigitalDesignAgent, "
            "GeneNetworkAgent, or InSilicoResearchAgent). This performs a "
            "single non-blocking status lookup — submit first, then poll "
            "with this tool; it never waits for completion.",
        ),
    ]


class PhytomniAgents(StrEnum):
    """Enumeration of specialized AI agents for plant science research support.

    Defines available agent types with domain-specific capabilities for
    different aspects of botanical studies and computational biology workflows.

    Members:
        CHAT_AGENT: Core language model interface for fundamental Q&A.
            Usage: Basic conceptual queries, single-domain problem solving.
            Limitations: Avoid for multi-factor agricultural optimizations.
        KNOWLEDGE_AGENT: Evidence-based literature synthesis system.
            Usage: Cross-referenced answers from curated scientific sources.
        DATA_AGENT: Structured data query interface.
            Usage: Precise numerical/statistical retrieval from databases.
        ANALYST_AGENT: Genomic workflow orchestration system.
            Usage: Automated execution of bioinformatics pipelines.

    Descriptions provide guidance on appropriate application scenarios and
    technical constraints for each agent type. All agents implement
    standardized JSON schema for parameter validation.
    """

    CHAT_AGENT = "ChatAgent"
    CHAT_AGENT_DESCRIPTION = (
        "Use for general plant-science Q&A, conceptual explanations, and "
        "summaries of user-uploaded files using the base LLM. Do not use "
        "when the request needs literature retrieval, SQL/database results, "
        "or bioinformatics workflow execution."
    )
    KNOWLEDGE_AGENT = "KnowledgeAgent"
    KNOWLEDGE_AGENT_DESCRIPTION = (
        "Use for evidence-backed answers that require retrieval from plant "
        "science literature, patents, or books, optionally combined with "
        "uploaded files. Do not use for broad review writing or structured "
        "database queries."
    )
    DATA_AGENT = "DataAgent"
    DATA_AGENT_DESCRIPTION = (
        "Use for natural-language questions that need structured botanical "
        "database or SQL results, including counts, tables, statistics, and "
        "other precise database-backed facts."
    )
    ANALYST_AGENT = "AnalystAgent"
    ANALYST_AGENT_DESCRIPTION = (
        "Use for bioinformatics workflow planning and task submission from a "
        "research goal plus input datasets, such as sequence, omics, or other "
        "computational biology analyses that should run on the analysis "
        "platform."
    )
    REVIEW_AGENT = "ReviewAgent"
    REVIEW_AGENT_DESCRIPTION = (
        "Use for broad literature review or report generation that requires "
        "multi-dimension planning, retrieval, drafting, critique, revision, "
        "and summary. Prefer KnowledgeAgent for targeted evidence-backed Q&A."
    )
    BRIEF_GENE_AGENT = "BriefGeneAgent"
    BRIEF_GENE_AGENT_DESCRIPTION = (
        "Use for a concise function report about one plant gene, transcript, "
        "or accepted database gene ID, using BI annotations when available "
        "and retrieved literature as fallback or supporting evidence. Do not "
        "route gene-symbol-only inputs here unless the user provides an ID."
    )
    DEEP_GENOME_AGENT = "DeepGenomeAgent"
    DEEP_GENOME_AGENT_DESCRIPTION = (
        "Use for comprehensive gene-function analysis from species_code plus "
        "gene_id, including annotations, ortholog/paralog/interaction "
        "context, deep computational analyses, experiment recommendations, "
        "protocols, and final report sections."
    )
    IN_SILICO_RESEARCH_AGENT = "InSilicoResearchAgent"
    IN_SILICO_RESEARCH_AGENT_DESCRIPTION = (
        "Use to extract computational research objectives from a paper or "
        "research context, then submit reproducibility-style analysis tasks "
        "against the provided datasets."
    )
    DIGITAL_DESIGN_AGENT = "DigitalDesignAgent"
    DIGITAL_DESIGN_AGENT_DESCRIPTION = (
        "Use to submit both protein design and promoter design analyses for a "
        "specific species plus gene_id. This is for computational design task "
        "execution, not general gene-function explanation."
    )
    GENE_NETWORK_AGENT = "GeneNetworkAgent"
    GENE_NETWORK_AGENT_DESCRIPTION = (
        "Use to submit trait-associated gene network analysis for a species "
        "plus Trait Ontology ID. Do not use for generic free-form gene "
        "interaction Q&A or broad network explanation."
    )
    GET_TASK_STATUS = "GetTaskStatus"
    GET_TASK_STATUS_DESCRIPTION = (
        "Use to check a previously submitted task's status without "
        "blocking. Pass the task_id returned by an async submission "
        "(Analyst/DeepGenome/DigitalDesign/GeneNetwork/InSilicoResearch); "
        "it returns the recorded status and output_dir merged with one "
        "live platform status check. Submit first, then poll with this."
    )


# (tool name, routing description, request model) for every dispatchable
# agent, in the same order the MCP ``list_tools`` surface emits them. The
# MCP ``list_tools`` handler and the in-process expert router both derive
# their tool lists from this single tuple so the two surfaces stay
# byte-equivalent and a new agent cannot be exposed on one but not the
# other.
AGENT_TOOL_DEFINITIONS: tuple[
    tuple[PhytomniAgents, PhytomniAgents, type[BaseModel]], ...
] = (
    (
        PhytomniAgents.CHAT_AGENT,
        PhytomniAgents.CHAT_AGENT_DESCRIPTION,
        ChatAgent,
    ),
    (
        PhytomniAgents.KNOWLEDGE_AGENT,
        PhytomniAgents.KNOWLEDGE_AGENT_DESCRIPTION,
        KnowledgeAgent,
    ),
    (
        PhytomniAgents.DATA_AGENT,
        PhytomniAgents.DATA_AGENT_DESCRIPTION,
        DataAgent,
    ),
    (
        PhytomniAgents.ANALYST_AGENT,
        PhytomniAgents.ANALYST_AGENT_DESCRIPTION,
        AnalystAgent,
    ),
    (
        PhytomniAgents.REVIEW_AGENT,
        PhytomniAgents.REVIEW_AGENT_DESCRIPTION,
        ReviewAgent,
    ),
    (
        PhytomniAgents.BRIEF_GENE_AGENT,
        PhytomniAgents.BRIEF_GENE_AGENT_DESCRIPTION,
        BriefGeneAgent,
    ),
    (
        PhytomniAgents.DEEP_GENOME_AGENT,
        PhytomniAgents.DEEP_GENOME_AGENT_DESCRIPTION,
        DeepGenomeAgent,
    ),
    (
        PhytomniAgents.IN_SILICO_RESEARCH_AGENT,
        PhytomniAgents.IN_SILICO_RESEARCH_AGENT_DESCRIPTION,
        InSilicoResearchAgent,
    ),
    (
        PhytomniAgents.DIGITAL_DESIGN_AGENT,
        PhytomniAgents.DIGITAL_DESIGN_AGENT_DESCRIPTION,
        DigitalDesignAgent,
    ),
    (
        PhytomniAgents.GENE_NETWORK_AGENT,
        PhytomniAgents.GENE_NETWORK_AGENT_DESCRIPTION,
        GeneNetworkAgent,
    ),
)


def agent_openai_tool_specs() -> list[dict[str, Any]]:
    """Return the dispatchable agents as OpenAI function-tool dicts.

    Builds one ``{"type": "function", "function": {...}}`` entry per
    agent in ``AGENT_TOOL_DEFINITIONS`` (GetTaskStatus excluded), shaped
    to match ``mcp_client_phytomni.client.PhytomniMcpClient.openai_tools``
    so an in-process router selects tools identically to the stdio client.

    Returns:
        OpenAI Chat Completions ``tools`` list for autonomous routing.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": name.value,
                "description": description.value,
                "parameters": model.model_json_schema(),
            },
        }
        for name, description, model in AGENT_TOOL_DEFINITIONS
    ]
