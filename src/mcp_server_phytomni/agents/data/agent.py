# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""NL2SQL agent for natural language database queries.

Classes: DataAgent. ``DataAgentState`` is defined in :mod:`.state`
and re-exported here for back compatibility.
Functions: rewrite_nl2sql, retrieve_and_generate.
"""

import logging
from typing import Any, Dict, Literal, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from ...common.docs import format_retrieved_doc_fragment
from ...common.prompts import get_prompt
from ...config.defaults import DataConfig
from ...config.overrides import (
    CHAT_COMPLETION_CONFIG_FIELD_MAP,
    NL2SQL_CONFIG_FIELD_MAP,
    RETRY_CONFIG_FIELD_MAP,
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from ...config.settings import SensitiveConfig, get_sensitive_config
from ...graphs.chat_adapters import build_chat_input, build_chat_kwargs_for
from ...graphs.data_to_knowledge_adapters import (
    build_data_knowledge_input,
    extract_data_knowledge_response,
)
from ...runtime.agent_registry import (
    agent_fingerprint_values,
    get_cached_agent,
)
from ...runtime.langgraph_runner import ainvoke_graph, ensure_checkpointer
from ..knowledge.retrieval import retrieve
from ..shared.chat_subgraph import make_chat_node_wrapper
from ..shared.intermediate_state import merge_intermediate_state
from ..shared.knowledge_subgraph import (
    build_knowledge_app,
    make_knowledge_after_router,
    make_knowledge_node_wrapper,
)
from .nl2sql import (
    Nl2SqlRequest,
    _default_dialog_id,
    execute_nl2sql_request,
)
from .state import DataAgentState, DataInput, DataOutput, DataState

logger = logging.getLogger(__name__)

DATA_CONFIG = DataConfig()

DATA_CONFIG_FIELD_MAP = {
    "retrieve_url": "RETRIEVE_URL",
    "data_repo_id": "DATA_REPO_ID",
    "page_num": "PAGE_NUM",
    "page_size": "DATA_PAGE_SIZE",
    "filter_string": "FILTER_STRING",
    "scope": "SCOPE",
    "rerank_url": "RERANK_URL",
    "rerank_batch_size": "RERANK_BATCH_SIZE",
    "score_threshold": "SCORE_THRESHOLD",
    **CHAT_COMPLETION_CONFIG_FIELD_MAP,
    **RETRY_CONFIG_FIELD_MAP,
    **NL2SQL_CONFIG_FIELD_MAP,
}
DATA_SENSITIVE_FIELD_MAP = {
    "base_url": "BASE_URL",
    "model": "MODEL_ID",
}
DATA_SECRET_FIELD_MAP = {"api_key": "API_KEY"}


async def rewrite_nl2sql(
    user_query: str,
    is_rewrite: bool = True,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Compatibility wrapper around the LangGraph-based DataAgent.

    Args:
        user_query: Natural-language database question.
        is_rewrite: Whether to retrieve scenarios and rewrite before NL2SQL.
        **kwargs: Keyword-compatible config and sensitive overrides.

    Returns:
        Final DataAgent response dictionary from the LangGraph workflow.
    """
    active_dialog_id = kwargs.get("dialog_id") or _default_dialog_id()
    arguments = {**kwargs, "dialog_id": active_dialog_id}
    data_config = copy_config_with_overrides(
        DATA_CONFIG,
        arguments,
        DATA_CONFIG_FIELD_MAP,
    )
    sensitive_config = copy_sensitive_config_with_overrides(
        get_sensitive_config(),
        arguments,
        field_map=DATA_SENSITIVE_FIELD_MAP,
        secret_field_map=DATA_SECRET_FIELD_MAP,
    )
    agent = get_cached_agent(
        "DataAgent",
        lambda: DataAgent(
            data_config=data_config,
            sensitive_config=sensitive_config,
        ),
        agent_fingerprint_values(
            data_config=data_config,
            sensitive_config=sensitive_config,
        ),
    )
    return await agent.arun(
        user_query=user_query,
        is_rewrite=is_rewrite,
        thread_id=active_dialog_id,
    )


