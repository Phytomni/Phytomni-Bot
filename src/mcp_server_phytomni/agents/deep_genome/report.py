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
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...common.prompts import get_prompt
from ...common.responses import (
    message_content,
    parse_follow_up_questions,
    parse_json_list_fragment,
)
from ...config.defaults import DeepGenomeConfig
from ...graphs.chat_adapters import build_chat_input, extract_chat_response
from ...runtime.workflow_mixins import WorkflowMixinBase
from ...storage.path_policy import RunIdentity
from ...storage.scratch import ScratchTarget, resolve_scratch_dir
from ..chat.service import _cached_chat_app, phyto_chat
from .formatting import SPECIES_CODE_MAP

if TYPE_CHECKING:
    from .agent import DeepGenomeState
else:
    DeepGenomeState = dict[str, Any]

logger = logging.getLogger(__name__)

DEEP_GENOME_CONFIG = DeepGenomeConfig()


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

    async def _dispatch_chat(
        self: Any, user_query: str
    ) -> dict[str, Any] | None:
        """Dispatch one chat call via either phyto_chat or the chat subgraph.

        Owns the chat-call seam shared by every report node body
        (experiment / protocol / discussion / summary / follow_up).
        When ``USE_CHAT_SUBGRAPH=False`` (the production default until
        the global flag flip), routes through the legacy
        ``phyto_chat`` helper. When True, invokes the cached chat
        compiled app with the shared ``chat_adapters`` IO mappers,
        keeping the response in the historical chat-completion
        envelope so callers stay agnostic to the dispatch route.

        Args:
            user_query: Prompt body to send to the chat backend.

        Returns:
            Chat completion dict matching the historical phyto_chat
            return shape (``{"choices": [...]}``).
        """
        chat_kwargs_bag = self._chat_kwargs()
        if self.deep_genome_config.USE_CHAT_SUBGRAPH:
            chat_output = await _cached_chat_app().ainvoke(
                build_chat_input(
                    user_query=user_query, chat_kwargs=chat_kwargs_bag
                )
            )
            return extract_chat_response(chat_output)
        return await phyto_chat(
            user_query=user_query,
            **chat_kwargs_bag,
        )

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
        gene_results = get_prompt(
            self.deep_genome_config.PROMPT_FILE,
            "template/gene_function_result",
            gene_results_data,
        )
        obj_replace_dict = {
            "tree_path": "![Tree Image]()",
            "tissue_path": "![Tissue Image]()",
            "cultivar_path": "![Cultivar Image]()",
            "treatment_path": "![Treatment Image]()",
            "mutant_path": "![Genotype Image]()",
            "umap_path": "![Single_cell Umap Image]()",
            "violin_path": "![Single_cell Violin Image]()",
            "haplotype_path": "![Haplotype Image]()",
            "fst_path": "![fst Image]()",
            "motif_path": "![Motif Image]()",
            "smep_path": "![SMEP Image]()",
            "smoc_path": "![SMOC Image]()",
            "promoter_path": "![Promoter Design]()",
            "protein_path": "![Protein Design]()",
        }
        for obj_key, replace_content in obj_replace_dict.items():
            try:
                if gene_results_data[obj_key] == "":
                    gene_results = gene_results.replace(replace_content, "")
            except KeyError:
                continue
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
            protocol_response = await self._agents.knowledge_agent.arun(
                user_query=experiment,
                repo_id_dict={
                    DEEP_GENOME_CONFIG.PROTOCOL_REPO_ID: (
                        DEEP_GENOME_CONFIG.PROTOCOL_PAGE_SIZE
                    )
                },
                is_generate=True,
                is_follow_up=False,
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
        return {
            "final_report": part0145_str,
            "follow_up_questions": follow_up_list,
        }
