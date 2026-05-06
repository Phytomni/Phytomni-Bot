# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Helpers for loading DeepGenome analyst result summaries."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

READ_ERRORS = (StopIteration, FileNotFoundError, OSError, IOError)


@dataclass(frozen=True)
class ImageSummarySpec:
    """File matching and output keys for one image summary group."""

    analysis_type: str
    image_pattern: str
    image_key: str
    summary_pattern: str
    summary_key: str
    legend_pattern: str
    legend_key: str
    missing_summary: str = "None Results"
    replace_summary_figure: bool = True
    fixed_summary: bool = False
    fixed_legend: bool = False


@dataclass
class SummaryBuildResult:
    """DeepGenome summary data and next figure index."""

    data: Dict[str, Any]
    figure_index: int


class SubSummaryBuilder:
    """Load DeepGenome analysis files into the report summary dictionary."""

    def __init__(
        self,
        gene_id: str,
        out_path: Path,
        data: Dict[str, Any],
        figure_index: int,
    ):
        """Initialize summary loading context."""
        self.gene_id = gene_id
        self.out_path = out_path
        self.data = data
        self.figure_index = figure_index
        self.handlers: Dict[str, Callable[[], None]] = {
            "evolution_analysis": self.load_evolution,
            "single_cell_analysis": self.load_single_cell,
            "protein_structure_analysis": self.load_protein_structure,
        }
        self.handlers.update(
            {
                spec.analysis_type: self._spec_handler(spec)
                for spec in IMAGE_SUMMARY_SPECS
            }
        )

    def build(self, analysis_type: str) -> SummaryBuildResult:
        """Load result files for the requested analysis type."""
        handler = self.handlers.get(analysis_type)
        if handler is not None:
            handler()
        return SummaryBuildResult(
            data=self.data,
            figure_index=self.figure_index,
        )

    def _spec_handler(self, spec: ImageSummarySpec) -> Callable[[], None]:
        """Return a loader function for a standard image summary spec."""

        def load_spec() -> None:
            self.load_image_summary(spec)

        return load_spec

    def load_image_summary(self, spec: ImageSummarySpec) -> None:
        """Load a standard image + summary + legend result group."""
        try:
            image_name = self.first_match(spec.image_pattern)
            self.data[spec.image_key] = f"{self.gene_id}/{image_name}"
            summary_name = self.summary_name(spec)
            self.data[spec.summary_key] = self.read_text(
                summary_name,
                replace_figure=spec.replace_summary_figure,
            )
            legend_name = self.legend_name(spec)
            self.data[spec.legend_key] = self.read_text(
                legend_name,
                replace_figure=True,
            )
            self.figure_index += 1
        except READ_ERRORS:
            self.data[spec.image_key] = ""
            self.data[spec.summary_key] = spec.missing_summary
            self.data[spec.legend_key] = ""

    def load_evolution(self) -> None:
        """Load tree image/summary/legend and domain table outputs."""
        self.load_image_summary(
            ImageSummarySpec(
                analysis_type="evolution_analysis",
                image_pattern="*tree.png",
                image_key="tree_path",
                summary_pattern=f"{self.gene_id}_tree.summary",
                summary_key="tree_summary",
                legend_pattern=f"{self.gene_id}_tree.legend",
                legend_key="tree_legend",
                fixed_summary=True,
                fixed_legend=True,
            )
        )
        self.load_domain_table()

    def load_domain_table(self) -> None:
        """Load evolution domain table, summary, and legend outputs."""
        try:
            self.data["domain_table"] = self.read_text(
                f"{self.gene_id}_domain.md",
                replace_figure=False,
            )
            self.data["domain_summary"] = self.read_text(
                f"{self.gene_id}_domain.summary",
                replace_figure=False,
            )
            self.data["domain_legend"] = self.read_text(
                f"{self.gene_id}_domain.legend",
                replace_figure=False,
            )
        except (FileNotFoundError, OSError, IOError):
            self.data["domain_table"] = ""
            self.data["domain_summary"] = "None Results"
            self.data["domain_legend"] = ""

    def load_single_cell(self) -> None:
        """Load single-cell UMAP, violin, summary, and legend outputs."""
        try:
            self.data["umap_path"] = (
                f"{self.gene_id}/{self.first_match('*_umap.png')}"
            )
            self.data["violin_path"] = (
                f"{self.gene_id}/{self.first_match('*_violin_plot.png')}"
            )
            self.data["single_cell_summary"] = self.read_text(
                f"{self.gene_id}_single_cell.summary",
                replace_figure=False,
            )
            self.data["single_cell_legend"] = self.read_text(
                f"{self.gene_id}_single_cell.legend",
                replace_figure=True,
            )
            self.figure_index += 1
        except READ_ERRORS:
            self.data["umap_path"] = ""
            self.data["violin_path"] = ""
            self.data["single_cell_summary"] = "None Results"
            self.data["single_cell_legend"] = ""

    def load_protein_structure(self) -> None:
        """Load protein structure CIF, legend, and summary outputs."""
        try:
            structure_files = list(
                self.out_path.glob("*_seed_101_sample_0.cif")
            )
            if not structure_files:
                self.data["protein_structures"] = "None Results"
                return
            self.data["protein_structures"] = "".join(
                self.structure_block(structure_path)
                for structure_path in structure_files
            )
        except (FileNotFoundError, OSError, IOError):
            self.data["protein_structures"] = "None Results"

    def structure_block(self, structure_path: Path) -> str:
        """Return one protein structure markdown block."""
        structure_file = f"{self.gene_id}/{structure_path.name}"
        structure_start = structure_path.name.split(".cif")[0]
        legend = self.read_text(
            f"{structure_start}.legend",
            replace_figure=True,
            replace_table=True,
        )
        summary = self.read_text(
            f"{structure_start}.summary",
            replace_figure=True,
        )
        self.figure_index += 1
        return f"![3D Structure]({structure_file})\n{legend}\n{summary}\n"

    def summary_name(self, spec: ImageSummarySpec) -> str:
        """Return the summary file name for a spec."""
        if spec.fixed_summary:
            return spec.summary_pattern.format(gene_id=self.gene_id)
        return self.first_match(spec.summary_pattern)

    def legend_name(self, spec: ImageSummarySpec) -> str:
        """Return the legend file name for a spec."""
        if spec.fixed_legend:
            return spec.legend_pattern.format(gene_id=self.gene_id)
        return self.first_match(spec.legend_pattern)

    def first_match(self, pattern: str) -> str:
        """Return the first matching file name for a pattern."""
        return next(self.out_path.rglob(pattern)).name

    def read_text(
        self,
        file_name: str,
        *,
        replace_figure: bool,
        replace_table: bool = False,
    ) -> str:
        """Read a text file and replace figure/table labels when requested."""
        content = (self.out_path / file_name).read_text(encoding="utf-8")
        label = f"Figure {self.figure_index}"
        if replace_table:
            content = content.replace("Table 1", label)
        if replace_figure:
            content = content.replace("Figure 1", label)
        return content


