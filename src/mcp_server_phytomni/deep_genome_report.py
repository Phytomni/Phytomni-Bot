# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Report synthesis nodes for the DeepGenome workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .chat_agents import phyto_chat
from .deep_genome_formatting import SPECIES_CODE_MAP
from .utils import (
    get_prompt,
    message_content,
    parse_follow_up_questions,
    parse_json_list_fragment,
)
from .workflow_mixins import WorkflowMixinBase

if TYPE_CHECKING:
    from .deep_genome_agents import DeepGenomeState
else:
    DeepGenomeState = dict[str, Any]


def _state_gene_string(state: "DeepGenomeState") -> str:
    """Return the display gene string from workflow state."""
    return state.get("gene_annotation", {}).get("gene_string", "")


class DeepGenomeReportMixin(WorkflowMixinBase):
    """Report synthesis and finalization nodes for DeepGenome."""

    async def _run_report_synthesizer(self: Any, state: DeepGenomeState):
        """Generate the report after all analysis branches finish."""
        # 🛡️ Barrier: 等待所有 analyst_node 完成后才执行汇总
        completed = state.get("analysis_completed_branches", 0)
        total_expected = len(state.get("analysis_tasks", []))

        # Test mode: skip synthesize_node and use mock data directly
        if state.get("skip_synthesize", False):
            print(
                "  [Test Mode] Skipping synthesize_node, using "
                "pre-prepared mock data"
            )
            return {"experiment_completed_branches": 1}

        if total_expected > 0 and completed < total_expected:
            print(
                "  [Barrier] Waiting for analysis completion: "
                f"{completed}/{total_expected}"
            )
            return {}

        print(
            "  [Barrier] All analysis completed "
            f"({completed}/{total_expected}), starting synthesis..."
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
        results_path = (
            f"{self.deep_genome_config.DEEPGENOME_OUT}/"
            f"{state['gene_id']}_results.md"
        )
        with open(results_path, "w", encoding="utf-8") as fo:
            fo.write(gene_results)
        # print(gene_results)
        return {
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
            return {}

        # Prevent duplicate execution
        if state.get("report_triggered", False):
            return {}

        print(
            "\n[Ultimate Convergence] Basic profile + Deep analysis merged! "
            "Designing recommended experiments..."
        )

        part12_str = self._part12_profile(state)
        experiment_response = await phyto_chat(
            user_query=self._experiment_prompt(state, part12_str),
            **self._chat_kwargs(),
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
                repo_id_dict={"44ad28b5-5c3b-4a02-8e8c-7fb4903424cb": 128},
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
        print("-> Generating experimental protocol summary...")

        part12_str = state.get("part12_combined") or ""
        experiment_report = state.get("experiment_report", "")

        protocol_response = await phyto_chat(
            user_query=get_prompt(
                self.deep_genome_config.PROMPT_FILE,
                "user/gene_function_protocol",
                {
                    "analysis_sections": part12_str,
                    "protocol_sections": experiment_report,
                },
            ),
            prompt_file=self.deep_genome_config.PROMPT_FILE,
            prompt_path=self.deep_genome_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            frequency_penalty=self.deep_genome_config.FREQUENCY_PENALTY,
            n=self.deep_genome_config.N,
            presence_penalty=self.deep_genome_config.PRESENCE_PENALTY,
            reasoning_effort=self.deep_genome_config.REASONING_EFFORT,
            response_format=self.deep_genome_config.RESPONSE_FORMAT,
            stream=self.deep_genome_config.STREAM,
            temperature=self.deep_genome_config.TEMPERATURE,
            top_p=self.deep_genome_config.TOP_P,
            user=self.deep_genome_config.USER,
            timeout=self.deep_genome_config.TIMEOUT,
            retriable_codes=self.deep_genome_config.RETRIABLE_CODES,
            max_retries=self.deep_genome_config.MAX_RETRIES,
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

    async def _run_report_introduction(self: Any, state: DeepGenomeState):
        """Generate report introduction section.

        This node generates the introduction section of the gene function
        report, with context based on gene annotation and analysis.

        Args:
            state: Current workflow state containing gene_annotation,
                part12_combined, etc.

        Returns:
            Dict with introduction_report, or empty dict if already triggered.
        """
        # Prevent duplicate execution
        if state.get("report_triggered", False):
            return {}

        print("-> Generating introduction...")

        species_code = state["species_code"]
        gene_annotation = state.get("gene_annotation", {})
        gene_string = gene_annotation.get("gene_string", "")

        use_analyst = state.get("config_params", {}).get(
            "use_analyst_agent", True
        )

        # 构建内容字符串
        part12_str = state.get("part12_combined") or ""
        if use_analyst:
            protocol_report = state.get("protocol_report", "")
            experiment_report = state.get("experiment_report", "")
            part123_str = (
                f"{part12_str}\n\n"
                f"## Recommended experiments\n\n{protocol_report}\n\n"
                f"{experiment_report}\n\n"
            )
            content = part123_str
        else:
            content = part12_str

        introduction_response = await phyto_chat(
            user_query=get_prompt(
                self.deep_genome_config.PROMPT_FILE,
                "user/gene_function_introduction",
                {
                    "gene_string": gene_string,
                    "species_string": SPECIES_CODE_MAP[species_code],
                    "content": content,
                },
            ),
            prompt_file=self.deep_genome_config.PROMPT_FILE,
            prompt_path=self.deep_genome_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            frequency_penalty=self.deep_genome_config.FREQUENCY_PENALTY,
            n=self.deep_genome_config.N,
            presence_penalty=self.deep_genome_config.PRESENCE_PENALTY,
            reasoning_effort=self.deep_genome_config.REASONING_EFFORT,
            response_format=self.deep_genome_config.RESPONSE_FORMAT,
            stream=self.deep_genome_config.STREAM,
            temperature=self.deep_genome_config.TEMPERATURE,
            top_p=self.deep_genome_config.TOP_P,
            user=self.deep_genome_config.USER,
            timeout=self.deep_genome_config.TIMEOUT,
            retriable_codes=self.deep_genome_config.RETRIABLE_CODES,
            max_retries=self.deep_genome_config.MAX_RETRIES,
        )

        introduction_content = ""
        if (
            introduction_response
            and "choices" in introduction_response
            and len(introduction_response["choices"]) > 0
            and "message" in introduction_response["choices"][0]
        ):
            introduction_content = introduction_response["choices"][0][
                "message"
            ].get("content", "")

        return {"introduction_report": introduction_content}

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
        print("-> Generating discussion...")

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

        discussion_response = await phyto_chat(
            user_query=get_prompt(
                self.deep_genome_config.PROMPT_FILE,
                "user/gene_function_discussion",
                {
                    "gene_string": gene_string,
                    "species_string": SPECIES_CODE_MAP[species_code],
                    "content": content,
                },
            ),
            prompt_file=self.deep_genome_config.PROMPT_FILE,
            prompt_path=self.deep_genome_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            frequency_penalty=self.deep_genome_config.FREQUENCY_PENALTY,
            n=self.deep_genome_config.N,
            presence_penalty=self.deep_genome_config.PRESENCE_PENALTY,
            reasoning_effort=self.deep_genome_config.REASONING_EFFORT,
            response_format=self.deep_genome_config.RESPONSE_FORMAT,
            stream=self.deep_genome_config.STREAM,
            temperature=self.deep_genome_config.TEMPERATURE,
            top_p=self.deep_genome_config.TOP_P,
            user=self.deep_genome_config.USER,
            timeout=self.deep_genome_config.TIMEOUT,
            retriable_codes=self.deep_genome_config.RETRIABLE_CODES,
            max_retries=self.deep_genome_config.MAX_RETRIES,
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
        print("-> Generating conclusion and future outlook...")

        summary_response = await phyto_chat(
            user_query=get_prompt(
                self.deep_genome_config.PROMPT_FILE,
                "user/gene_function_summary",
                {
                    "gene_string": _state_gene_string(state),
                    "species_string": SPECIES_CODE_MAP[state["species_code"]],
                    "content": self._summary_source_content(state),
                },
            ),
            **self._chat_kwargs(),
        )
        return {"summary_report": message_content(summary_response)}

    def _summary_source_content(self: Any, state: DeepGenomeState) -> str:
        """Build the source report content for final summary generation."""
        part12_str = state.get("part12_combined") or ""
        discussion_report = state.get("discussion_report", "")
        if not state.get("config_params", {}).get("use_analyst_agent", True):
            return f"{part12_str}\n\n## Discussion\n\n{discussion_report}\n\n"

        return (
            f"# Deep Genome Analysis of {state['gene_id']}\n\n"
            f"{state.get('introduction_report', '')}\n\n{part12_str}\n\n"
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
        print("-> Generating follow-up research questions...")

        gene_id = state["gene_id"]
        use_analyst = state.get("config_params", {}).get(
            "use_analyst_agent", True
        )

        # Assemble complete final report content
        part12_str = state.get("part12_combined") or ""
        introduction_report = state.get("introduction_report", "")
        discussion_report = state.get("discussion_report", "")
        summary_report = state.get("summary_report", "")
        experiment_report = state.get("experiment_report", "")
        protocol_report = state.get("protocol_report", "")

        if use_analyst:
            part0145_str = (
                f"# Deep Genome Analysis of {gene_id}\n\n"
                f"{introduction_report}\n\n"
                f"{part12_str}\n\n"
                f"## Recommended experiments\n\n"
                f"{protocol_report}\n\n"
                f"{experiment_report}\n\n"
                f"## Discussion\n\n"
                f"{discussion_report}\n\n"
                f"## Conclusion and Future Outlook\n\n"
                f"{summary_report}\n\n"
            )
        else:
            part0145_str = (
                f"# Deep Genome Analysis of {gene_id}\n\n"
                f"{part12_str}\n\n"
                f"## Discussion\n\n"
                f"{discussion_report}\n\n"
                f"## Conclusion and Future Outlook\n\n"
                f"{summary_report}\n\n"
            )

        # 生成 follow-up questions
        follow_up_response = await phyto_chat(
            user_query=get_prompt(
                self.deep_genome_config.PROMPT_FILE,
                "system/follow_up_questions",
                {
                    "user_query": f"Analyze the gene {gene_id}",
                    "system_response": part0145_str,
                },
            ),
            prompt_file=self.deep_genome_config.PROMPT_FILE,
            prompt_path=self.deep_genome_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            frequency_penalty=self.deep_genome_config.FREQUENCY_PENALTY,
            n=self.deep_genome_config.N,
            presence_penalty=self.deep_genome_config.PRESENCE_PENALTY,
            reasoning_effort=self.deep_genome_config.REASONING_EFFORT,
            response_format=self.deep_genome_config.RESPONSE_FORMAT,
            stream=self.deep_genome_config.STREAM,
            temperature=self.deep_genome_config.TEMPERATURE,
            top_p=self.deep_genome_config.TOP_P,
            user=self.deep_genome_config.USER,
            timeout=self.deep_genome_config.TIMEOUT,
            retriable_codes=self.deep_genome_config.RETRIABLE_CODES,
            max_retries=self.deep_genome_config.MAX_RETRIES,
        )

        follow_up_list = parse_follow_up_questions(
            message_content(follow_up_response)
        )
        return {
            "final_report": part0145_str,
            "follow_up_questions": follow_up_list,
        }
