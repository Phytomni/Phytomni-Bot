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
from typing import Any, Literal, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from ...common.prompts import get_prompt
from ...common.responses import message_content, parse_follow_up_questions
from ...config.defaults import BriefGeneConfig
from ...config.settings import SensitiveConfig, get_sensitive_config
from ...graphs.chat_adapters import build_chat_input, build_chat_kwargs_for
from ...mcp.progress_events import emit_progress
from ...runtime.langgraph_runner import (
    ainvoke_graph,
    ensure_checkpointer,
    make_async_router,
)
from ...runtime.locale import SupportedLocale
from ..knowledge.agent import KnowledgeAgent
from ..knowledge.retrieval_result import retrieval_unavailable_error
from ..shared.chat_subgraph import (
    make_chat_after_router,
    mount_chat_node,
)
from ..shared.intermediate_state import merge_intermediate_state
from ..shared.knowledge_subgraph import KnowledgeApp, build_knowledge_app
from ..shared.options import resolve_agent_locale
from ..shared.sql import sql_literal
from .analytical_sections import (
    _run_section_application_node,
    _run_section_cloning_node,
    _run_section_discovery_node,
    _run_section_functional_node,
)
from .graph_knowledge_subgraph import BriefGeneKnowledgeSubgraphMixin
from .homology import _run_fetch_homology_interactions_node
from .interactions import _empty_homology_interactions_result
from .introduction import _run_introduction_node
from .pipeline import (
    _alias_counts_delta,
    _annotation_strings_delta,
    _attach_metadata,
    _dedupe,
    _identity_response_rows,
    _partition_annotation_results,
    _split_symbols,
    run_bi_api,
)
from .render import _render_preamble_node
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
    "initial_brief_gene_state",
]

BRIEF_CONFIG = BriefGeneConfig()
_BRIEF_GENE_IDENTITY_CAUGHT: tuple[type[BaseException], ...] = (Exception,)


async def _render_preamble_async_node(
    state: BriefGeneAgentState,
) -> dict[str, Any]:
    """Run the pure render template through the async graph runner."""
    return _render_preamble_node(state)