IMAGE_SUMMARY_SPECS = (
    ImageSummarySpec(
        analysis_type="gene_expression_tissues",
        image_pattern="*tissues.png",
        image_key="tissue_path",
        summary_pattern="*tissues.summary",
        summary_key="tissue_summary",
        legend_pattern="*tissues.legend",
        legend_key="tissue_legend",
    ),
    ImageSummarySpec(
        analysis_type="gene_expression_cultivars",
        image_pattern="*cultivars.png",
        image_key="cultivar_path",
        summary_pattern="*cultivars.summary",
        summary_key="cultivar_summary",
        legend_pattern="*cultivars.legend",
        legend_key="cultivar_legend",
    ),
    ImageSummarySpec(
        analysis_type="gene_expression_treatments",
        image_pattern="*treatments.png",
        image_key="treatment_path",
        summary_pattern="*treatments.summary",
        summary_key="treatment_summary",
        legend_pattern="*treatments.legend",
        legend_key="treatment_legend",
    ),
    ImageSummarySpec(
        analysis_type="gene_expression_genotypes",
        image_pattern="*genotypes.png",
        image_key="mutant_path",
        summary_pattern="*genotypes.summary",
        summary_key="mutant_summary",
        legend_pattern="*genotypes.legend",
        legend_key="mutant_legend",
    ),
    ImageSummarySpec(
        analysis_type="haplotypes_analysis",
        image_pattern="*promoter_hap.png",
        image_key="haplotype_path",
        summary_pattern="{gene_id}_haplotype.summary",
        summary_key="haplotype_summary",
        legend_pattern="{gene_id}_haplotype.legend",
        legend_key="haplotype_legend",
        replace_summary_figure=False,
        fixed_summary=True,
        fixed_legend=True,
    ),
    ImageSummarySpec(
        analysis_type="promoter_analysis",
        image_pattern="motif_all_logo.png",
        image_key="motif_path",
        summary_pattern="{gene_id}_motif.summary",
        summary_key="motif_summary",
        legend_pattern="{gene_id}_motif.legend",
        legend_key="motif_legend",
        fixed_summary=True,
        fixed_legend=True,
    ),
    ImageSummarySpec(
        analysis_type="smep_analysis",
        image_pattern="*smep.png",
        image_key="smep_path",
        summary_pattern="*smep.summary",
        summary_key="smep_summary",
        legend_pattern="*smep.legend",
        legend_key="smep_legend",
        missing_summary="",
    ),
    ImageSummarySpec(
        analysis_type="smoc_analysis",
        image_pattern="*smoc.png",
        image_key="smoc_path",
        summary_pattern="*smoc.summary",
        summary_key="smoc_summary",
        legend_pattern="*smoc.legend",
        legend_key="smoc_legend",
        missing_summary="",
    ),
)


def build_sub_summary(
    analysis_type: str,
    gene_id: str,
    deepgenome_out: str,
    data: Optional[Dict[str, Any]],
    figure_index: int,
) -> SummaryBuildResult:
    """Build one DeepGenome analyst sub-summary."""
    gene_results_data = data if data is not None else {"gene_name": gene_id}
    builder = SubSummaryBuilder(
        gene_id=gene_id,
        out_path=Path(deepgenome_out) / gene_id,
        data=gene_results_data,
        figure_index=figure_index,
    )
    return builder.build(analysis_type)
