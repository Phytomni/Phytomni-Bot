#!/usr/bin/env python3
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Regenerate canonical demo_data fixtures for Phytomni-Bot.

This script is the single source of truth for everything committed under
``demo_data/``. Re-running it must produce byte-identical output so the
local ``validate_local.sh`` idempotency gate stays green; every emitted
artifact therefore uses deterministic timestamps and sorted keys.

Outputs:
    * ``payloads/<tool>.json`` -- one payload per public MCP tool.
    * ``docs/plant_science_brief.md`` -- short reference brief.
    * ``docs/plant_science_brief.pdf`` -- deterministic PDF render of the
      brief (``reportlab`` with ``invariant=1``).
    * ``docs/sample_metadata.xlsx`` -- five-row metadata workbook with
      normalized ZIP timestamps for byte-equality across runs.
    * ``sequences/arabidopsis_sample.fasta`` -- five short curated
      Arabidopsis sequences.
    * ``manifest.json`` and ``README.md`` -- index for humans and tests.

Install the optional extras before running::

    uv pip install -e ".[demo]"
    python demo_data/scripts/generate_demo_data.py
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from openpyxl import Workbook
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen.canvas import Canvas

DEMO_ROOT = Path(__file__).resolve().parent.parent
DEMO_OBS_PREFIX = "/obs/phytomni/demo"
FIXED_TIMESTAMP = datetime(2026, 1, 1, tzinfo=UTC)
FIXED_ZIP_DATE_TIME = (2026, 1, 1, 0, 0, 0)
FIXED_W3CDTF = "2026-01-01T00:00:00Z"
FOOTPRINT_LIMIT_BYTES = 100_000

# openpyxl rewrites docProps/core.xml's dcterms:modified to the
# current time at save() regardless of wb.properties.modified, so the
# rezip pass replaces this file wholesale with a frozen canonical
# version to keep the workbook byte-identical across runs.
CORE_PROPERTIES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    "<cp:coreProperties"
    ' xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
    'metadata/core-properties">'
    '<dc:creator xmlns:dc="http://purl.org/dc/elements/1.1/">'
    "phytomni-demo</dc:creator>"
    '<dcterms:created xmlns:dcterms="http://purl.org/dc/terms/"'
    ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
    f' xsi:type="dcterms:W3CDTF">{FIXED_W3CDTF}</dcterms:created>'
    '<dcterms:modified xmlns:dcterms="http://purl.org/dc/terms/"'
    ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
    f' xsi:type="dcterms:W3CDTF">{FIXED_W3CDTF}</dcterms:modified>'
    "<cp:lastModifiedBy>phytomni-demo</cp:lastModifiedBy>"
    "</cp:coreProperties>"
).encode()


# ---------------------------------------------------------------------------
# Payload definitions (one per public MCP tool name in mcp/schemas.py)
# ---------------------------------------------------------------------------

