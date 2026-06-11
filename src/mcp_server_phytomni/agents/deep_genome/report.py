# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Report synthesis nodes for the DeepGenome workflow.

Exports DeepGenomeReportMixin, which combines Part 1 profiles, analyst
summaries, recommended experiments, protocols, discussion, summary sections,
and follow-up questions into the final report state.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, Optional

from ...common.prompts import get_prompt
from ...common.responses import (
    message_content,
    parse_follow_up_questions,
    parse_json_list_fragment,
)
from ...config.defaults import DeepGenomeConfig
from ...graphs.chat_adapters import build_chat_input, extract_chat_response
from ...runtime.task_manager import TaskManager, resolve_tasks_db_path
from ...runtime.workflow_mixins import WorkflowMixinBase
from ...storage.path_policy import RunIdentity
from ...storage.scratch import ScratchTarget, resolve_scratch_dir
from ..chat.service import _cached_chat_app
from .formatting import SPECIES_CODE_MAP

if TYPE_CHECKING:
    from .agent import DeepGenomeState
else:
    DeepGenomeState = dict[str, Any]

logger = logging.getLogger(__name__)

DEEP_GENOME_CONFIG = DeepGenomeConfig()


class SynthesisSection(NamedTuple):
    """Configuration for one synthesis section in the final report.

    Attributes:
        template_key: Key of the section template in prompts.yaml
            (``template/<template_key>``).
        required_keys: Data keys that must all be present and non-empty
            for the section to be rendered. Missing values cause the
            section to be skipped.
        legend_keys: Data keys whose ``Figure N`` label is rewritten at
            render time to match the section's render position.
    """

    template_key: str
    required_keys: tuple[str, ...]
    legend_keys: tuple[str, ...]


# Single source of truth for report section order. Sections not present
# in this list are not rendered. When an analyst branch has no data, the
# section is dropped and the next rendered section still receives the
# next integer — do not reorder the remaining entries around the gap.
_SYNTHESIS_SECTIONS: tuple[SynthesisSection, ...] = (
    SynthesisSection(
        template_key="section_phylogenetic",
        required_keys=("tree_path", "tree_summary", "tree_legend"),
        legend_keys=("tree_legend",),
    ),
    SynthesisSection(
        template_key="section_transcriptomic_tissue",
        required_keys=("tissue_path", "tissue_summary", "tissue_legend"),
        legend_keys=("tissue_legend",),
    ),
    SynthesisSection(
        template_key="section_transcriptomic_cultivar",
        required_keys=(
            "cultivar_path",
            "cultivar_summary",
            "cultivar_legend",
        ),
        legend_keys=("cultivar_legend",),
    ),
    SynthesisSection(
        template_key="section_transcriptomic_treatment",
        required_keys=(
            "treatment_path",
            "treatment_summary",
            "treatment_legend",
        ),
        legend_keys=("treatment_legend",),
    ),
    SynthesisSection(
        template_key="section_genetic",
        required_keys=("mutant_path", "mutant_summary", "mutant_legend"),
        legend_keys=("mutant_legend",),
    ),
    SynthesisSection(
        template_key="section_single_cell",
        required_keys=("single_cell_summary",),
        legend_keys=("single_cell_legend",),
    ),
    SynthesisSection(
        template_key="section_haplotype",
        required_keys=("haplotype_path", "haplotype_summary"),
        legend_keys=("haplotype_legend",),
    ),
    SynthesisSection(
        template_key="section_cis_regulatory_motif",
        required_keys=("motif_path", "motif_summary", "motif_legend"),
        legend_keys=("motif_legend",),
    ),
    SynthesisSection(
        template_key="section_cis_regulatory_smep",
        required_keys=("smep_path", "smep_summary", "smep_legend"),
        legend_keys=("smep_legend",),
    ),
    SynthesisSection(
        template_key="section_cis_regulatory_smoc",
        required_keys=("smoc_path", "smoc_summary", "smoc_legend"),
        legend_keys=("smoc_legend",),
    ),
    SynthesisSection(
        template_key="section_protein_domain",
        required_keys=("domain_table", "domain_summary"),
        legend_keys=("domain_legend",),
    ),
    SynthesisSection(
        template_key="section_protein_structure",
        required_keys=("protein_structures",),
        legend_keys=(),
    ),
    SynthesisSection(
        template_key="section_ai_design_promoter",
        required_keys=(
            "promoter_path",
            "promoter_summary",
            "promoter_legend",
        ),
        legend_keys=("promoter_legend",),
    ),
    SynthesisSection(
        template_key="section_ai_design_protein",
        required_keys=("protein_path", "protein_summary", "protein_legend"),
        legend_keys=("protein_legend",),
    ),
)

