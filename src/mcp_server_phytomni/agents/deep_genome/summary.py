# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Helpers for loading DeepGenome analyst result summaries.

Exports summary spec/result types, SubSummaryBuilder, and build_sub_summary.
The builder reads downloaded analyst files, normalizes image/table labels, and
returns report data plus the next figure index.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

READ_ERRORS = (StopIteration, FileNotFoundError, OSError, IOError)


class UnusableAnalysisResultError(ValueError):
    """Raised when a completed analysis has no usable local content."""


class ImageSummarySpec(NamedTuple):
    """File matching and output keys for one image summary group.

    Attributes:
        analysis_type: Analysis task type handled by this spec.
        image_pattern: Glob pattern for the primary image.
        image_key: Output data key for the image path.
        summary_pattern: Glob or format pattern for the summary file.
        summary_key: Output data key for the summary text.
        legend_pattern: Glob or format pattern for the legend file.
        legend_key: Output data key for the legend text.
        missing_summary: Summary value used when files are absent.
        replace_summary_figure: Whether to rewrite summary figure labels.
        fixed_summary: Whether summary_pattern is formatted directly.
        fixed_legend: Whether legend_pattern is formatted directly.
    """

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
    """DeepGenome summary data and next figure index.

    Attributes:
        data: Updated report data dictionary.
        figure_index: Next figure index after loaded assets are counted.
    """

    data: dict[str, Any]
    figure_index: int