def initial_brief_gene_state(
    user_query: str,
    locale: SupportedLocale | None = None,
) -> BriefGeneAgentState:
    """Build the fully seeded initial state for a BriefGene graph run.

    Shared by :meth:`BriefGeneAgent.arun` and the stdio progress seed
    builder so the two call sites stay byte-identical — a single source
    of truth prevents the two literals from drifting apart.

    Args:
        user_query: Gene identifier, symbol, or free-text query.

    Returns:
        A fully-seeded :class:`BriefGeneAgentState` dict with every
        key at its empty/zero default.
    """
    return cast(
        BriefGeneAgentState,
        {
            "user_query": user_query,
            "locale": resolve_agent_locale(locale),
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
            # Seeded empty so the TypedDict contract holds at ``arun`` entry.
            "gene_structure_string": "",
            **_empty_homology_interactions_result(),
            "cross_species_alias_count": 0,
            "cross_species_alias_species_count": 0,
            "section1_markdown": "",
            "section2_markdown": "",
            "section3_markdown": "",
            "section4_markdown": "",
            "introduction_report": "",
            # Each section node writes +1 via ``operator.add``.
            "gene_profile_completed_branches": 0,
            # Retrieve workers concat ``(task_index, doc_list)`` via
            # ``operator.add``.
            "retrieve_indexed_results": [],
            "retrieve_failed_indices": [],
            "retrieve_cancelled_indices": [],
            "annotation_failed_indices": [],
            # Retrieve workers append ``DegradedRecord`` via ``operator.add``.
            "literature_degraded": [],
        },
    )


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
        checkpointer: BaseCheckpointSaver | None = None,
        brief_config: BriefGeneConfig = BRIEF_CONFIG,
        sensitive_config: SensitiveConfig | None = None,
        knowledge_agent: KnowledgeAgent | None = None,
    ):
        self.brief_config = brief_config
        self.sensitive_config = sensitive_config or get_sensitive_config()
        self.ka = knowledge_agent or KnowledgeAgent(
            knowledge_config=brief_config,
            sensitive_config=self.sensitive_config,
        )
        self._knowledge_app: KnowledgeApp = build_knowledge_app(
            knowledge_config=self.brief_config,
            sensitive_config=self.sensitive_config,
        )
        self.checkpointer = ensure_checkpointer(checkpointer)
        self.app = self._build_graph()

    def _build_graph(self):
        """Compile the brief_gene preamble workflow.

        ``_wire_preamble_graph`` builds the rich preamble fan-out: a
        parallel annotation / homology fetch, four section LLM nodes
        joined on retrieve + homology, an introduction summary, and a
        pure-template render that writes ``final_response``. The
        ``retrieve`` site mounts the Send-dispatch knowledge subgraph
        triad via ``_register_retrieve_nodes`` / ``_retrieve_targets``;
        the trailing follow-up hop reuses the shared ``chat`` subgraph.
        """
        workflow = StateGraph(
            state_schema=BriefGeneState,
            input_schema=BriefGeneInput,
            output_schema=BriefGeneOutput,
        )
        self._wire_preamble_graph(workflow)
        return workflow.compile(checkpointer=self.checkpointer)

    def _wire_preamble_graph(self, workflow: StateGraph) -> None:
        """Register the preamble fan-out graph on ``workflow``.

        ``query_judge_node`` fans out to
        ``fetch_homology_interactions_node`` (always) and conditionally
        to ``fetch_annotation_node`` (gene found) or the retrieve triad
        (gene not found). ``retrieve`` runs after
        ``fetch_annotation_node`` because the per-symbol literature
        tasks read its ``gene_id_list``. The four ``section{1-4}_node``
        gate ONLY on ``retrieve_reduce_node`` and converge through an
        explicit multi-source edge into ``introduction_node``. LangGraph
        therefore waits for all four section results before the
        pure-template ``render_node`` writes ``final_response``.
        ``fetch_homology_interactions_node`` runs in parallel and
        commits its counts to state in an early superstep, well before
        the deeper retrieve reduce settles, so the sections read
        homology from state without a direct edge. A homology->section
        edge would make the fan-in fire once per superstep (shallow
        homology vs deep retrieve), double-running every section LLM and
        raising ``InvalidUpdateError`` on the no-reducer
        ``final_response`` channel under the follow-up tail. The
        trailing follow-up hop reuses the shared ``chat`` subgraph via
        prep + post nodes.

        Args:
            workflow: Uncompiled ``StateGraph`` to register nodes and
                edges on.
        """
        retrieve_in, retrieve_out = self._retrieve_targets()
        workflow.add_node("query_judge_node", self.query_judge_node)
        workflow.add_node("fetch_annotation_node", self.fetch_annotation_node)
        workflow.add_node(
            "fetch_homology_interactions_node",
            _run_fetch_homology_interactions_node,
        )
        self._register_retrieve_nodes(workflow)
        workflow.add_node(
            "section_discovery_node", _run_section_discovery_node
        )
        workflow.add_node("section_cloning_node", _run_section_cloning_node)
        workflow.add_node(
            "section_functional_node", _run_section_functional_node
        )
        workflow.add_node(
            "section_application_node", _run_section_application_node
        )
        workflow.add_node("introduction_node", _run_introduction_node)
        workflow.add_node("render_node", _render_preamble_async_node)
        workflow.add_node("follow_up_prep_node", self.follow_up_prep_node)
        workflow.add_node("follow_up_post_node", self.follow_up_post_node)
        mount_chat_node(workflow)

        workflow.add_edge(START, "query_judge_node")
        workflow.add_edge(
            "query_judge_node", "fetch_homology_interactions_node"
        )
        workflow.add_conditional_edges(
            "query_judge_node",
            make_async_router(self.route_after_judge),
            {
                "fetch_annotation_node": "fetch_annotation_node",
                "retrieve_node": retrieve_in,
            },
        )
        workflow.add_edge("fetch_annotation_node", retrieve_in)
        section_nodes = tuple(
            f"section_{role}_node"
            for role in ("discovery", "cloning", "functional", "application")
        )
        for section in section_nodes:
            workflow.add_edge(retrieve_out, section)
        workflow.add_edge(list(section_nodes), "introduction_node")
        workflow.add_edge("introduction_node", "render_node")
        workflow.add_conditional_edges(
            "render_node",
            make_async_router(self.route_after_generate),
            {
                "follow_up_node": "follow_up_prep_node",
                "__end__": END,
            },
        )
        workflow.add_edge("follow_up_prep_node", "chat")
        workflow.add_conditional_edges(
            "chat",
            make_async_router(make_chat_after_router()),
            {
                "follow_up_post_node": "follow_up_post_node",
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
        """Route after render based on the is_follow_up flag.

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
        try:
            query_rows = _identity_response_rows(
                await run_bi_api(
                    "SELECT * FROM id2multispecies "
                    f"WHERE query_id = {sql_literal(user_query)}",
                    timeout=self.brief_config.TIMEOUT,
                    retriable_codes=self.brief_config.RETRIABLE_CODES,
                    max_retries=self.brief_config.MAX_RETRIES,
                )
            )
        except _BRIEF_GENE_IDENTITY_CAUGHT:
            raise retrieval_unavailable_error() from None
        if not query_rows:
            return {"gene_found": False}

        row = query_rows[0]
        gene_id = str(row.get("gene_id", "")).strip()
        species_code = str(row.get("species_code", "")).strip()
        if not gene_id or not species_code:
            raise retrieval_unavailable_error()
        try:
            identity_responses = await asyncio.gather(
                run_bi_api(
                    "SELECT * FROM id2multispecies "
                    f"WHERE query_id = {sql_literal(gene_id)}",
                    timeout=self.brief_config.TIMEOUT,
                    retriable_codes=self.brief_config.RETRIABLE_CODES,
                    max_retries=self.brief_config.MAX_RETRIES,
                ),
                run_bi_api(
                    "SELECT * FROM species "
                    f"WHERE species_code = {sql_literal(species_code)}",
                    timeout=self.brief_config.TIMEOUT,
                    retriable_codes=self.brief_config.RETRIABLE_CODES,
                    max_retries=self.brief_config.MAX_RETRIES,
                ),
            )
            gene_id_rows = _identity_response_rows(identity_responses[0])
            species_rows = _identity_response_rows(identity_responses[1])
        except _BRIEF_GENE_IDENTITY_CAUGHT:
            raise retrieval_unavailable_error() from None
        gene_id_row = gene_id_rows[0] if gene_id_rows else {}
        species_row = species_rows[0] if species_rows else {}
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
            **_alias_counts_delta(identity_responses[0]),
        }

    async def fetch_annotation_node(self, state: BriefGeneAgentState):
        """Fetch gene annotation from BI database tables.

        Args:
            state: Current workflow state containing the resolved gene id.

        Returns:
            State updates containing symbols, coordinates, and formatted
            annotation strings.
        """
        emit_progress("annotating", 0, detail="fetching gene annotation")
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
                    timeout=self.brief_config.TIMEOUT,
                    retriable_codes=self.brief_config.RETRIABLE_CODES,
                    max_retries=self.brief_config.MAX_RETRIES,
                )
                for sql in annotation_sqls
            ],
            return_exceptions=True,
        )

        annotation_rows, failed_indices = _partition_annotation_results(
            annotation_responses
        )
        id_rows = annotation_rows[0]
        gene_symbols = _split_symbols(
            str(id_rows[0].get("symbol", "")) if id_rows else ""
        )
        gene_id_list = _dedupe(
            [state["user_query"], state["gene_id"], *gene_symbols]
        )

        structure_rows = annotation_rows[1]
        structure_row = structure_rows[0] if structure_rows else {}

        return {
            "gene_name_symbol_list": gene_symbols,
            "gene_id_list": gene_id_list,
            "gene_chr": str(structure_row.get("chromosome", "")),
            "gene_start": str(structure_row.get("start", "")),
            "gene_end": str(structure_row.get("end", "")),
            "gene_strand": str(structure_row.get("strand", "")),
            **_annotation_strings_delta(annotation_rows, structure_row),
            "annotation_failed_indices": failed_indices,
        }

    async def follow_up_prep_node(
        self, state: BriefGeneAgentState
    ) -> dict[str, Any]:
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
            locale=state.get("locale"),
        )
        chat_payload = build_chat_input(rendered_user_query, chat_kwargs)
        return {
            "chat_payload": chat_payload,
            "pending_post": "follow_up_post_node",
        }

    async def follow_up_post_node(
        self, state: BriefGeneAgentState
    ) -> dict[str, Any]:
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
        self,
        user_query: str,
        thread_id: str | None = None,
        locale: SupportedLocale | None = None,
    ) -> dict[str, Any]:
        """Execute the BriefGeneAgent workflow.

        Args:
            user_query: Gene identifier, symbol, or free-text query.
            thread_id: Optional LangGraph checkpoint thread id.

        Returns:
            Chat-completions-style final response payload with content,
            references, and follow-up questions.
        """
        initial_state = initial_brief_gene_state(user_query, locale=locale)
        final_state = await ainvoke_graph(
            self.app, initial_state, thread_id=thread_id
        )
        return merge_intermediate_state(final_state)