_FIGURE_LABEL_RE = re.compile(r"\bFigure \d+\b")
_EMPTY_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*\)\s*")


def _section_has_data(data: dict[str, Any], keys: tuple[str, ...]) -> bool:
    """Return True when every required key holds non-empty data.

    Treats the ``None Results`` placeholder string the loaders write
    when files are missing as empty so such sections are skipped.
    """
    for key in keys:
        value = data.get(key)
        if not value:
            return False
        if isinstance(value, str) and value.strip().lower() == "none results":
            return False
    return True


def _assemble_sections(
    data: dict[str, Any],
    prompt_file: Any,
) -> str:
    """Render report sections in order, skipping sections with no data.

    Iterates over ``_SYNTHESIS_SECTIONS`` and renders each section via
    the matching ``template/<key>`` entry in ``prompts.yaml``. Sections
    whose required keys are missing or empty are dropped; the next
    rendered section still receives the next integer, so a gap in the
    data does not cause the downstream numbering to stall. Each
    section's legend text is rewritten so the ``Figure N`` label
    matches the section's render position rather than its data-load
    order, which keeps cross-references stable across runs.

    Args:
        data: Report data dictionary keyed by template placeholders.
        prompt_file: Prompt file path consumed by ``get_prompt``.

    Returns:
        Concatenated markdown body of all rendered sections separated
        by blank lines, with empty image placeholders stripped.
    """
    sections: list[str] = []
    section_index = 0
    for section in _SYNTHESIS_SECTIONS:
        if not _section_has_data(data, section.required_keys):
            continue
        section_index += 1
        section_data = dict(data)
        for legend_key in section.legend_keys:
            legend = section_data.get(legend_key)
            if isinstance(legend, str) and legend:
                section_data[legend_key] = _FIGURE_LABEL_RE.sub(
                    f"Figure {section_index}", legend
                )
        section_data["section_number"] = section_index
        sections.append(
            get_prompt(
                prompt_file,
                f"template/{section.template_key}",
                section_data,
            )
        )
    return _EMPTY_IMAGE_RE.sub("", "\n".join(sections))


def _state_gene_string(state: "DeepGenomeState") -> str:
    """Return the display gene string from workflow state."""
    return state.get("gene_annotation", {}).get("gene_string", "")


def _assemble_final_report(state: "DeepGenomeState") -> str:
    """Concatenate all report sections into one markdown string.

    Branches on ``use_analyst_agent`` to pick between the full
    seven-section layout (intro + part12 + recommended experiments
    + discussion + conclusion) and the analyst-off three-section
    layout (part12 + discussion + conclusion). Extracted from
    ``_run_follow_up_node`` to keep that node within pylint's
    R0914 too-many-locals cap.
    """
    gene_id = state["gene_id"]
    use_analyst = state.get("config_params", {}).get("use_analyst_agent", True)
    part12 = state.get("part12_combined") or ""
    discussion = state.get("discussion_report", "")
    summary = state.get("summary_report", "")
    if not use_analyst:
        return (
            f"# Deep Genome Analysis of {gene_id}\n\n"
            f"{part12}\n\n"
            f"## Discussion\n\n{discussion}\n\n"
            f"## Conclusion and Future Outlook\n\n{summary}\n\n"
        )
    return (
        f"# Deep Genome Analysis of {gene_id}\n\n"
        f"{state.get('introduction_report', '')}\n\n"
        f"{part12}\n\n"
        f"## Recommended experiments\n\n"
        f"{state.get('protocol_report', '')}\n\n"
        f"{state.get('experiment_report', '')}\n\n"
        f"## Discussion\n\n{discussion}\n\n"
        f"## Conclusion and Future Outlook\n\n{summary}\n\n"
    )


