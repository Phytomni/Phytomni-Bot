# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: lihu (lihu0628@qq.com)
#         maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""BriefGeneAgent LangGraph orchestration class.

Hosts the BriefGeneAgent class (graph construction, node methods,
arun entry point). Public IO schemas live in state.py; the public
brief_gene_function wrapper and backward-compat re-exports live in
agent.py; pipeline helpers live in pipeline.py.
"""

import asyncio
from typing import Any, Dict, Literal, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ...common.prompts import get_prompt
from ...common.responses import message_content, parse_follow_up_questions
from ...config.defaults import BriefGeneConfig
from ...config.settings import SensitiveConfig, get_sensitive_config
from ...graphs.chat_adapters import build_chat_input, build_chat_kwargs_for
from ...runtime.langgraph_runner import ainvoke_graph, ensure_checkpointer
from ..knowledge.agent import KnowledgeAgent
from ..shared.chat_subgraph import (
    make_chat_after_router,
    make_chat_node_wrapper,
)
from ..shared.intermediate_state import merge_intermediate_state
from ..shared.knowledge_subgraph import build_knowledge_app
from ..shared.sql import sql_literal
from .graph_knowledge_subgraph import BriefGeneKnowledgeSubgraphMixin
from .pipeline import (
    _alias_counts_delta,
    _annotation_strings_delta,
    _attach_metadata,
    _dedupe,
    _first_row,
    _safe_rows,
    _split_symbols,
    run_bi_api,
)
from .state import (
    BriefGeneAgentState,
    BriefGeneInput,
    BriefGeneOutput,
    BriefGeneState,
)

__all__ = [
    "BRIEF_CONFIG",
    "BriefGeneAgent",
    "BriefGeneAgentState",
]

BRIEF_CONFIG = BriefGeneConfig()


class BriefGeneAgent(BriefGeneKnowledgeSubgraphMixin):
    """LangGraph-based agent for brief gene function analysis.

    Attributes:
        brief_config: Public config for BI, retrieval, and chat defaults.
        sensitive_config: Sensitive config with model and BI credentials.
        ka: KnowledgeAgent used for literature retrieval.
        checkpointer: LangGraph checkpointer used by the compiled graph.
        app: Compiled LangGraph application.
    """

    def __init__(
        self,
        checkpointer: Optional[MemorySaver] = None,
        brief_config: BriefGeneConfig = BRIEF_CONFIG,
        sensitive_config: Optional[SensitiveConfig] = None,
        knowledge_agent: Optional[KnowledgeAgent] = None,
    ):
        self.brief_config = brief_config
        self.sensitive_config = sensitive_config or get_sensitive_config()
        self.ka = knowledge_agent or KnowledgeAgent(
            knowledge_config=brief_config,
            sensitive_config=self.sensitive_config,
        )
        self._knowledge_app: CompiledStateGraph = build_knowledge_app(
            knowledge_config=self.brief_config,
            sensitive_config=self.sensitive_config,
        )
        self.checkpointer = ensure_checkpointer(checkpointer)
        self.app = self._build_graph()

    def _build_graph(self):
        """Compile the brief_gene workflow.

        ``_wire_chat_subgraph`` splits ``generate_node`` and
        ``follow_up_node`` into prep + post pairs surrounding a shared
        ``chat`` subgraph mount so LangGraph's xray rendering can
        inline the compiled chat subgraph under the brief_gene render.
        The ``retrieve`` site mounts the Send-dispatch knowledge
        subgraph triad via ``_register_retrieve_nodes`` /
        ``_retrieve_targets``.
        """
        workflow = StateGraph(
            state_schema=BriefGeneState,
            input_schema=BriefGeneInput,
            output_schema=BriefGeneOutput,
        )
        self._wire_chat_subgraph(workflow)
        return workflow.compile(checkpointer=self.checkpointer)

    def _wire_chat_subgraph(self, workflow: StateGraph) -> None:
        """Register the prep + post + shared chat form on ``workflow``.

        Replaces both ``generate_node`` and ``follow_up_node`` with
        prep + post pairs surrounding a single shared ``chat`` node.
        The chat node is registered via
        :func:`~agents.shared.chat_subgraph.make_chat_node_wrapper` so
        LangGraph's ``xray`` rendering can inline the compiled chat
        subgraph in the brief_gene render.
        :func:`~agents.shared.chat_subgraph.make_chat_after_router`
        reads the ``pending_post`` sentinel each prep node stages to
        branch back to the correct post node after the chat call. The
        ``retrieve`` site mounts the Send-dispatch knowledge subgraph
        triad (delegated via ``_register_retrieve_nodes`` and
        ``_retrieve_targets``); the combined wire surfaces both
        ``chat:`` and ``retrieve_worker_node:`` subgraph blocks under
        xray.

        Args:
            workflow: Uncompiled ``StateGraph`` to register nodes and
                edges on.
        """
        retrieve_in, retrieve_out = self._retrieve_targets()
        workflow.add_node("query_judge_node", self.query_judge_node)
        workflow.add_node("fetch_annotation_node", self.fetch_annotation_node)
        self._register_retrieve_nodes(workflow)
        workflow.add_node("generate_prep_node", self.generate_prep_node)
        workflow.add_node("generate_post_node", self.generate_post_node)
        workflow.add_node("follow_up_prep_node", self.follow_up_prep_node)
        workflow.add_node("follow_up_post_node", self.follow_up_post_node)
        workflow.add_node(
            "chat",
            make_chat_node_wrapper(
                build_input_fn=lambda state: state["chat_payload"],
                extract_output_fn=lambda chat_output: (
                    chat_output.get("response") or {}
                ),
                response_key="chat_response",
            ),
        )

        workflow.add_edge(START, "query_judge_node")
        workflow.add_conditional_edges(
            "query_judge_node",
            self.route_after_judge,
            {
                "fetch_annotation_node": "fetch_annotation_node",
                "retrieve_node": retrieve_in,
            },
        )
        workflow.add_edge("fetch_annotation_node", retrieve_in)
        workflow.add_edge(retrieve_out, "generate_prep_node")
        workflow.add_edge("generate_prep_node", "chat")
        workflow.add_edge("follow_up_prep_node", "chat")
        workflow.add_conditional_edges(
            "chat",
            make_chat_after_router(),
            {
                "generate_post_node": "generate_post_node",
                "follow_up_post_node": "follow_up_post_node",
            },
        )
        workflow.add_conditional_edges(
            "generate_post_node",
            self.route_after_generate,
            {
                "follow_up_node": "follow_up_prep_node",
                "__end__": END,
            },
        )
        workflow.add_edge("follow_up_post_node", END)

    def route_after_judge(self, state: BriefGeneAgentState) -> str:
        """Route to annotation lookup only when BI found the gene.

        Args:
            state: Current workflow state after query judging.

        Returns:
            Next node name for annotation lookup or direct retrieval.
        """
        if state["gene_found"]:
            return "fetch_annotation_node"
        return "retrieve_node"

    def route_after_generate(
        self, state: BriefGeneState
    ) -> Literal["follow_up_node", "__end__"]:
        """Route after generate based on the is_follow_up flag.

        Mirrors KnowledgeAgent's ``route_after_generate``: parent
        graphs may set ``is_follow_up=False`` via ``BriefGeneInput``
        to skip the trailing follow-up-question LLM hop; direct
        callers leave the field unset and see legacy True default.

        Returns:
            "follow_up_node" if the state flag is True (default),
            otherwise "__end__".
        """
        if state.get("is_follow_up", True):
            return "follow_up_node"
        return "__end__"

    async def query_judge_node(self, state: BriefGeneAgentState):
        """Check whether the query is known to the BI gene ID table.

        Args:
            state: Current workflow state containing the user query.

        Returns:
            State updates containing gene resolution and species metadata,
            or ``gene_found=False`` when BI has no match.
        """
        user_query = state["user_query"]
        query_response = await run_bi_api(
            "SELECT * FROM id2multispecies "
            f"WHERE query_id = {sql_literal(user_query)}",
            bi_url=self.brief_config.BI_URL,
            bi_token=self.sensitive_config.BI_TOKEN.get_secret_value(),
            timeout=self.brief_config.TIMEOUT,
            retriable_codes=self.brief_config.RETRIABLE_CODES,
            max_retries=self.brief_config.MAX_RETRIES,
        )
        row = _first_row(query_response)
        if row is None:
            return {"gene_found": False}

        gene_id = str(row.get("gene_id", ""))
        species_code = str(row.get("species_code", ""))
        gene_id_info_response, species_response = await asyncio.gather(
            run_bi_api(
                "SELECT * FROM id2multispecies "
                f"WHERE query_id = {sql_literal(gene_id)}",
                bi_url=self.brief_config.BI_URL,
                bi_token=self.sensitive_config.BI_TOKEN.get_secret_value(),
                timeout=self.brief_config.TIMEOUT,
                retriable_codes=self.brief_config.RETRIABLE_CODES,
                max_retries=self.brief_config.MAX_RETRIES,
            ),
            run_bi_api(
                "SELECT * FROM species "
                f"WHERE species_code = {sql_literal(species_code)}",
                bi_url=self.brief_config.BI_URL,
                bi_token=self.sensitive_config.BI_TOKEN.get_secret_value(),
                timeout=self.brief_config.TIMEOUT,
                retriable_codes=self.brief_config.RETRIABLE_CODES,
                max_retries=self.brief_config.MAX_RETRIES,
            ),
        )
        gene_id_row = _first_row(gene_id_info_response) or {}
        species_row = _first_row(species_response) or {}
        species_latin_name = str(
            species_row.get("species_scientific_name", "")
        )
        species_english_name = str(species_row.get("species_name_eng", ""))
        species_all_name = (
            f"{species_english_name} ({species_latin_name}, {species_code})"
        )
        return {
            "gene_found": True,
            "gene_id": gene_id,
            "query_id_version": str(row.get("id_type", "")),
            "gene_id_version": str(gene_id_row.get("id_type", "")),
            "species_code": species_code,
            "species_latin_name": species_latin_name,
            "species_english_name": species_english_name,
            "species_all_name": species_all_name,
            **_alias_counts_delta(gene_id_info_response),
        }

    async def fetch_annotation_node(self, state: BriefGeneAgentState):
        """Fetch gene annotation from BI database tables.

        Args:
            state: Current workflow state containing the resolved gene id.

        Returns:
            State updates containing symbols, coordinates, and formatted
            annotation strings.
        """
        gene_id_literal = sql_literal(state["gene_id"])
        species_code_literal = sql_literal(state["species_code"])
        annotation_sqls = [
            f"SELECT * FROM id_table WHERE gene_id = {gene_id_literal}",
            "SELECT * FROM annotation_gene_structure_col "
            f"WHERE gene_id = {gene_id_literal} AND sequence_type = 'gene'",
            "SELECT * FROM annotation_gene_ontology "
            f"WHERE gene_id = {gene_id_literal} LIMIT 50",
            "SELECT * FROM annotation_gene_mapman "
            f"WHERE gene_id = {gene_id_literal}",
            "SELECT * FROM annotation_gene_interpro "
            f"WHERE gene_id = {gene_id_literal}",
            # ``annotation_gene_description`` is species-keyed: the same
            # gene_id can carry different descriptive text across
            # ortholog species, so we MUST filter by both gene_id and
            # species_code (matching deep_genome's
            # ``_cached_gene_annotation_lookup`` SQL semantics) to avoid
            # cross-species contamination. The other annotation tables
            # above are gene_id-unique by convention; only this one
            # needs the species disambiguation.
            "SELECT description FROM annotation_gene_description "
            f"WHERE gene_id = {gene_id_literal} "
            f"AND species_code = {species_code_literal}",
        ]
        annotation_responses = await asyncio.gather(
            *[
                run_bi_api(
                    sql,
                    bi_url=self.brief_config.BI_URL,
                    bi_token=self.sensitive_config.BI_TOKEN.get_secret_value(),
                    timeout=self.brief_config.TIMEOUT,
                    retriable_codes=self.brief_config.RETRIABLE_CODES,
                    max_retries=self.brief_config.MAX_RETRIES,
                )
                for sql in annotation_sqls
            ],
            return_exceptions=True,
        )

        id_rows = _safe_rows(annotation_responses, 0)
        gene_symbols = _split_symbols(
            str(id_rows[0].get("symbol", "")) if id_rows else ""
        )
        gene_id_list = _dedupe(
            [state["user_query"], state["gene_id"], *gene_symbols]
        )

        structure_rows = _safe_rows(annotation_responses, 1)
        structure_row = structure_rows[0] if structure_rows else {}

        return {
            "gene_name_symbol_list": gene_symbols,
            "gene_id_list": gene_id_list,
            "gene_chr": str(structure_row.get("chromosome", "")),
            "gene_start": str(structure_row.get("start", "")),
            "gene_end": str(structure_row.get("end", "")),
            "gene_strand": str(structure_row.get("strand", "")),
            **_annotation_strings_delta(annotation_responses, structure_row),
        }

    async def generate_prep_node(
        self, state: BriefGeneAgentState
    ) -> Dict[str, Any]:
        """Build the chat payload for the brief gene response generation.

        Mirrors the prompt-building half of ``generate_node``. The
        actual chat dispatch runs in the shared ``chat`` node mounted
        by ``_wire_chat_subgraph``; ``generate_post_node`` parses the
        response into the legacy ``final_response`` delta.

        Args:
            state: Current workflow state with annotations and
                retrieval text.

        Returns:
            State delta with a ``ChatInput`` payload under
            ``chat_payload`` and the ``pending_post`` sentinel for the
            after-chat router. ``with_follow_up=False`` is passed
            explicitly because brief_gene generates its own follow-up
            questions in the separate ``follow_up_node`` step; the
            chat subgraph router's ``follow_up_node`` branch would
            cascade an unwanted second follow-up generation on top of
            the main answer.
        """
        if state["gene_found"]:
            prompt_vars = {
                "user_query": state["user_query"],
                "query_id_type": state["query_id_version"],
                "gene_id": state["gene_id"],
                "gene_id_type": state["gene_id_version"],
                "species": state["species_code"],
                "species_latin_name": state["species_latin_name"],
                "species_english_name": state["species_english_name"],
                "gene_string": "|".join(state["gene_id_list"]),
                "chromosome": state["gene_chr"],
                "start": state["gene_start"],
                "end": state["gene_end"],
                "strand": state["gene_strand"],
                "go_terms": state["go_string"],
                "kegg_annotation": state["kegg_string"],
                "interpro_terms": state["interpro_string"],
                "retrieve_results": state["retrieve_context"],
            }
            chat_query = get_prompt(
                self.brief_config.PROMPT_FILE,
                "user/brief_gene_function",
                prompt_vars,
            )
        else:
            chat_query = get_prompt(
                self.brief_config.PROMPT_FILE,
                "user/brief_gene_function_nogeneid",
                {
                    "user_query": state["user_query"],
                    "retrieve_results": state["retrieve_context"],
                },
            )

        chat_kwargs = build_chat_kwargs_for(
            self.brief_config,
            self.sensitive_config,
            with_follow_up=False,
        )
        chat_payload = build_chat_input(chat_query, chat_kwargs)
        return {
            "chat_payload": chat_payload,
            "pending_post": "generate_post_node",
        }

    async def generate_post_node(
        self, state: BriefGeneAgentState
    ) -> Dict[str, Any]:
        """Parse the chat response into the brief gene final response.

        Mirrors the response-parsing half of ``generate_node`` but
        reads the chat response from ``state['chat_response']``
        instead of awaiting a fresh ``phyto_chat`` call. Preserves the
        ``phyto_response is None`` fallback that the legacy body uses
        so downstream ``_attach_metadata`` always sees a valid
        chat-completions-shaped dict.

        Args:
            state: Current workflow state. Reads ``chat_response``
                written by the shared chat node and ``retrieved_docs``
                staged by ``retrieve_reduce_node``.

        Returns:
            State delta with ``final_response``.
        """
        phyto_response = state.get("chat_response") or {
            "choices": [{"message": {}}]
        }
        return {
            "final_response": _attach_metadata(
                phyto_response, state["retrieved_docs"]
            )
        }

    async def follow_up_prep_node(
        self, state: BriefGeneAgentState
    ) -> Dict[str, Any]:
        """Build the chat payload for follow-up questions generation.

        Mirrors the prompt-building half of the legacy
        ``follow_up_node``: renders the
        ``system/follow_up_questions`` template against the prior
        ``final_response`` (the brief gene answer the user just
        received) so the LLM can suggest the next questions to ask.
        The actual chat dispatch runs in the shared ``chat`` node;
        ``follow_up_post_node`` parses the response back into the
        ``follow_up_questions`` list and the enriched ``final_response``.

        Args:
            state: Current workflow state. Reads ``user_query`` and
                ``final_response`` written by ``generate_post_node``.

        Returns:
            State delta with a ``ChatInput`` payload under
            ``chat_payload`` and the ``follow_up_post_node``
            ``pending_post`` sentinel. ``with_follow_up=False`` is
            passed explicitly because this site IS the follow-up
            generation; the chat subgraph router's ``follow_up_node``
            branch firing here would cascade an unwanted recursive
            follow-up-on-follow-up generation.
        """
        rendered_user_query = get_prompt(
            self.brief_config.PROMPT_FILE,
            "system/follow_up_questions",
            {
                "user_query": (
                    f"What is gene function of {state['user_query']}"
                ),
                "system_response": message_content(state["final_response"]),
            },
        )
        chat_kwargs = build_chat_kwargs_for(
            self.brief_config,
            self.sensitive_config,
            with_follow_up=False,
        )
        chat_payload = build_chat_input(rendered_user_query, chat_kwargs)
        return {
            "chat_payload": chat_payload,
            "pending_post": "follow_up_post_node",
        }

    async def follow_up_post_node(
        self, state: BriefGeneAgentState
    ) -> Dict[str, Any]:
        """Parse the chat response into follow-up questions + final_response.

        Mirrors the response-parsing half of the legacy
        ``follow_up_node`` but reads the chat response from
        ``state['chat_response']`` instead of awaiting a fresh
        ``phyto_chat`` call. Re-runs ``_attach_metadata`` so the
        ``final_response`` payload carries the freshly parsed
        ``follow_up_questions`` list alongside the document references
        from the retrieval step.

        Args:
            state: Current workflow state. Reads ``chat_response``
                written by the shared chat node, plus ``final_response``
                and ``retrieved_docs`` already staged earlier in the
                workflow.

        Returns:
            State delta with ``follow_up_questions`` and the enriched
            ``final_response``.
        """
        chat_response = state.get("chat_response") or {}
        follow_up_questions = parse_follow_up_questions(
            message_content(chat_response)
        )
        final_response = _attach_metadata(
            state["final_response"],
            state["retrieved_docs"],
            follow_up_questions,
        )
        return {
            "follow_up_questions": follow_up_questions,
            "final_response": final_response,
        }

    async def arun(
        self, user_query: str, thread_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute the BriefGeneAgent workflow.

        Args:
            user_query: Gene identifier, symbol, or free-text query.
            thread_id: Optional LangGraph checkpoint thread id.

        Returns:
            Chat-completions-style final response payload with content,
            references, and follow-up questions.
        """
        initial_state: BriefGeneAgentState = {
            "user_query": user_query,
            "is_follow_up": True,
            "gene_found": False,
            "gene_id": "",
            "query_id_version": "",
            "gene_id_version": "",
            "species_code": "",
            "species_latin_name": "",
            "species_english_name": "",
            "species_all_name": "",
            "gene_name_symbol_list": [],
            "gene_id_list": [],
            "gene_chr": "",
            "gene_start": "",
            "gene_end": "",
            "gene_strand": "",
            "go_string": "",
            "kegg_string": "",
            "interpro_string": "",
            "description_string": "",
            "retrieved_docs": [],
            "retrieve_context": "",
            "follow_up_questions": [],
            "final_response": {},
            # M6 — X3b A architecture preamble fan-out fields.
            # All seeded empty here so the TypedDict contract holds at
            # ``arun`` entry; nodes populate them during the workflow.
            "gene_structure_string": "",
            "orthologs_data": {"gene_list": []},
            "paralogs_data": {"gene_list": []},
            "interaction_data": {"gene_list": []},
            "ortholog_count": 0,
            "ortholog_species_count": 0,
            "paralog_count": 0,
            "interaction_count": 0,
            "cross_species_alias_count": 0,
            "cross_species_alias_species_count": 0,
            "section1_markdown": "",
            "section2_markdown": "",
            "section3_markdown": "",
            "section4_markdown": "",
            "introduction_report": "",
            # Barrier counter reducer (replaces M5-era
            # ``part1_completed_branches``); each section node writes
            # +1 via ``operator.add``.
            "gene_profile_completed_branches": 0,
            # Seed the Send fan-out reducer channel so the TypedDict
            # contract is satisfied at ``arun`` entry. The retrieve
            # workers concat per-worker ``(task_index, doc_list)``
            # tuples onto this list via ``operator.add``.
            "retrieve_indexed_results": [],
        }
        final_state = await ainvoke_graph(
            self.app, initial_state, thread_id=thread_id
        )
        return merge_intermediate_state(final_state)