PAYLOADS: dict[str, dict[str, Any]] = {
    "chat_agent.json": {
        "obs_file_list": [],
        "user_query": (
            "Explain the C3 photosynthesis pathway. Cover how RuBisCO "
            "fixes atmospheric CO2 into 3-phosphoglycerate, the role of "
            "the Calvin cycle, and one major weakness compared to C4 "
            "photosynthesis. Keep the answer concise (around 150 words) "
            "and aimed at a researcher new to plant biochemistry."
        ),
    },
    "knowledge_agent.json": {
        "obs_file_list": [],
        "user_query": (
            "Which gene families and signalling pathways are most "
            "strongly implicated in drought tolerance in wheat "
            "(Triticum aestivum)? Mention at least two well-studied "
            "loci and cite the canonical hormonal pathways."
        ),
    },
    "data_agent.json": {
        "user_query": (
            "What are the homologous genes of Os01g0177400 in wheat "
            "(Triticum aestivum)? List up to ten orthologs with their "
            "gene IDs and identity scores."
        ),
    },
    "analyst_agent.json": {
        "data_list": {
            f"{DEMO_OBS_PREFIX}/sequences/sample_rep1.fastq.gz": (
                "Illumina single-end ATAC-seq fastq, 50bp reads, rice "
                "(Oryza sativa) leaf control replicate 1, for peak "
                "calling."
            ),
            f"{DEMO_OBS_PREFIX}/sequences/sample_rep2.fastq.gz": (
                "Illumina single-end ATAC-seq fastq, 50bp reads, rice "
                "(Oryza sativa) leaf control replicate 2, for peak "
                "calling."
            ),
        },
        "goal_description": (
            "Run an ATAC-seq peak-calling workflow on these two rice "
            "leaf control replicates: align reads to the Oryza sativa "
            "reference, mark duplicates, call peaks with MACS2 in "
            "narrow-peak mode, and report peak counts plus the QC "
            "summary."
        ),
        "obs_file_list": [],
    },
    "review_agent.json": {
        "obs_file_list": [],
        "user_query": (
            "Write a literature review on drought adaptation mechanisms "
            "in sorghum (Sorghum bicolor). Cover stomatal regulation, "
            "osmotic adjustment, root architecture, and "
            "transcription-factor regulation, comparing field studies "
            "from the last decade. Aim for around 1000 words, "
            "structured into clear sections."
        ),
    },
    "brief_gene_agent.json": {
        "user_query": "Os01g0177400",
    },
    "deep_genome_agent.json": {
        "gene_id": "Os01g0177400",
        "species_code": "osa",
    },
    "in_silico_research_agent.json": {
        "data_list": {
            f"{DEMO_OBS_PREFIX}/sequences/arabidopsis_sample.fasta": (
                "Curated short Arabidopsis thaliana protein sequences "
                "for orthology benchmarking and motif scanning."
            ),
            f"{DEMO_OBS_PREFIX}/docs/sample_metadata.xlsx": (
                "Sample metadata workbook covering species, tissue, "
                "treatment, and biological replicate columns for the "
                "demo dataset."
            ),
        },
        "obs_file_list": [
            f"{DEMO_OBS_PREFIX}/docs/plant_science_brief.pdf",
        ],
        "user_query": (
            "Decompose the attached plant-science brief into "
            "reproducible computational analyses. Focus on orthology "
            "identification across the curated sequences, expression "
            "pattern comparison using the metadata workbook, and a "
            "simple stress-response classifier."
        ),
    },
    "digital_design_agent.json": {
        "gene_id": "Os01g0177400",
        "obs_file_list": [],
        "species_code": "osa",
    },
    "gene_network_agent.json": {
        "obs_file_list": [],
        "species_code": "osa",
        "to_id": "TO:0000207",
    },
    "get_task_status.json": {
        "task_id": "a1b2c3d4-0000-4000-8000-000000000001",
    },
}


# ---------------------------------------------------------------------------
# Static text assets
# ---------------------------------------------------------------------------

PLANT_SCIENCE_BRIEF_MD = """# Plant Science Brief: Drought Tolerance in Wheat

Drought is the single largest abiotic constraint on wheat (Triticum
aestivum) productivity worldwide, with global yield losses estimated at
10-50% in affected seasons depending on developmental stage and severity.
This brief summarises canonical molecular and physiological mechanisms
cereal crops deploy to tolerate water deficit, with a focus on wheat.

## Hormonal Signalling

Abscisic acid (ABA) is the master regulator of drought response. Stress
triggers ABA biosynthesis primarily through NCED gene family
upregulation. ABA binds PYR/PYL/RCAR receptors, inhibits PP2C
phosphatases, and allows SnRK2 kinases to phosphorylate downstream
effectors including SLAC1, AREB/ABF transcription factors, and ion
channels.

## Stomatal Regulation

ABA-driven stomatal closure reduces transpirational water loss but also
constrains CO2 uptake. Cultivars with rapid stomatal closure show
improved early-stage survival; cultivars that maintain partial stomatal
conductance perform better under mild, sustained deficit.

## Osmotic Adjustment

Accumulation of compatible solutes -- proline, glycine betaine,
trehalose, and soluble sugars -- maintains cell turgor under low water
potential. P5CS and BADH gene family overexpression has been
demonstrated to improve drought tolerance in transgenic wheat and
barley.

## Key Loci

- TaSnRK2.10 -- kinase central to ABA signalling; linked QTLs on
  chromosome 6A associate with field drought tolerance.
- TaDREB1A -- DREB/CBF-family transcription factor binding DRE/CRT
  cis-elements upstream of stress-response genes.
- TaNAC69 -- NAC family; ABA-responsive, regulates root architecture
  under deficit.

This brief is a synthesised demo summary and not a peer-reviewed source.
"""


ARABIDOPSIS_FASTA = """>AT1G01010 NAC001 NAC domain transcription factor
MEDQVGFGFRPNDEELVGHYLRNKIEGNTSRDVEVAISEVNICSYDPWNL
>AT1G75370 SLA1 putative SLAC1-family anion channel
MAKAKLLLAVVLALVAAVVAFAQEVDPETGECMVTEKADLAAANRRPGVL
>AT3G15500 ANAC055 NAC domain transcription factor
MGEKVLHEELVHLPVNGGNNGGGSLPPPTPTANSEHDLSDDNNNCSSREE
>AT5G44030 CESA4 cellulose synthase 4 catalytic subunit
MAFRTHLISIVNFGKKWLGQLEKAYLTSKDLDPVKAFKRVSLVFLLAEAA
>AT5G65080 MAF5 MADS-box flowering regulator
MGRGRVELKRIENKINRQVTFAKRRNGLLKKAYELSVLCDAEVALIIFSS
"""