class DeepGenomeReportMixin(WorkflowMixinBase):
    """Report synthesis and finalization nodes for DeepGenome."""

    async def _dispatch_knowledge_retrieve(
        self: Any,
        user_query: str,
        repo_id_dict: dict[str, int],
    ) -> dict[str, Any] | None:
        """Dispatch one protocol retrieval via the knowledge subgraph.

        Owns the knowledge-call seam ``_experiment_protocols`` uses to
        fan out one retrieve+generate per recommended experiment.
        Invokes the per-instance compiled knowledge subgraph
        (``self._agents.knowledge_app``, built in
        ``DeepGenomeAgents.__init__``) with the same
        ``KnowledgeInput`` shape every other knowledge-subgraph
        consumer uses, then unwraps the
        ``KnowledgeOutput.final_response`` envelope so callers keep
        their historical chat-completion dict interface.

        Args:
            user_query: Retrieval query (one recommended experiment).
            repo_id_dict: Repo id → page size mapping consumed by the
                retriever (protocol repo for deep_genome).

        Returns:
            Chat completion dict matching the historical
            ``knowledge_agent.arun`` return shape.
        """
        knowledge_output = await self._agents.knowledge_app.ainvoke(
            {
                "user_query": user_query,
                "repo_id_dict": repo_id_dict,
                "is_generate": True,
                "is_follow_up": False,
            }
        )
        return knowledge_output["final_response"]

    async def _dispatch_chat(
        self: Any, user_query: str
    ) -> dict[str, Any] | None:
        """Dispatch one chat call via the shared chat subgraph.

        Owns the chat-call seam shared by every report node body
        (experiment / protocol / discussion / summary / follow_up).
        Invokes the cached chat compiled app with the shared
        ``chat_adapters`` IO mappers, keeping the response in the
        historical chat-completion envelope so callers stay agnostic
        to the dispatch route.

        Args:
            user_query: Prompt body to send to the chat backend.

        Returns:
            Chat completion dict matching the historical chat-completion
            return shape (``{"choices": [...]}``).
        """
        chat_kwargs_bag = self._chat_kwargs()
        chat_output = await _cached_chat_app().ainvoke(
            build_chat_input(
                user_query=user_query, chat_kwargs=chat_kwargs_bag
            )
        )
        return extract_chat_response(chat_output)

    async def _run_report_synthesizer(self: Any, state: DeepGenomeState):
        """Generate the report after all analysis branches finish."""
        completed = state.get("analysis_completed_branches", 0)
        total_expected = len(state.get("analysis_tasks", []))

        # Test mode: skip synthesize_node and use mock data directly
        if state.get("skip_synthesize", False):
            logger.info(
                "[Test Mode] Skipping synthesize_node, using "
                "pre-prepared mock data"
            )
            return {"experiment_completed_branches": 1}

        if total_expected > 0 and completed < total_expected:
            logger.info(
                "[Barrier] Waiting for analysis completion: %s/%s",
                completed,
                total_expected,
            )
            return {"synthesis_waiting": True}
        logger.info(
            "[Barrier] All analysis completed (%s/%s), starting synthesis",
            completed,
            total_expected,
        )

        gene_results_data = state.get("analyst_summaries", {})
        gene_results_body = _assemble_sections(
            gene_results_data,
            self.deep_genome_config.PROMPT_FILE,
        )
        gene_results = (
            f"## Bioinformatic Analysis and Molecular Design\n\n"
            f"{gene_results_body}"
        )
        run_identity = RunIdentity.create(
            user_id=self.deep_genome_config.USER_ID,
            scope="report",
        )
        report_dir = resolve_scratch_dir(
            "tmp",
            run_identity,
            "report",
            ScratchTarget(
                bucket_name=self.deep_genome_config.BUCKET_NAME,
                local_fallback=Path(self.deep_genome_config.DEEPGENOME_OUT),
            ),
        )
        results_path = Path(report_dir) / f"{state['gene_id']}_results.md"
        with open(results_path, "w", encoding="utf-8") as fo:
            fo.write(gene_results)
        return {
            "report_dir": report_dir,
            "synthesize_report": gene_results,
            "experiment_completed_branches": 1,
        }

    async def _run_report_experiment(self: Any, state: DeepGenomeState):
        """Barrier node for experiment recommendations generation.

        This is the "Ultimate Convergence" barrier. It waits for both
        part1_node and synthesize_node, then generates recommended
        experiments using LLM. It also retrieves detailed protocols.

        Args:
            state: Current workflow state containing part1_report and
                synthesize_report.

        Returns:
            Dict with experiment_report, part12_combined, and
            report_triggered flag, or empty dict if blocked.
        """
        # Barrier 3: Ultimate convergence - wait for part1 and synthesize
        if state.get("experiment_completed_branches", 0) < 2:
            logger.info(
                "[Ultimate Barrier] Waiting for both branches: %s/2",
                state.get("experiment_completed_branches", 0),
            )
            return {"experiment_waiting": True}

        # Prevent duplicate execution - already done, let workflow proceed
        if state.get("report_triggered", False):
            return {}
        logger.info(
            "[Ultimate Convergence] Basic profile + Deep analysis merged; "
            "designing recommended experiments"
        )

        part12_str = self._part12_profile(state)
        experiment_response = await self._dispatch_chat(
            self._experiment_prompt(state, part12_str)
        )
        experiment_list = parse_json_list_fragment(
            message_content(experiment_response)
        )
        experiments_str = await self._experiment_protocols(experiment_list)

        return {
            "experiment_report": experiments_str,
            "part12_combined": part12_str,
            "report_triggered": True,
        }

    def _part12_profile(self: Any, state: DeepGenomeState) -> str:
        """Combine basic and deep analysis profiles for report prompts."""
        part1_str = state.get("part1_report", "")
        part2_str = str(state.get("synthesize_report", "") or "")
        return f"## Gene Profiles\n\n{part1_str}\n\n{part2_str}\n\n"

    def _experiment_prompt(
        self: Any,
        state: DeepGenomeState,
        content: str,
    ) -> str:
        """Build the recommended-experiment prompt."""
        gene_annotation = state.get("gene_annotation", {})
        return get_prompt(
            self.deep_genome_config.PROMPT_FILE,
            "user/gene_function_experiment",
            {
                "gene_string": gene_annotation.get("gene_string", ""),
                "species_string": SPECIES_CODE_MAP[state["species_code"]],
                "content": content,
            },
        )

    async def _experiment_protocols(
        self: Any,
        experiments: list[Any],
    ) -> str:
        """Retrieve protocol sections for recommended experiments."""
        sections = []
        for index, experiment in enumerate(experiments):
            protocol_response = await self._dispatch_knowledge_retrieve(
                user_query=experiment,
                repo_id_dict={
                    DEEP_GENOME_CONFIG.PROTOCOL_REPO_ID: (
                        DEEP_GENOME_CONFIG.PROTOCOL_PAGE_SIZE
                    )
                },
            )
            sections.append(
                f"## {index + 1}. Step-by-Step {experiment} Protocol\n\n"
                f"{message_content(protocol_response)}\n"
            )
        return "".join(sections)

    async def _run_report_protocol(self: Any, state: DeepGenomeState):
        """Generate experimental protocol summary.

        This node generates a summary of experimental protocols based on the
        recommended experiments and analysis sections.

        Args:
            state: Current workflow state containing part12_combined and
                experiment_report.

        Returns:
            Dict with protocol_report.
        """
        logger.info("Generating experimental protocol summary")

        part12_str = state.get("part12_combined") or ""
        experiment_report = state.get("experiment_report", "")
        protocol_response = await self._dispatch_chat(
            get_prompt(
                self.deep_genome_config.PROMPT_FILE,
                "user/gene_function_protocol",
                {
                    "analysis_sections": part12_str,
                    "protocol_sections": experiment_report,
                },
            )
        )

        protocol_content = ""
        if (
            protocol_response
            and "choices" in protocol_response
            and len(protocol_response["choices"]) > 0
            and "message" in protocol_response["choices"][0]
        ):
            protocol_content = protocol_response["choices"][0]["message"].get(
                "content", ""
            )

        return {"protocol_report": protocol_content}

    async def _run_report_discussion(self: Any, state: DeepGenomeState):
        """Generate report discussion section.

        This node generates the discussion section of the gene function
        report, interpreting results and providing insights.

        Args:
            state: Current workflow state containing gene_annotation and all
                report sections.

        Returns:
            Dict with discussion_report.
        """
        logger.info("Generating discussion")
        gene_id = state["gene_id"]
        species_code = state["species_code"]
        gene_annotation = state.get("gene_annotation", {})
        gene_string = gene_annotation.get("gene_string", "")

        use_analyst = state.get("config_params", {}).get(
            "use_analyst_agent", True
        )

        # Build content string
        part12_str = state.get("part12_combined") or ""
        if use_analyst:
            protocol_report = state.get("protocol_report", "")
            experiment_report = state.get("experiment_report", "")
            introduction_report = state.get("introduction_report", "")
            part0123_str = (
                f"# Deep Genome Analysis of {gene_id}\n\n"
                f"{introduction_report}\n\n{part12_str}\n\n"
                f"## Recommended experiments\n\n{protocol_report}\n\n"
                f"{experiment_report}\n\n"
            )
            content = part0123_str
        else:
            content = part12_str

        discussion_response = await self._dispatch_chat(
            get_prompt(
                self.deep_genome_config.PROMPT_FILE,
                "user/gene_function_discussion",
                {
                    "gene_string": gene_string,
                    "species_string": SPECIES_CODE_MAP[species_code],
                    "content": content,
                },
            )
        )

        discussion_content = ""
        if (
            discussion_response
            and "choices" in discussion_response
            and len(discussion_response["choices"]) > 0
            and "message" in discussion_response["choices"][0]
        ):
            discussion_content = discussion_response["choices"][0][
                "message"
            ].get("content", "")

        return {"discussion_report": discussion_content}

    async def _run_report_summary(self: Any, state: DeepGenomeState):
        """Generate report conclusion and future outlook section.

        This node generates the summary and conclusion section of the gene
        function report, synthesizing findings and suggesting future research
        directions.

        Args:
            state: Current workflow state containing gene_annotation and all
                report sections.

        Returns:
            Dict with summary_report.
        """
        logger.info("Generating conclusion and future outlook")
        summary_response = await self._dispatch_chat(
            get_prompt(
                self.deep_genome_config.PROMPT_FILE,
                "user/gene_function_summary",
                {
                    "gene_string": _state_gene_string(state),
                    "species_string": SPECIES_CODE_MAP[state["species_code"]],
                    "content": self._summary_source_content(state),
                },
            )
        )

        return {"summary_report": message_content(summary_response)}

    def _summary_source_content(self: Any, state: DeepGenomeState) -> str:
        """Build the source report content for final summary generation.

        M11 cleanup: the M5-era ``brief_response`` prefix block is
        removed. brief_gene now writes ``introduction_report``
        directly to state via the mount IO projection, so this
        helper reads ``state["introduction_report"]`` as the
        introduction body (no LLM call here, no message_content
        unwrap of brief_response).
        """
        part12_str = state.get("part12_combined") or ""
        discussion_report = state.get("discussion_report", "")
        introduction_report = state.get("introduction_report", "")
        if not state.get("config_params", {}).get("use_analyst_agent", True):
            # Matches pre-M5 behavior: no intro on the analyst-off path.
            return (
                f"{part12_str}\n\n" f"## Discussion\n\n{discussion_report}\n\n"
            )

        return (
            f"# Deep Genome Analysis of {state['gene_id']}\n\n"
            f"{introduction_report}\n\n{part12_str}\n\n"
            "## Recommended experiments\n\n"
            f"{state.get('protocol_report', '')}\n\n"
            f"{state.get('experiment_report', '')}\n\n"
            f"## Discussion\n\n{discussion_report}\n\n"
        )

    async def _run_follow_up_node(self: Any, state: DeepGenomeState):
        """Generate follow-up research questions.

        This node analyzes the complete gene function report and generates
        suggested follow-up research questions using LLM.

        Args:
            state: Current workflow state containing all report sections.

        Returns:
            Dict with final_report and follow_up_questions list.
        """
        logger.info("Generating follow-up research questions")

        gene_id = state["gene_id"]
        part0145_str = _assemble_final_report(state)

        follow_up_response = await self._dispatch_chat(
            get_prompt(
                self.deep_genome_config.PROMPT_FILE,
                "system/follow_up_questions",
                {
                    "user_query": f"Analyze the gene {gene_id}",
                    "system_response": part0145_str,
                },
            )
        )

        follow_up_list = parse_follow_up_questions(
            message_content(follow_up_response)
        )
        report_dir = state["report_dir"] or ""
        results_path = Path(report_dir) / f"{state['gene_id']}_report.md"
        final_report = part0145_str + "\n## Follow up questions: \n"
        for follow_up in follow_up_list:
            final_report += follow_up
            final_report += "\n"
        with open(results_path, "w", encoding="utf-8") as fo:
            fo.write(final_report)
        self._persist_final_report(state.get("task_id"), final_report)
        return {
            "final_report": part0145_str,
            "follow_up_questions": follow_up_list,
        }

    @staticmethod
    def _persist_final_report(
        task_id: Optional[str], final_report: str
    ) -> None:
        """Write the assembled report to the umbrella task row, best-effort.

        DeepGenome runs in the background and returns only a submit
        handle, so the assembled markdown reaches a polling client only
        if it is persisted on the local task row here (the last report
        node). ``set_task_final_report`` is a targeted column write, so a
        later terminal ``update_task`` status flip leaves it intact. The
        write is best-effort: a registry hiccup (``sqlite3.Error`` for
        WAL / lock failures, ``OSError`` for a full disk) is logged and
        swallowed rather than failing the workflow's final node, since
        the report is already on disk and only the poll-surfacing is lost.

        Args:
            task_id: Umbrella task id minted by ``arun``; ``None`` skips
                the write (defensive guard for state built without it).
            final_report: Assembled report markdown to persist verbatim.
        """
        if not task_id:
            return
        try:
            TaskManager(resolve_tasks_db_path()).set_task_final_report(
                task_id, final_report
            )
        except (sqlite3.Error, OSError) as exc:
            logger.warning(
                "DeepGenome failed to persist final_report for %s: %s",
                task_id,
                exc,
            )