class DataAgent:
    """A LangGraph-based agent for database querying via natural language.

    This agent orchestrates a workflow that converts natural language queries
    into SQL queries against a database. It retrieves relevant database
    scenarios, rewrites the query using an LLM for better SQL generation,
    and executes the resulting query.

    The workflow graph consists of three main nodes:
        1. retrieve_node: Retrieves relevant database scenarios for context.
        2. rewrite_node: Rewrites the query using an LLM for SQL generation.
        3. search_node: Executes NL2SQL conversion and queries the database.

    Args:
        checkpointer: A LangGraph checkpointer for state persistence.
                      Defaults to a fresh MemorySaver instance.
        data_config: Configuration for data retrieval and NL2SQL.
                     Defaults to the global DATA_CONFIG instance.
        sensitive_config: Configuration for sensitive data (e.g., API keys).
                          Defaults to the cached ``get_sensitive_config()``
                          instance when ``None``.

    Attributes:
        data_config: The data configuration instance.
        sensitive_config: The sensitive configuration instance.
        checkpointer: The checkpointer for state persistence.
        app: The compiled LangGraph application.
    """

    def __init__(
        self,
        checkpointer: Optional[MemorySaver] = None,
        data_config=DATA_CONFIG,
        sensitive_config: Optional[SensitiveConfig] = None,
    ):
        """Initialize the DataAgent with configuration and build the graph."""
        self.data_config = data_config
        self.sensitive_config = sensitive_config or get_sensitive_config()
        self.checkpointer = ensure_checkpointer(checkpointer)
        self._knowledge_app: Optional[CompiledStateGraph]
        if self.data_config.USE_KNOWLEDGE_SUBGRAPH:
            self._knowledge_app = build_knowledge_app(
                knowledge_config=self.data_config,
                sensitive_config=self.sensitive_config,
            )
        else:
            self._knowledge_app = None
        self.app = self._build_graph()

    def _build_graph(self):
        """Build and compile the LangGraph StateGraph workflow.

        Splits the single rewrite site into ``rewrite_prep_node`` +
        ``rewrite_post_node`` around a shared ``chat`` node
        registered via ``make_chat_node_wrapper`` so xray expansion
        surfaces the chat subgraph in the data render.
        ``USE_KNOWLEDGE_SUBGRAPH`` independently splits the retrieve
        site into ``retrieve_prep_node`` + ``retrieve_post_node``
        around a per-instance compiled ``knowledge`` node. The chat
        post node reads ``chat_response``; the knowledge post node
        reads ``knowledge_response`` selected by a router on
        ``pending_post_knowledge``. The chat site is always mounted as
        the prep + post pair around the shared chat node.
        """
        workflow = StateGraph(
            state_schema=DataState,
            input_schema=DataInput,
            output_schema=DataOutput,
        )
        workflow.add_node("search_node", self.search_node)
        self._register_retrieve_nodes(workflow)
        retrieve_in, retrieve_out = self._retrieve_targets()

        workflow.add_node("rewrite_prep_node", self.rewrite_prep_node)
        workflow.add_node("rewrite_post_node", self.rewrite_post_node)
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
        workflow.add_conditional_edges(
            START,
            self.route_start,
            [retrieve_in, "search_node"],
        )
        workflow.add_edge(retrieve_out, "rewrite_prep_node")
        workflow.add_edge("rewrite_prep_node", "chat")
        workflow.add_edge("chat", "rewrite_post_node")
        workflow.add_edge("rewrite_post_node", "search_node")
        workflow.add_edge("search_node", END)

        return workflow.compile(checkpointer=self.checkpointer)

    def _retrieve_targets(self) -> tuple[str, str]:
        """Return (incoming, outgoing) node names for the retrieve site.

        The legacy single-node form keeps ``retrieve_node`` as both
        the incoming target (``START`` routing into retrieval) and
        the outgoing source (edge into the rewrite stage). The
        knowledge-subgraph form splits the site into
        ``retrieve_prep_node`` (incoming) and ``retrieve_post_node``
        (outgoing), with the shared ``knowledge`` node mounted
        between them. Returning the pair from one helper lets
        ``_build_graph`` substitute names without duplicating the
        conditional.
        """
        if self.data_config.USE_KNOWLEDGE_SUBGRAPH:
            return "retrieve_prep_node", "retrieve_post_node"
        return "retrieve_node", "retrieve_node"

    def _register_retrieve_nodes(self, workflow: StateGraph) -> None:
        """Register the retrieve node(s) on ``workflow``.

        Under ``USE_KNOWLEDGE_SUBGRAPH=False`` registers the legacy
        single ``retrieve_node``. Under ``=True`` registers the prep
        + post pair plus a shared ``knowledge`` node whose wrapper
        closes over the per-instance compiled ``self._knowledge_app``
        so ``find_subgraph_pregel`` discovers it at parent compile
        time and xray expands the knowledge block in the data
        render. The after-knowledge router is wired here as a
        one-branch ``conditional_edges`` for symmetry with the chat
        after-router; adding more knowledge sites later only needs
        another branch in the mapping dict.
        """
        if not self.data_config.USE_KNOWLEDGE_SUBGRAPH:
            workflow.add_node("retrieve_node", self.retrieve_node)
            return
        knowledge_app = self._knowledge_app
        if knowledge_app is None:
            raise RuntimeError(
                "unreachable: USE_KNOWLEDGE_SUBGRAPH is True but "
                "_knowledge_app was not built in __init__"
            )
        workflow.add_node("retrieve_prep_node", self.retrieve_prep_node)
        workflow.add_node("retrieve_post_node", self.retrieve_post_node)
        workflow.add_node(
            "knowledge",
            make_knowledge_node_wrapper(
                knowledge_app=knowledge_app,
                build_input_fn=lambda state: state["knowledge_payload"],
                extract_output_fn=lambda ko: ko,
                response_key="knowledge_response",
            ),
        )
        workflow.add_edge("retrieve_prep_node", "knowledge")
        workflow.add_conditional_edges(
            "knowledge",
            make_knowledge_after_router(),
            {
                "retrieve_post_node": "retrieve_post_node",
            },
        )

    def route_start(
        self, state: DataAgentState
    ) -> Literal["retrieve_node", "search_node"]:
        """Choose whether the workflow should rewrite before SQL search.

        Args:
            state: Current DataAgent workflow state.

        Returns:
            Name of the first graph node to execute.
        """
        if state["is_rewrite"]:
            return "retrieve_node"
        return "search_node"

    async def retrieve_node(self, state: DataAgentState):
        """Retrieve relevant database scenarios and construct a query prompt.

        This node searches the knowledge base for relevant database scenarios
        that can help the LLM better understand the query context. It formats
        the retrieved scenarios into a prompt template for the rewrite node.

        Args:
            state: The current workflow state containing user_query.

        Returns:
            A dictionary containing the retrieve_prompt key with the
            constructed prompt for the next node.
        """
        user_query = state["user_query"]
        retrieve_response = await retrieve(
            user_query=user_query,
            retrieve_url=self.data_config.RETRIEVE_URL,
            repo_id=self.data_config.DATA_REPO_ID,
            page_num=self.data_config.PAGE_NUM,
            page_size=self.data_config.DATA_PAGE_SIZE,
            filter_string=self.data_config.FILTER_STRING,
            scope=self.data_config.SCOPE,
            extra_repo_ids=None,
            rerank_url=self.data_config.RERANK_URL,
            rerank_batch_size=self.data_config.RERANK_BATCH_SIZE,
            score_threshold=self.data_config.SCORE_THRESHOLD,
            timeout=self.data_config.TIMEOUT,
            retriable_codes=self.data_config.RETRIABLE_CODES,
            max_retries=self.data_config.MAX_RETRIES,
        )

        retrieve_results = []
        total_length = 0
        for i, doc in enumerate(retrieve_response.get("doc_list", [])):
            fragment = format_retrieved_doc_fragment(doc, i, label="scenario")
            if total_length + len(fragment) <= DATA_CONFIG.MAX_TOKENS:
                retrieve_results.append(fragment)
                total_length += len(fragment)
            else:
                break

        retrieve_context = "\n\n".join(retrieve_results)
        retrieve_prompt = get_prompt(
            DATA_CONFIG.PROMPT_FILE,
            "user/database",
            {"scenario_prompts": retrieve_context, "user_query": user_query},
        )

        return {"retrieve_prompt": retrieve_prompt}

    async def retrieve_prep_node(
        self, state: DataAgentState
    ) -> Dict[str, Any]:
        """Stage the knowledge input + post-knowledge sentinel.

        Mirrors the user-query-extraction half of the legacy
        ``retrieve_node`` but only emits the ``knowledge_payload``
        plus the ``pending_post_knowledge`` sentinel that routes the
        knowledge output back to ``retrieve_post_node``. No retrieve
        call happens here; the shared knowledge node runs between
        this prep and the post, then ``ainvoke`` of the compiled KA
        subgraph writes its return into ``knowledge_response`` for
        the post node to consume.

        Args:
            state: The current workflow state. Reads ``user_query``
                so the KnowledgeInput payload mirrors the legacy
                ``user_query=state['user_query']`` argument to
                ``retrieve``.

        Returns:
            A state delta with the ``KnowledgeInput`` dict under
            ``knowledge_payload`` and ``"retrieve_post_node"`` under
            ``pending_post_knowledge``.
        """
        return {
            "knowledge_payload": build_data_knowledge_input(
                state["user_query"],
                self.data_config.DATA_REPO_ID,
                self.data_config.DATA_PAGE_SIZE,
            ),
            "pending_post_knowledge": "retrieve_post_node",
        }

    async def retrieve_post_node(
        self, state: DataAgentState
    ) -> Dict[str, Any]:
        """Format retrieved docs into the ``retrieve_prompt`` delta.

        Mirrors the post-processing half of the legacy
        ``retrieve_node``: reads the doc list from
        ``state['knowledge_response']`` (the KA subgraph's final
        state) instead of awaiting a fresh ``retrieve`` call, then
        runs the same fragment-formatting + token-budget truncation
        + prompt-template stitch the legacy node ran so the
        ``retrieve_prompt`` shape is bit-equivalent between the two
        graph forms.

        Args:
            state: The current workflow state. Reads ``user_query``
                (re-stitched into the prompt template) and
                ``knowledge_response`` (written by the shared
                knowledge node).

        Returns:
            A state delta with the ``retrieve_prompt`` key carrying
            the stitched user/database prompt the downstream rewrite
            stage consumes.
        """
        user_query = state["user_query"]
        docs = extract_data_knowledge_response(
            state.get("knowledge_response") or {}
        )

        retrieve_results = []
        total_length = 0
        for i, doc in enumerate(docs):
            fragment = format_retrieved_doc_fragment(doc, i, label="scenario")
            if total_length + len(fragment) <= DATA_CONFIG.MAX_TOKENS:
                retrieve_results.append(fragment)
                total_length += len(fragment)
            else:
                break

        retrieve_context = "\n\n".join(retrieve_results)
        retrieve_prompt = get_prompt(
            DATA_CONFIG.PROMPT_FILE,
            "user/database",
            {"scenario_prompts": retrieve_context, "user_query": user_query},
        )

        return {"retrieve_prompt": retrieve_prompt}

    async def rewrite_prep_node(self, state: DataAgentState) -> Dict[str, Any]:
        """Build the chat payload for the rewrite call.

        Mirrors the prompt + payload-building half of
        :meth:`rewrite_node`; the actual chat dispatch runs in the
        shared chat node, and :meth:`rewrite_post_node` converts the
        response into ``rewrite_query``. No ``pending_post`` sentinel
        is needed because DataAgent's single chat call site routes
        the after-chat edge unconditionally to ``rewrite_post_node``.

        Args:
            state: The current workflow state. Reads ``retrieve_prompt``.

        Returns:
            A state delta with the ``ChatInput`` dict under
            ``chat_payload``.
        """
        chat_kwargs = build_chat_kwargs_for(
            self.data_config, self.sensitive_config
        )
        chat_payload = build_chat_input(
            user_query=state["retrieve_prompt"],
            chat_kwargs=chat_kwargs,
        )
        return {"chat_payload": chat_payload}

    async def rewrite_post_node(self, state: DataAgentState) -> Dict[str, Any]:
        """Convert the chat response into the ``rewrite_query`` delta.

        Mirrors the response-validation + content-extraction half of
        :meth:`rewrite_node`, raising the same :class:`McpError` when
        the upstream response is missing the choices payload so the
        failure mode stays observable across both graph shapes.

        Args:
            state: The current workflow state. Reads the upstream
                ``chat_response`` written by the shared chat node.

        Returns:
            A state delta with the ``rewrite_query`` key extracted
            from ``chat_response['choices'][0]['message']['content']``.

        Raises:
            McpError: If the chat response is missing or has no choices.
        """
        phyto_response = state.get("chat_response") or {}
        if (
            not phyto_response
            or "choices" not in phyto_response
            or not phyto_response["choices"]
        ):
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=("Failed to get response from phyto_chat service"),
                )
            )
        rewrite_query = phyto_response["choices"][0]["message"]["content"]
        return {"rewrite_query": rewrite_query}

    async def search_node(self, state: DataAgentState):
        """Execute the NL2SQL query and return database results.

        This node converts the rewritten natural language query to SQL
        using the nl2sql service and executes it against the database.
        The response may include insights depending on configuration.

        Args:
            state: The current workflow state containing rewrite_query.

        Returns:
            A dictionary containing the final_response key with the
            database query results.
        """
        if state["is_rewrite"]:
            query = state["rewrite_query"]
        else:
            query = state["user_query"]
        request = Nl2SqlRequest.from_kwargs(
            query,
            {
                "database_url": self.data_config.DATABASE_URL,
                "workspace_id": self.data_config.WORKSPACE_ID,
                "subject_id": self.data_config.SUBJECT_ID,
                "dialog_id": (
                    self.data_config.DIALOG_ID or _default_dialog_id()
                ),
                "need_insight": self.data_config.NEED_INSIGHT,
                "simplify_response": self.data_config.SIMPLIFY_RESPONSE,
                "timeout": self.data_config.TIMEOUT,
                "retriable_codes": self.data_config.RETRIABLE_CODES,
                "max_retries": self.data_config.MAX_RETRIES,
            },
        )
        result = await execute_nl2sql_request(request)
        if result is None:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message="No response received from SQL database",
                )
            )
        logger.debug("nl2sql response: %s", result)
        return {"final_response": result}

    async def arun(
        self,
        user_query: str,
        is_rewrite: bool = True,
        thread_id: Optional[str] = None,
    ):
        """Execute the DataAgent workflow.

        This is the main entry point for invoking the agent. It initializes
        the state with the user's query, then runs the LangGraph workflow
        to retrieve scenarios, rewrite the query, and execute the database
        query.

        Args:
            user_query: The user's natural language query.
            thread_id: Optional thread ID for state persistence. If not
                       provided, a new UUID will be generated.

        Returns:
            The final response dictionary containing database query results.
        """
        initial_state = {
            "user_query": user_query,
            "is_rewrite": is_rewrite,
            "retrieve_prompt": None,
            "rewrite_query": None,
            "final_response": None,
        }

        final_state = await ainvoke_graph(
            self.app, initial_state, thread_id=thread_id
        )

        return merge_intermediate_state(final_state)