METADATA_HEADER: list[str] = [
    "sample_id",
    "species",
    "tissue",
    "treatment",
    "biological_replicate",
]

METADATA_ROWS: list[list[Any]] = [
    ["S001", "Arabidopsis thaliana", "leaf", "control", 1],
    ["S002", "Arabidopsis thaliana", "leaf", "drought", 1],
    ["S003", "Oryza sativa", "leaf", "control", 1],
    ["S004", "Oryza sativa", "leaf", "drought", 1],
    ["S005", "Triticum aestivum", "leaf", "drought", 1],
]


# ---------------------------------------------------------------------------
# Manifest + README templates
# ---------------------------------------------------------------------------

TOOL_INDEX: tuple[dict[str, str], ...] = (
    {
        "name": "ChatAgent",
        "payload": "payloads/chat_agent.json",
        "kind": "sync",
        "summary": "General Q&A on C3 photosynthesis (no file upload).",
    },
    {
        "name": "KnowledgeAgent",
        "payload": "payloads/knowledge_agent.json",
        "kind": "sync",
        "summary": "Evidence-backed wheat drought-tolerance question.",
    },
    {
        "name": "DataAgent",
        "payload": "payloads/data_agent.json",
        "kind": "sync",
        "summary": "NL2SQL homology lookup for Os01g0177400 in wheat.",
    },
    {
        "name": "AnalystAgent",
        "payload": "payloads/analyst_agent.json",
        "kind": "async",
        "summary": "ATAC-seq peak-calling submission on rice replicates.",
    },
    {
        "name": "ReviewAgent",
        "payload": "payloads/review_agent.json",
        "kind": "sync",
        "summary": "Multi-section sorghum drought literature review.",
    },
    {
        "name": "BriefGeneAgent",
        "payload": "payloads/brief_gene_agent.json",
        "kind": "sync",
        "summary": (
            "Gene profile preamble (introduction + Gene Profiles + four "
            "analytical sections) for Os01g0177400."
        ),
    },
    {
        "name": "DeepGenomeAgent",
        "payload": "payloads/deep_genome_agent.json",
        "kind": "async",
        "summary": "Deep gene-function analysis for Os01g0177400 in osa.",
    },
    {
        "name": "InSilicoResearchAgent",
        "payload": "payloads/in_silico_research_agent.json",
        "kind": "async",
        "summary": "Reproducibility tasks decomposed from the brief PDF.",
    },
    {
        "name": "DigitalDesignAgent",
        "payload": "payloads/digital_design_agent.json",
        "kind": "async",
        "summary": "Protein and promoter design for Os01g0177400.",
    },
    {
        "name": "GeneNetworkAgent",
        "payload": "payloads/gene_network_agent.json",
        "kind": "async",
        "summary": "Trait-network analysis for rice (TO:0000207).",
    },
    {
        "name": "GetTaskStatus",
        "payload": "payloads/get_task_status.json",
        "kind": "sync",
        "summary": "Non-blocking status poll for a submitted task id.",
    },
)

FILE_INDEX: Mapping[str, str] = {
    "docs/plant_science_brief.md": (
        "Source markdown for the plant-science brief used by Review, "
        "Knowledge, and InSilicoResearch demo runs."
    ),
    "docs/plant_science_brief.pdf": (
        "Deterministic PDF render of the brief, attached via "
        "obs_file_list for upload-enabled tools."
    ),
    "docs/sample_metadata.xlsx": (
        "Five-row sample metadata workbook covering species, tissue, "
        "treatment, and biological replicate columns."
    ),
    "sequences/arabidopsis_sample.fasta": (
        "Five short curated Arabidopsis thaliana protein sequences for "
        "orthology and motif benchmarking."
    ),
}