class SubSummaryBuilder:
    """Load DeepGenome analysis files into the report summary dictionary.

    Attributes:
        gene_id: Target gene identifier used in result paths.
        out_path: Local directory containing downloaded analysis results.
        data: Mutable report data dictionary.
        figure_index: Current figure index used for label rewriting.
        handlers: Mapping from analysis type to loader callable.
    """

    def __init__(
        self,
        gene_id: str,
        out_path: Path,
        data: dict[str, Any],
        figure_index: int,
    ):
        """Initialize summary loading context."""
        self.gene_id = gene_id
        self.out_path = out_path
        # Per-gene image directory the markdown report references via the
        # ../../ prefix below. Use gene_id directly — the prior
        # ``out_path.split(".out/")[-1]`` recovered the gene dir only when
        # out_path was literally under ``.out/`` and produced a full
        # absolute path for obsfs result dirs, breaking the markdown
        # ``../../{dir}/`` prefix the report renders against.
        if ".out" in str(out_path):
            self.markdown_path = str(out_path).rsplit(".out/", maxsplit=1)[-1]
        else:
            self.markdown_path = gene_id
        self.data = data
        self.figure_index = figure_index
        self.handlers: dict[str, Callable[[], None]] = {
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
        """Load result files for the requested analysis type.

        Args:
            analysis_type: DeepGenome analysis type to summarize.

        Returns:
            Updated report data and next figure index.
        """
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
        """Load a standard image, summary, and legend result group.

        Args:
            spec: File patterns and data keys for the result group.
        """
        try:
            image_name = self.first_match(spec.image_pattern)
            self.data[spec.image_key] = (
                f"../../{self.markdown_path}/{image_name}"
            )
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
        except (FileNotFoundError, OSError):
            self.data["domain_table"] = ""
            self.data["domain_summary"] = "None Results"
            self.data["domain_legend"] = ""

    def load_single_cell(self) -> None:
        """Load single-cell UMAP, violin, summary, and legend outputs."""
        try:
            self.data["umap_path"] = (
                f"../../{self.markdown_path}/{self.first_match('*_umap.png')}"
            )
            self.data["violin_path"] = (
                f"../../{self.markdown_path}/"
                f"{self.first_match('*_violin_plot.png')}"
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
        except (FileNotFoundError, OSError):
            self.data["protein_structures"] = "None Results"

    def structure_block(self, structure_path: Path) -> str:
        """Return one protein structure markdown block.

        Args:
            structure_path: CIF file path for one predicted structure.

        Returns:
            Markdown block containing structure image/link text, legend, and
            summary content.
        """
        structure_file = f"../../{self.markdown_path}/{structure_path.name}"
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
        """Return the summary file name for a spec.

        Args:
            spec: Image summary spec that defines summary matching behavior.

        Returns:
            Resolved summary file name.
        """
        if spec.fixed_summary:
            return spec.summary_pattern.format(gene_id=self.gene_id)
        return self.first_match(spec.summary_pattern)

    def legend_name(self, spec: ImageSummarySpec) -> str:
        """Return the legend file name for a spec.

        Args:
            spec: Image summary spec that defines legend matching behavior.

        Returns:
            Resolved legend file name.
        """
        if spec.fixed_legend:
            return spec.legend_pattern.format(gene_id=self.gene_id)
        return self.first_match(spec.legend_pattern)

    def first_match(self, pattern: str) -> str:
        """Return the first matching file name for a pattern.

        Args:
            pattern: Glob pattern searched recursively under ``out_path``.

        Returns:
            Name of the first matching file.
        """
        return next(self.out_path.rglob(pattern)).name

    def read_text(
        self,
        file_name: str,
        *,
        replace_figure: bool,
        replace_table: bool = False,
    ) -> str:
        """Read a text file and replace figure/table labels when requested.

        Args:
            file_name: Relative file name to read under ``out_path``.
            replace_figure: Whether to replace ``Figure 1`` labels.
            replace_table: Whether to replace ``Table 1`` labels.

        Returns:
            Text content with optional figure or table label replacement.
        """
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
    # §8.2 Artificial Intelligence Design (protein). The design task
    # produces psap_scores.png and a summary_all.py --summary_type
    # protein_design summary/legend; the file names follow the motif
    # convention ({gene_id}_<summary_type>.summary/.legend).
    ImageSummarySpec(
        analysis_type="protein_design_analysis",
        image_pattern="psap_scores.png",
        image_key="protein_path",
        summary_pattern="{gene_id}_protein_design.summary",
        summary_key="protein_summary",
        legend_pattern="{gene_id}_protein_design.legend",
        legend_key="protein_legend",
        fixed_summary=True,
        fixed_legend=True,
    ),
)


def _relative_result_name(path: Path, results_dir: Path) -> str:
    """Return a stable POSIX-relative artifact name."""
    return path.relative_to(results_dir).as_posix()


def _nonblank_summary_files(results_dir: Path) -> list[tuple[str, str]]:
    """Read nonblank summary files in deterministic path order."""
    summaries: list[tuple[str, str]] = []
    for path in sorted(
        results_dir.rglob("*.summary"),
        key=lambda candidate: _relative_result_name(candidate, results_dir),
    ):
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (FileNotFoundError, OSError, UnicodeError):
            continue
        if text:
            summaries.append((_relative_result_name(path, results_dir), text))
    return summaries


def _design_artifacts(work_item_key: str, results_dir: Path) -> list[str]:
    """Return the allow-listed design artifacts in stable order."""
    names: set[str] = set()
    for path in results_dir.rglob("*"):
        if not path.is_file():
            continue
        relative = _relative_result_name(path, results_dir)
        if (
            path.suffix in {".legend", ".json"}
            or (
                work_item_key == "promoter_design"
                and path.name == "motif_all_logo.png"
            )
            or (
                work_item_key == "protein_design"
                and path.name == "psap_scores.png"
            )
        ):
            names.add(relative)
    return sorted(names)


def build_design_work_item_summary(
    work_item_key: str,
    results_dir: str | Path,
) -> str:
    """Build deterministic Markdown for one Digital Design work item.

    A successful promoter result commonly contains several summary files;
    all nonblank files are joined in filename order.  Some platform
    versions only emit the motif image and manifest artifacts, so the
    fallback lists the allow-listed artifacts rather than manufacturing
    scientific prose.  The same deterministic contract is used for the
    protein work item, which keeps both concrete jobs independently
    resolvable by the coordinator.

    Args:
        work_item_key: ``protein_design`` or ``promoter_design``.
        results_dir: Local directory containing downloaded result files.

    Returns:
        Nonblank Markdown headed by the work-item display name.

    Raises:
        UnusableAnalysisResultError: If no nonblank summary or allow-listed
            artifact exists.
        ValueError: If ``work_item_key`` is not a supported design job.
    """
    if work_item_key not in {"protein_design", "promoter_design"}:
        raise ValueError(f"unknown design work item: {work_item_key}")
    root = Path(results_dir)
    if not root.is_dir():
        raise UnusableAnalysisResultError(
            "design result directory is unavailable"
        )
    title = (
        "Protein Design"
        if work_item_key == "protein_design"
        else ("Promoter Design")
    )
    summaries = _nonblank_summary_files(root)
    if summaries:
        body = "\n\n".join(f"### {name}\n\n{text}" for name, text in summaries)
        return f"## {title}\n\n{body}\n"
    artifacts = _design_artifacts(work_item_key, root)
    if not artifacts:
        raise UnusableAnalysisResultError(
            f"{work_item_key} result contains no usable content"
        )
    artifact_lines = "\n".join(f"- `{name}`" for name in artifacts)
    return f"## {title}\n\n" "Result artifacts:\n\n" f"{artifact_lines}\n"


def build_sub_summary(
    analysis_type: str,
    gene_id: str,
    deepgenome_out: str,
    data: dict[str, Any] | None,
    figure_index: int,
    **kwargs: Any,
) -> SummaryBuildResult:
    """Build one DeepGenome analyst sub-summary.

    Args:
        analysis_type: Analysis type whose result files should be loaded.
        gene_id: Target gene identifier.
        deepgenome_out: Default DeepGenome output directory.
        data: Existing report data dictionary, if any.
        figure_index: Current figure index before loading this summary.
        **kwargs: Optional results_dir override for tests or custom outputs.

    Returns:
        Updated summary data and next figure index.
    """
    display_order = kwargs.get("display_order")
    if isinstance(display_order, int) and display_order >= 0:
        figure_index = display_order + 1
    results_dir = kwargs.get("results_dir")
    gene_results_data = data if data is not None else {"gene_name": gene_id}
    out_path = Path(results_dir) if results_dir else Path(deepgenome_out)
    if results_dir is None:
        out_path = out_path / gene_id
    builder = SubSummaryBuilder(
        gene_id=gene_id,
        out_path=out_path,
        data=gene_results_data,
        figure_index=figure_index,
    )
    return builder.build(analysis_type)