README_TEMPLATE = """# demo_data

Canonical synthesised fixtures for Phytomni-Bot demo runs and the
live business-layer E2E suite under [../e2e/](../e2e/).

Everything in this directory is regenerated by
[scripts/generate_demo_data.py](scripts/generate_demo_data.py). The
script is byte-deterministic; the local validation gate verifies the
working tree stays clean after a rerun.

## Per-tool payloads

| Tool | Kind | Payload | Summary |
| --- | --- | --- | --- |
{tool_table}

## Supporting fixtures

| Path | Description |
| --- | --- |
{file_table}

## Regenerate

```bash
uv pip install -e ".[demo]"
python demo_data/scripts/generate_demo_data.py
```

The OBS paths inside the payloads use the placeholder prefix
`/obs/phytomni/demo/`. The E2E conftest rewrites them at runtime to
point at the per-session upload location (see
[../e2e/helpers/obs_publish.py](../e2e/helpers/obs_publish.py)).
"""


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def _ensure_parent(path: Path) -> None:
    """Create the parent directory of *path* if it is missing."""
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, data: Any) -> None:
    """Write JSON with sorted keys and a trailing newline."""
    _ensure_parent(path)
    rendered = json.dumps(data, indent=2, sort_keys=True) + "\n"
    path.write_text(rendered, encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    """Write text with LF newlines and no BOM."""
    _ensure_parent(path)
    path.write_bytes(text.encode("utf-8"))


def write_pdf(path: Path, paragraphs: Iterable[str]) -> None:
    """Render *paragraphs* to a deterministic single-page PDF."""
    _ensure_parent(path)
    canv = Canvas(
        str(path),
        pagesize=LETTER,
        invariant=1,
        pageCompression=0,
    )
    canv.setTitle("Phytomni Demo Brief")
    canv.setAuthor("phytomni-demo")
    canv.setCreator("phytomni-demo")
    canv.setSubject("Drought tolerance in wheat")
    canv.setFont("Helvetica", 10)

    text_object = canv.beginText()
    text_object.setTextOrigin(0.75 * inch, LETTER[1] - 0.85 * inch)
    text_object.setLeading(13)
    for paragraph in paragraphs:
        text_object.textLine(paragraph)
    canv.drawText(text_object)
    canv.showPage()
    canv.save()


def write_xlsx(
    path: Path,
    header: list[str],
    rows: list[list[Any]],
) -> None:
    """Render a workbook then repack the ZIP with fixed timestamps."""
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None  # Workbook() always seeds an active sheet.
    sheet.title = "metadata"
    sheet.append(header)
    for row in rows:
        sheet.append(row)

    properties = workbook.properties
    properties.creator = "phytomni-demo"
    properties.lastModifiedBy = "phytomni-demo"
    properties.created = FIXED_TIMESTAMP
    properties.modified = FIXED_TIMESTAMP

    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    _ensure_parent(path)
    with ZipFile(buffer, "r") as src, ZipFile(path, "w", ZIP_DEFLATED) as dst:
        for name in sorted(src.namelist()):
            info = ZipInfo(
                filename=name,
                date_time=FIXED_ZIP_DATE_TIME,
            )
            info.compress_type = ZIP_DEFLATED
            if name == "docProps/core.xml":
                payload = CORE_PROPERTIES_XML
            else:
                payload = src.read(name)
            dst.writestr(info, payload)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _render_readme() -> str:
    tool_lines = [
        "| {name} | {kind} | [{payload}]({payload}) | {summary} |".format(
            **entry
        )
        for entry in TOOL_INDEX
    ]
    file_lines = [
        f"| [{name}]({name}) | {description} |"
        for name, description in sorted(FILE_INDEX.items())
    ]
    return README_TEMPLATE.format(
        tool_table="\n".join(tool_lines),
        file_table="\n".join(file_lines),
    )


def _build_manifest() -> dict[str, Any]:
    return {
        "files": dict(sorted(FILE_INDEX.items())),
        "tools": list(TOOL_INDEX),
    }


def _verify_footprint() -> int:
    total = sum(
        entry.stat().st_size
        for entry in DEMO_ROOT.rglob("*")
        if entry.is_file()
    )
    if total >= FOOTPRINT_LIMIT_BYTES:
        raise SystemExit(
            f"demo_data footprint {total} bytes exceeds limit "
            f"{FOOTPRINT_LIMIT_BYTES}; trim fixtures before committing."
        )
    return total


def main() -> None:
    """Regenerate every committed fixture under ``demo_data/``."""
    for filename, payload in PAYLOADS.items():
        write_json(DEMO_ROOT / "payloads" / filename, payload)

    write_text(
        DEMO_ROOT / "docs" / "plant_science_brief.md",
        PLANT_SCIENCE_BRIEF_MD,
    )
    write_pdf(
        DEMO_ROOT / "docs" / "plant_science_brief.pdf",
        PLANT_SCIENCE_BRIEF_MD.splitlines(),
    )
    write_xlsx(
        DEMO_ROOT / "docs" / "sample_metadata.xlsx",
        METADATA_HEADER,
        METADATA_ROWS,
    )

    write_text(
        DEMO_ROOT / "sequences" / "arabidopsis_sample.fasta",
        ARABIDOPSIS_FASTA,
    )

    write_json(DEMO_ROOT / "manifest.json", _build_manifest())
    write_text(DEMO_ROOT / "README.md", _render_readme())

    total = _verify_footprint()
    print(f"demo_data total size: {total} bytes")


if __name__ == "__main__":
    main()
